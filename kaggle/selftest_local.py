#!/usr/bin/env python3
"""CPU-only end-to-end self test of the notebook's agent core.

Executes the @CORE cells from kaggle/notebook_cells/ as plain Python, builds a toy
git repository with a real bug, drives the full agent loop with a scripted "model"
that emits real tool calls, and runs the verification pipeline (patch apply ->
protected-file reset -> test_patch -> pytest + JUnit scoring). Asserts the toy task
resolves. Also unit-checks the 3-tier edit matcher and text-protocol tool-call
parsing. No GPU, vLLM, openai, or network required.

Usage: python kaggle/selftest_local.py
"""

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CORE_CELLS = ["06_tools_core.py", "07_agent_core.py", "08_verify_core.py"]


class _Fn:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _ToolCall:
    def __init__(self, cid, name, arguments):
        self.id = cid
        self.function = _Fn(name, arguments)


class _Message:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _Choice:
    def __init__(self, message, finish_reason="tool_calls"):
        self.message = message
        self.finish_reason = finish_reason


class _Usage:
    def __init__(self, p, c):
        self.prompt_tokens = p
        self.completion_tokens = c


class _Response:
    def __init__(self, message, finish_reason="tool_calls", tokens=(40, 20)):
        self.choices = [_Choice(message, finish_reason)]
        self.usage = _Usage(*tokens)


class FakeChat:
    """Scripted model: pops queued responses that carry native-style tool calls."""

    def __init__(self, script):
        self.script = list(script)
        self.seen_messages = []

    def chat(self, messages, tools=None):
        self.seen_messages.append(json.dumps(messages, default=str)[:100000])
        if not self.script:
            raise AssertionError("script exhausted; the agent kept calling tools")
        return self.script.pop(0)


def _run_git(cwd, *args):
    cp = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if cp.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {cp.stderr}")
    return cp.stdout.strip()


def build_toy_repo(base_dir):
    pkg = os.path.join(base_dir, "toylib")
    tests = os.path.join(base_dir, "tests")
    os.makedirs(pkg, exist_ok=True)
    os.makedirs(tests, exist_ok=True)
    with open(os.path.join(pkg, "core.py"), "w", newline="\n") as f:
        f.write("def add(a, b):\n    return a - b\n\n\ndef mul(a, b):\n    return a * b\n")
    with open(os.path.join(base_dir, "conftest.py"), "w", newline="\n") as f:
        f.write("import os, sys\nsys.path.insert(0, os.path.dirname(__file__))\n")
    with open(os.path.join(tests, "test_core.py"), "w", newline="\n") as f:
        f.write("def test_mul():\n    from toylib.core import mul\n    assert mul(2, 3) == 6\n")
    _run_git(base_dir, "init", "-q")
    _run_git(base_dir, "config", "user.name", "selftest")
    _run_git(base_dir, "config", "user.email", "selftest@local")
    _run_git(base_dir, "add", "-A")
    _run_git(base_dir, "commit", "-q", "-m", "baseline")
    return _run_git(base_dir, "rev-parse", "HEAD")


TOY_TEST_PATCH = """--- a/tests/test_core.py
+++ b/tests/test_core.py
@@ -1,3 +1,6 @@
 def test_mul():
     from toylib.core import mul
     assert mul(2, 3) == 6
+
+def test_add():
+    from toylib.core import add
+    assert add(2, 3) == 5
"""


