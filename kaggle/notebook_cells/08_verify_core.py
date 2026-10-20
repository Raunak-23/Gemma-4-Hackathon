# @CORE
# ============================== WORKSPACE SETUP + VERIFICATION ==============================
# Container A: agent workspace at base_commit. Container B: pristine clone ->
# 4-pass patch apply -> protected-file reset -> test_patch -> hermetic pytest + JUnit scoring.
import os, re, shutil, subprocess, sys
import xml.etree.ElementTree as ET


def _sh(cmd, cwd=None, timeout=900):
    return _run_shell(cmd, cwd=cwd, timeout=timeout)


def _rmtree(path):
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)


def ensure_repo_cache(repo, cache_root):
    """Clone each upstream repo once; per-task workspaces clone locally from the cache."""
    d = os.path.join(cache_root, repo.replace("/", "__"))
    if not os.path.isdir(os.path.join(d, ".git")):
        os.makedirs(cache_root, exist_ok=True)
        cp = _sh(f'git clone --quiet https://github.com/{repo}.git "{d}"', timeout=3600)
        if cp.returncode != 0 or not os.path.isdir(os.path.join(d, ".git")):
            raise RuntimeError(f"git clone failed for {repo}: {(cp.stderr or cp.stdout)[-600:]}")
    return d


def _local_clone(source_dir, dest_dir, base_commit):
    if os.path.exists(dest_dir):
        _rmtree(dest_dir)
    cp = _sh(f'git clone --quiet --no-hardlinks "{source_dir}" "{dest_dir}"', timeout=1800)
    if cp.returncode != 0:
        raise RuntimeError(f"local clone failed: {(cp.stderr or '')[-600:]}")
    cp = _sh(f'git checkout --quiet --force {base_commit}', cwd=dest_dir, timeout=600)
    if cp.returncode != 0:
        raise RuntimeError(f"checkout {base_commit} failed: {(cp.stderr or '')[-600:]}")
    _sh("git clean -fdx -q", cwd=dest_dir)


def setup_agent_workspace(task, cache_root, ws_root, pip_install=True):
    cache = ensure_repo_cache(task["repo"], cache_root)
    ws = os.path.join(ws_root, task["instance_id"])
    os.makedirs(ws_root, exist_ok=True)
    _local_clone(cache, ws, task["base_commit"])
    _sh("git tag -f _swegemma_baseline", cwd=ws)
    exclude_dir = os.path.join(ws, ".git", "info")
    os.makedirs(exclude_dir, exist_ok=True)
    with open(os.path.join(exclude_dir, "exclude"), "w", encoding="utf-8", newline="\n") as f:
        f.write(GIT_EXCLUDE)
    install_log = ""
    if pip_install:
        cp = _sh("pip install -q -e .", cwd=ws, timeout=1800)
        install_log = ((cp.stdout or "") + (cp.stderr or "")).strip()[-300:]
    return ws, install_log


# ---------- patch parsing / protected paths ----------

def _patch_paths(patch):
    paths = []
    for m in re.finditer(r"^\+\+\+ b/(.+)$", patch or "", re.M):
        p = m.group(1).strip()
        if p and not p.startswith("/dev/null"):
            paths.append(p)
    return paths


def _is_protected(path):
    p = path.replace("\\", "/")
    base = p.rsplit("/", 1)[-1]
    if base in {"conftest.py", "pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg",
                ".pytest.ini", "sitecustomize.py", "usercustomize.py", "_swegemma_stubs.py"} \
            or base.endswith(".pth"):
        return True
    if re.match(r"test_.*\.py$", base) or re.match(r".*_test\.py$", base):
        return True
    return any(seg in ("tests", "test", "testing") for seg in p.split("/")[:-1])


def reset_protected(ws, patch):
    """Force-reset any protected test/config files the agent touched (anti-tampering)."""
    paths = [p for p in _patch_paths(patch) if _is_protected(p)]
    for p in paths:
        _sh(f'git checkout HEAD -- "{p}"', cwd=ws)
        _sh(f'git clean -f -- "{p}"', cwd=ws)
    return paths


def apply_patch_resilient(ws, patch_text):
    """4-pass application, mirroring verify_task: git apply (-p1 variants) then GNU patch."""
    if not (patch_text or "").strip():
        return False, "empty patch"
    pf = os.path.join(ws, "_agent_patch.diff")
    with open(pf, "w", encoding="utf-8", newline="\n") as f:
        f.write(patch_text if patch_text.endswith("\n") else patch_text + "\n")
    passes = [
        "git apply --unsafe-paths -p1",
        "git apply --unsafe-paths -p1 -3",
        "git apply --unsafe-paths -p1 --ignore-space-change --ignore-whitespace",
        "git apply --unsafe-paths -p1 --recount",
        "git apply --unsafe-paths -p0",
        "patch -p1 --batch --forward -l",
        "patch -p0 --batch --forward -l",
    ]
    last = ""
    for p in passes:
        cp = _sh(f'{p} "{pf}"', cwd=ws, timeout=300)
        if cp.returncode == 0:
            os.remove(pf)
            return True, p
        last = (cp.stderr or cp.stdout or "")[-800:]
    if os.path.exists(pf):
        os.remove(pf)
    return False, last


