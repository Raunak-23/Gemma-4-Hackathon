# @CORE
# ============================== AGENT CORE: code index + sandboxed tools ==============================
# Faithful re-implementation of swegemma.tools: budget gating, output caps, JSON results.
import ast, difflib, json, os, re, subprocess, time

EXCLUDE_DIRS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", "build", "dist",
                ".tox", "venv", ".venv", "node_modules"}
GIT_EXCLUDE = "__pycache__/\n*.pyc\n.pytest_cache/\n*.egg-info/\nbuild/\ndist/\n.coverage\n"


def _j(d):
    return json.dumps(d, ensure_ascii=False)


def _detect_shell():
    """Prefer bash (the notebook targets Linux/Kaggle); fall back to the platform
    shell so the CPU selftest can run on Windows/macOS machines without bash."""
    try:
        cp = subprocess.run(["bash", "-c", "echo __ok__"], capture_output=True,
                            text=True, timeout=15, errors="replace")
        if cp.returncode == 0 and "__ok__" in (cp.stdout or ""):
            return ["bash", "-c"]
    except Exception:
        pass
    return None


SHELL_CMD = _detect_shell()


def _run_shell(cmd, cwd=None, timeout=900):
    """Run a shell command; returns CompletedProcess. In the non-bash fallback,
    'VAR=value cmd' prefixes are converted to environment variables."""
    if SHELL_CMD is not None:
        return subprocess.run(SHELL_CMD + [cmd], cwd=cwd, capture_output=True,
                              text=True, errors="replace", timeout=timeout)
    env = dict(os.environ)
    m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(\S*)\s+(.*)$", cmd, re.S)
    if m:
        env[m.group(1)] = m.group(2)
        cmd = m.group(3)
    return subprocess.run(cmd, cwd=cwd, shell=True, capture_output=True,
                          text=True, errors="replace", timeout=timeout, env=env)


def _ok(**kw):
    return {"status": "ok", **kw}


def _err(error_type, message, **details):
    return {"status": "error", "error_type": error_type, "error_message": message, "details": details}


class CodeIndex:
    """Lightweight AST symbol index standing in for the harness's pre-computed graph/embeddings."""

    def __init__(self, root):
        self.root = root
        self.symbols = []   # {name, kind, file, line, snippet}
        self.py_files = []
        self._build()

    def _walk(self):
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
            for fn in sorted(filenames):
                if fn.endswith(".py") and not fn.endswith(".pyc"):
                    yield os.path.join(dirpath, fn)

    def _build(self):
        for path in self._walk():
            rel = os.path.relpath(path, self.root).replace("\\", "/")
            self.py_files.append(rel)
            try:
                src = open(path, encoding="utf-8", errors="replace").read()
                tree = ast.parse(src)
            except Exception:
                continue
            lines = src.splitlines()
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    self.symbols.append({
                        "name": node.name,
                        "kind": "class" if isinstance(node, ast.ClassDef) else "function",
                        "file": rel, "line": node.lineno,
                        "snippet": "\n".join(lines[node.lineno - 1: node.lineno - 1 + 12])})

    def resolve(self, node):
        """Resolve a short symbol name to matching index entries (exact -> suffix -> case-insensitive)."""
        n = (node or "").strip()
        if not n:
            return []
        short = n.rsplit(".", 1)[-1]
        syms = [s for s in self.symbols if s["name"] == short or s["name"] == n]
        if not syms:
            ln, lshort = n.lower(), short.lower()
            syms = [s for s in self.symbols
                    if s["name"].lower() == lshort or s["name"].lower().endswith("." + lshort)]
        return syms