def main():
    ns = {"__name__": "selftest_ns"}
    for cell in CORE_CELLS:
        path = os.path.join(HERE, "notebook_cells", cell)
        with open(path, encoding="utf-8") as f:
            src = f.read()
        assert src.lstrip().startswith("# @CORE"), f"{cell} is missing the @CORE marker"
        exec(compile(src, cell, "exec"), ns)
    print("[1] core cells executed (tools, client+runner, verification)")

    tmp = tempfile.mkdtemp(prefix="gemma4_selftest_")
    repo_dir = os.path.join(tmp, "toyrepo")
    os.makedirs(repo_dir)
    base_commit = build_toy_repo(repo_dir)

    task = {
        "instance_id": "toy__toylib-1",
        "repo": "toy/toylib",
        "base_commit": base_commit,
        "problem_statement": "toylib.core.add returns the wrong result for positive inputs.",
        "hints_text": "",
        "test_patch": TOY_TEST_PATCH,
        "fail_to_pass": ["tests/test_core.py::test_add"],
        "pass_to_pass": ["tests/test_core.py::test_mul"],
    }
    budget = dict(timeout_seconds=60, max_tool_calls=30, max_time_minutes=5, max_turns=20)

    # --- agent phase on a workspace clone (mirrors setup, without pip) ---
    ws = os.path.join(tmp, "ws")
    ns["_local_clone"](repo_dir, ws, base_commit)
    exclude_dir = os.path.join(ws, ".git", "info")
    os.makedirs(exclude_dir, exist_ok=True)
    with open(os.path.join(exclude_dir, "exclude"), "w", newline="\n") as f:
        f.write(ns["GIT_EXCLUDE"])

    py = sys.executable.replace(os.sep, "/")
    script = [
        _Response(_Message(tool_calls=[_ToolCall("c1", "search_similar_code", '{"query": "add", "k": 3}')])),
        _Response(_Message(tool_calls=[_ToolCall("c2", "read_file", '{"filepath": "toylib/core.py", "start_line": 1, "end_line": 20}')])),
        _Response(_Message(tool_calls=[_ToolCall("c3", "edit_file", json.dumps({
            "filepath": "toylib/core.py",
            "old_string": "def add(a, b):\n    return a - b",
            "new_string": "def add(a, b):\n    return a + b"}))])),
        _Response(_Message(tool_calls=[_ToolCall("c4", "run_command", json.dumps({
            "command": f'"{py}" -m pytest tests/test_core.py -q'}))])),
        _Response(_Message(tool_calls=[_ToolCall("c5", "submit_patch", "{}")])),
    ]
    ctx = ns["SwegemmaContext"](ws, budget=budget, index=ns["CodeIndex"](ws))
    run = ns["run_agent_task"](task, ctx, FakeChat(script),
                               "You are a coding agent. Fix the bug.", budget, verbose=True)
    print(f"[2] agent loop done: turns={run['turns']} tool_calls={run['tool_calls']} "
          f"errors={run['tool_errors']} patch={len(run['patch'])}B")
    assert run["patch_submitted"], "agent never submitted a patch"
    assert "+    return a + b" in run["patch"], f"patch missing the fix:\n{run['patch']}"
    assert "-    return a - b" in run["patch"], "patch missing the removal"

    # --- verification phase against a pristine clone ---
    verify_root = os.path.join(tmp, "verify")
    v = ns["verify_task"](task, run["patch"], repo_dir, verify_root, pip_install=False)
    print(f"[3] verification: classification={v.get('classification')} resolved={v.get('resolved')} "
          f"junit={v.get('junit_counts')}")
    if v.get("pytest_tail"):
        print("    pytest tail:", v["pytest_tail"][-300:])
    assert v.get("resolved") is True, f"toy task did not resolve: {v}"

    # --- unit checks: text-protocol parsing ---
    msg_text = _Message(content='I will check the budget now.\n<|tool_call|>[{"name": "get_status", "arguments": {}}]')
    calls, mode = ns["extract_tool_calls"](msg_text)
    assert mode == "text" and calls and calls[0]["name"] == "get_status", (mode, calls)

    # --- unit checks: 3-tier edit matcher (via execute() -> JSON) ---
    fpath = os.path.join(ws, "toylib", "core.py")
    flex = ns["SwegemmaContext"](ws, budget=budget, index=ns["CodeIndex"](ws))
    r = json.loads(flex.execute("edit_file", {
        "filepath": "toylib/core.py",
        "old_string": "def mul(a, b):\n        return a * b",
        "new_string": "def mul(a, b):\n        return a * b  # flexible"}))
    assert r["status"] == "ok" and r["strategy"] == "flexible", r
    with open(fpath, encoding="utf-8") as f:
        content = f.read()
    assert "return a * b  # flexible" in content and "    return a + b" in content, content
    r = json.loads(flex.execute("edit_file", {
        "filepath": "toylib/core.py",
        "old_string": "def add(a, b): return a + b",   # one-line; file has it across two lines
        "new_string": "def add(a, b):\n    return a + b"}))
    assert r["status"] == "ok" and r["strategy"] == "regex", r
    r = json.loads(flex.execute("read_file", {"filepath": "toylib/core.py"}))
    assert r["status"] == "ok" and "a + b" in r["content"], r

    print("\nSELFTEST PASSED: agent loop, tools, 3-tier edits, verification, and "
          "text-protocol parsing all work end-to-end.")


if __name__ == "__main__":
    main()