# ---------- JUnit scoring ----------

def _parse_junit(path):
    res = {"passed": [], "failed": [], "errors": [], "skipped": []}
    if not os.path.isfile(path):
        return res
    for tc in ET.parse(path).getroot().iter("testcase"):
        cn, nm = tc.get("classname") or "", tc.get("name") or ""
        tags = {ch.tag for ch in tc}
        if "failure" in tags:
            res["failed"].append((cn, nm))
        elif "error" in tags:
            res["errors"].append((cn, nm))
        elif "skipped" in tags:
            res["skipped"].append((cn, nm))
        else:
            res["passed"].append((cn, nm))
    return res


def _test_matches(req_id, classname, name):
    parts = req_id.split("::")
    func = parts[-1].split("[")[0]
    if (name or "").split("[")[0] != func:
        return False
    file_part = parts[0].replace("\\", "/")
    if file_part.endswith(".py"):
        file_part = file_part[:-3]
    file_part = file_part.replace("/", ".")
    cn = classname or ""
    if file_part and file_part not in cn and not cn.endswith(file_part):
        return False
    if len(parts) > 2 and parts[1] not in cn:
        return False
    return True


def run_tests_and_score(vw, task, pip_install=True):
    test_files = sorted(set(_patch_paths(task["test_patch"])))
    if not test_files:
        return {"resolved": False, "classification": "verify_error",
                "details": "no test files found in test_patch"}
    if pip_install:
        _sh("pip install -q -e .", cwd=vw, timeout=1800)
    junit_path = os.path.join(vw, "_junit.xml")
    py = sys.executable.replace(os.sep, "/")
    targets = " ".join(f'"{t}"' for t in test_files)
    # NOTE: the production harness runs `python3 -s -m pytest`; we omit -s so that
    # user-site installs work when iterating on a local machine. PYTHONSAFEPATH=1
    # still prevents cwd injection into sys.path.
    cmd = (f'PYTHONSAFEPATH=1 "{py}" -m pytest {targets} --junitxml="{junit_path}" '
           f'-p no:cacheprovider -o timeout=0 -o python_classes="Test* *Test" -q')
    try:
        cp = _sh(cmd, cwd=vw, timeout=2400)
    except subprocess.TimeoutExpired:
        return {"resolved": False, "classification": "verify_error",
                "details": "pytest timed out"}
    junit = _parse_junit(junit_path)
    required = list(task["fail_to_pass"]) + list(task["pass_to_pass"])
    missing = [rid for rid in required
               if not any(_test_matches(rid, cn, nm) for cn, nm in junit["passed"])]
    resolved = cp.returncode == 0 and bool(junit["passed"]) and not missing
    return {"resolved": resolved,
            "missing_required": missing,
            "junit_counts": {k: len(v) for k, v in junit.items()},
            "pytest_exit": cp.returncode,
            "pytest_tail": ((cp.stdout or "") + (cp.stderr or ""))[-1500:],
            "classification": "resolved" if resolved else "not_resolved"}


def verify_task(task, agent_patch, source_repo_dir, verify_root, pip_install=True):
    """Phase 2: fresh workspace at base_commit, apply agent patch, reset protected
    files, apply test_patch, run hermetic pytest, score against F2P + P2P."""
    if not (agent_patch or "").strip():
        return {"resolved": False, "classification": "no_patch",
                "details": "agent produced an empty patch"}
    vw = os.path.join(verify_root, task["instance_id"])
    try:
        os.makedirs(verify_root, exist_ok=True)
        _local_clone(source_repo_dir, vw, task["base_commit"])
    except Exception as e:
        return {"resolved": False, "classification": "env_setup_failed",
                "details": f"verify workspace setup failed: {e}"}
    try:
        ok, info = apply_patch_resilient(vw, agent_patch)
        if not ok:
            return {"resolved": False, "classification": "patch_apply_failed",
                    "details": f"4-pass patch application failed: {info}"}
        reset_protected(vw, agent_patch)
        tp_ok, tp_info = apply_patch_resilient(vw, task["test_patch"])
        if not tp_ok:
            return {"resolved": False, "classification": "test_patch_failed",
                    "details": f"test_patch failed to apply (upstream drift?): {tp_info}"}
        result = run_tests_and_score(vw, task, pip_install=pip_install)
    finally:
        _rmtree(vw)
    return result