class SwegemmaContext:
    """The 9 sandboxed tools bound to one task workspace, with budgets and JSON outputs."""

    def __init__(self, workspace_dir, budget, index=None):
        self.workspace = os.path.abspath(workspace_dir)
        self.budget = budget
        self.tool_calls_used = 0
        self.tool_errors = 0
        self.turns_used = 0
        self.start_time = time.time()
        self.patch_submitted = False
        self.submitted_patch = ""
        self.index = index if index is not None else CodeIndex(self.workspace)

    # ---------- budget plumbing ----------
    def remaining_seconds(self):
        return self.budget["max_time_minutes"] * 60 - (time.time() - self.start_time)

    def _remaining_calls(self):
        return self.budget["max_tool_calls"] - self.tool_calls_used

    def _gate(self):
        if self._remaining_calls() <= 0:
            return _err("BudgetExceeded",
                        "tool call budget exhausted; finalize and call submit_patch")
        if self.remaining_seconds() <= 0:
            return _err("BudgetExceeded",
                        "time budget exhausted; finalize and call submit_patch")
        return None

    # ---------- path resolution ----------
    def _resolve(self, filepath):
        p = (filepath or "").strip().replace("\\", "/")
        if p.startswith("/workspace/"):
            p = p[len("/workspace/"):]
        elif p.startswith("/"):
            p = p.lstrip("/")
        if not p:
            raise ValueError("filepath is empty")
        if ".." in p.split("/"):
            raise ValueError("path traversal ('..') is forbidden")
        return os.path.join(self.workspace, *p.split("/"))

    # ---------- execution tools ----------
    def run_command(self, command):
        timeout = min(int(self.budget.get("timeout_seconds", 300)),
                      max(5, int(self.remaining_seconds())))
        try:
            cp = _run_shell(command, cwd=self.workspace, timeout=timeout)
        except subprocess.TimeoutExpired:
            return _err("TimeoutExceeded", f"command exceeded {timeout}s")
        except FileNotFoundError:
            return _err("CommandError", "no shell is available in this environment")
        out = (cp.stdout or "")[:5000]
        errout = (cp.stderr or "")[:5000]
        if cp.returncode == 0:
            return _ok(stdout=out, stderr=errout, exit_code=0)
        return _err("CommandError", f"command exited with code {cp.returncode}",
                    stdout=out, stderr=errout, exit_code=cp.returncode)

    def get_status(self):
        return _ok(
            time_elapsed_seconds=round(time.time() - self.start_time, 1),
            time_remaining_seconds=round(max(0, self.remaining_seconds()), 1),
            tool_calls_used=self.tool_calls_used,
            tool_calls_limit=self.budget["max_tool_calls"],
            tool_calls_remaining=max(0, self._remaining_calls()),
            turns_used=self.turns_used,
            turns_limit=self.budget["max_turns"],
            patch_submitted=self.patch_submitted)

    def submit_patch(self):
        self.submitted_patch = self._extract_diff()
        self.patch_submitted = True
        return _ok(patch_size=len(self.submitted_patch),
                   files_changed=self.submitted_patch.count("diff --git"))

    def extract_fallback_patch(self):
        """Harness fallback: if the session ends without submit_patch, diff the tree anyway."""
        if not self.patch_submitted:
            self.submitted_patch = self._extract_diff()

    def _extract_diff(self):
        cp = _run_shell("git add -N . && git diff --binary _swegemma_baseline",
                        cwd=self.workspace)
        if cp.returncode != 0 or not (cp.stdout or "").strip():
            cp = _run_shell("git add -N . && git diff --binary HEAD", cwd=self.workspace)
        return cp.stdout or ""

    # ---------- workspace file tools ----------
    def read_file(self, filepath, start_line=None, end_line=None):
        path = self._resolve(filepath)
        if not os.path.isfile(path):
            return _err("ValidationError", f"file not found: {filepath}")
        lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
        total = len(lines)
        s = int(start_line) if start_line else 1
        e = int(end_line) if end_line else total
        s = max(1, min(s, max(1, total)))
        e = max(s, min(e, total))
        if e - s + 1 > 150:
            e = s + 149
        chunk = lines[s - 1:e]
        text = "\n".join(chunk)
        truncated = e < total
        if len(text) > 10000:
            cut = text.rfind("\n", 0, 10000)
            text = text[:cut] if cut > 0 else text[:10000]
            truncated = True
            e = s + text.count("\n")
        return _ok(filepath=(filepath or "").strip().strip("/"), content=text,
                   start_line=s, end_line=e, total_lines=total, is_truncated=truncated)

    def edit_file(self, filepath, old_string, new_string, allow_multiple=False):
        path = self._resolve(filepath)
        if not os.path.isfile(path):
            return _err("FileEditError", f"file not found: {filepath}")
        raw = open(path, encoding="utf-8", errors="replace").read()
        if not raw.strip():
            return _err("FileEditError", "file is empty")
        if not old_string:
            return _err("FileEditError", "old_string must not be empty")
        had_crlf = "\r\n" in raw
        content = raw.replace("\r\n", "\n")
        try:
            out, strategy, occurrences = _apply_replacement(
                content, old_string, new_string, bool(allow_multiple))
        except _EditError as exc:
            return _err("FileEditError", str(exc))
        if had_crlf:
            out = out.replace("\n", "\r\n")
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(out)
        diff = "\n".join(difflib.unified_diff(
            content.splitlines(), out.replace("\r\n", "\n").splitlines(),
            "a/" + (filepath or "").strip("/"), "b/" + (filepath or "").strip("/"), lineterm=""))
        return _ok(filepath=(filepath or "").strip("/"), occurrences=occurrences,
                   strategy=strategy, diff=diff[:5000], is_truncated=len(diff) > 5000)

    def write_file(self, filepath, content):
        path = self._resolve(filepath)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(content or "")
        return _ok(filepath=(filepath or "").strip("/"), size=len(content or ""))

    # ---------- code intelligence tools ----------
    def search_similar_code(self, query, k=3):
        q = (query or "").strip()
        if not q:
            return _err("ValidationError", "query must be a non-empty symbol name")
        scored = []
        for sym in self.index.symbols:
            name = sym["name"]
            lq, ln = q.lower(), name.lower()
            if name == q:
                score = 1.0
            elif ln == lq:
                score = 0.98
            elif name.startswith(q) or q in name:
                score = 0.85
            elif lq in sym["file"].lower():
                score = 0.7
            else:
                ratio = difflib.SequenceMatcher(None, lq, ln).ratio()
                score = ratio if ratio > 0.6 else 0.0
            if score > 0:
                scored.append((score, sym))
        scored.sort(key=lambda x: (-x[0], x[1]["file"], x[1]["line"]))
        try:
            k = max(1, min(10, int(k)))
        except Exception:
            k = 3
        results = [{"node_name": f'{s["file"]}:{s["line"]}::{s["name"]}', "file": s["file"],
                    "line": s["line"], "kind": s["kind"], "similarity": round(sc, 4),
                    "code": s["snippet"][:1500]} for sc, s in scored[:k]]
        return _ok(query=q, results=results, count=len(results))

    def get_code_neighbors(self, node, edge_type=None, max_neighbors=10):
        syms = self.index.resolve(node)
        if not syms:
            return _err("NodeNotFound", f"symbol '{node}' not found in the code index")
        try:
            cap = max(1, min(50, int(max_neighbors)))
        except Exception:
            cap = 10
        neighbors = [{"relation": "DEFINED_IN", "node": s["name"], "file": s["file"],
                      "line": s["line"]} for s in syms[:cap]]
        pat = re.compile(r"\b" + re.escape(syms[0]["name"]) + r"\b")
        calls = 0
        for rel in self.index.py_files:
            if calls >= cap:
                break
            path = os.path.join(self.root_or_ws(), rel)
            try:
                src_lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
            except Exception:
                continue
            for i, line in enumerate(src_lines, 1):
                stripped = line.strip()
                if pat.search(line) and not stripped.startswith(("def " + syms[0]["name"], "class " + syms[0]["name"])):
                    neighbors.append({"relation": "CALLS", "file": rel, "line": i,
                                     "code": stripped[:120]})
                    calls += 1
                    if calls >= cap:
                        break
        return _ok(node=node, edge_type=edge_type, neighbors=neighbors,
                   count=len(neighbors))

    def root_or_ws(self):
        return self.index.root if hasattr(self.index, "root") else self.workspace

    def get_code_subgraph(self, nodes):
        if not isinstance(nodes, list) or not nodes:
            return _err("ValidationError", "nodes must be a non-empty list of symbol names")
        names = [str(n) for n in nodes][:10]
        syms = []
        for n in names:
            syms.extend(self.index.resolve(n)[:3])
        edges = []
        for a in syms:
            for b in syms:
                if a is b or a["name"] == b["name"]:
                    continue
                try:
                    src = open(os.path.join(self.root_or_ws(), a["file"]),
                               encoding="utf-8", errors="replace").read().splitlines()
                except Exception:
                    continue
                region = "\n".join(src[a["line"] - 1:a["line"] + 200])
                if re.search(r"\b" + re.escape(b["name"]) + r"\b", region):
                    edges.append({"from": a["name"], "to": b["name"], "type": "CALLS",
                                  "file": a["file"]})
                    if len(edges) >= 20:
                        break
        return _ok(nodes=[s["name"] for s in syms], edges=edges,
                   node_count=len(syms), edge_count=len(edges))

    # ---------- dispatch ----------
    def execute(self, name, args):
        """Run one tool call: budget-gate, execute, serialize JSON, inject budget warning."""
        if not isinstance(args, dict):
            args = {}
        if name not in TOOL_NAMES:
            self.tool_errors += 1
            return _j(_err("ValidationError", f"unknown tool '{name}'"))
        gate = self._gate()
        if gate is not None:
            return _j(gate)
        if name in GATED_TOOLS:
            self.tool_calls_used += 1
        try:
            result = getattr(self, name)(**args)
        except TypeError as e:
            self.tool_errors += 1
            return _j(_err("ValidationError", f"bad arguments for {name}: {e}"))
        except Exception as e:
            self.tool_errors += 1
            return _j(_err("ToolError", f"{type(e).__name__}: {e}"))
        if result.get("status") == "error" and result.get("error_type") != "CommandError":
            self.tool_errors += 1
        if name in GATED_TOOLS and self.tool_calls_used >= 20 and 0 < self._remaining_calls() <= 10:
            result["budget_warning"] = (f"Only {self._remaining_calls()} tool call(s) remaining "
                                        f"({self.tool_calls_used}/{self.budget['max_tool_calls']}). "
                                        "Finalize your edits and call submit_patch soon.")
        return _j(result)


class _EditError(Exception):
    pass


def _apply_replacement(content, old, new, allow_multiple):
    """3-tier resilient matching (mirrors adk-eval-core apply_replacement).
    Returns (new_content, strategy, occurrences); raises _EditError on failure."""
    n = content.count(old)
    if n:
        if n > 1 and not allow_multiple:
            raise _EditError(f"old_string matches {n} locations; add surrounding context "
                             "lines or pass allow_multiple=true")
        return (content.replace(old, new) if allow_multiple else content.replace(old, new, 1),
                "exact", n)
    # Tier 2: line-trimmed flexible match with re-indentation
    old_lines = old.split("\n")
    c_lines = content.split("\n")
    o_stripped = [l.strip() for l in old_lines]
    hits = [i for i in range(len(c_lines) - len(o_stripped) + 1)
            if [l.strip() for l in c_lines[i:i + len(o_stripped)]] == o_stripped]
    if hits:
        if len(hits) > 1 and not allow_multiple:
            raise _EditError(f"old_string (flexible match) hits {len(hits)} locations; "
                             "add surrounding context lines")
        base = re.match(r"[ \t]*", c_lines[hits[0]]).group(0)
        oldf = re.match(r"[ \t]*", old_lines[0]).group(0)
        reb = []
        for l in new.split("\n"):
            reb.append(base + l[len(oldf):] if (l.strip() and l.startswith(oldf)) else l)
        out, i, applied = [], 0, 0
        while i <= len(c_lines) - len(o_stripped):
            if [l.strip() for l in c_lines[i:i + len(o_stripped)]] == o_stripped:
                out.extend(reb)
                i += len(o_stripped)
                applied += 1
            else:
                out.append(c_lines[i])
                i += 1
        out.extend(c_lines[i:])
        return "\n".join(out), "flexible", applied
    # Tier 3: whitespace-insensitive regex match over code delimiters
    toks = [t for t in re.split(r"([()\[\]{}:>=<])|\s+", old) if t]
    pat = re.compile(r"\s*".join(re.escape(t) for t in toks))
    matches = list(pat.finditer(content))
    if matches:
        if len(matches) > 1 and not allow_multiple:
            raise _EditError(f"old_string (regex match) hits {len(matches)} locations")
        if allow_multiple:
            return pat.sub(lambda _: new, content), "regex", len(matches)
        m = matches[0]
        return content[:m.start()] + new + content[m.end():], "regex", 1
    raise _EditError("old_string not found with exact, flexible, or regex matching")
