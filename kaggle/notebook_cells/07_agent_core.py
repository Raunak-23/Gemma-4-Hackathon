# @CORE
# ============================== AGENT CORE: chat client + tool specs + runner ==============================
# Mirrors swegemma's agent_runner: task prompt, native/text tool-call extraction,
# continuation nudges, budget-aware loop, fallback patch extraction.
import json, os, re, time


def _spec(name, desc, params):
    return {"type": "function",
            "function": {"name": name, "description": desc, "parameters": params}}


TOOL_SPECS = [
    _spec("run_command",
          "Execute a shell command in the workspace (bash). 300s timeout; output truncated to 5000 chars.",
          {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}),
    _spec("read_file",
          "Read a slice of a file (1-indexed, inclusive). Max 150 lines / 10000 chars per call.",
          {"type": "object", "properties": {"filepath": {"type": "string"},
                                           "start_line": {"type": "integer"},
                                           "end_line": {"type": "integer"}}, "required": ["filepath"]}),
    _spec("edit_file",
          "Replace old_string with new_string in an existing file. Include 2-3 unchanged "
          "surrounding lines in old_string so it matches exactly once.",
          {"type": "object", "properties": {"filepath": {"type": "string"},
                                           "old_string": {"type": "string"},
                                           "new_string": {"type": "string"},
                                           "allow_multiple": {"type": "boolean"}},
           "required": ["filepath", "old_string", "new_string"]}),
    _spec("write_file", "Create or overwrite a file inside the workspace.",
          {"type": "object", "properties": {"filepath": {"type": "string"},
                                            "content": {"type": "string"}},
           "required": ["filepath", "content"]}),
    _spec("get_status", "Return remaining time, tool-call budget, and patch submission status.",
          {"type": "object", "properties": {}, "required": []}),
    _spec("submit_patch", "Submit the current workspace diff as the final patch. Ends the session.",
          {"type": "object", "properties": {}, "required": []}),
    _spec("search_similar_code",
          "Find functions/classes by symbol name. query must be an identifier "
          "(e.g. 'HTTPConnection'), not prose.",
          {"type": "object", "properties": {"query": {"type": "string"},
                                           "k": {"type": "integer", "default": 3}},
           "required": ["query"]}),
    _spec("get_code_neighbors", "List definitions and call sites related to a symbol.",
          {"type": "object", "properties": {"node": {"type": "string"},
                                            "edge_type": {"type": "string"},
                                            "max_neighbors": {"type": "integer", "default": 10}},
           "required": ["node"]}),
    _spec("get_code_subgraph", "Return references among a list of symbols.",
          {"type": "object", "properties": {"nodes": {"type": "array", "items": {"type": "string"}}},
           "required": ["nodes"]}),
]

TOOL_NAMES = {s["function"]["name"] for s in TOOL_SPECS}
GATED_TOOLS = {"run_command", "read_file", "edit_file", "write_file",
               "search_similar_code", "get_code_neighbors", "get_code_subgraph"}


class ChatClient:
    """OpenAI-compatible client for the local vLLM server, with param degradation retries."""

    def __init__(self, base_url, model, sampling=None, api_key="EMPTY", request_timeout=900):
        self.model = model
        self.sampling = sampling or {}
        from openai import OpenAI   # lazy import keeps this cell runnable on CPU machines
        self.client = OpenAI(base_url=base_url, api_key=api_key,
                             timeout=request_timeout, max_retries=0)

    def chat(self, messages, tools=None):
        s = self.sampling
        base = {"model": self.model, "messages": messages,
                "temperature": s.get("temperature", 0.0),
                "top_p": s.get("top_p", 1.0),
                "max_tokens": int(s.get("max_output_tokens", 16384))}
        extras = {"top_k": s.get("top_k")} if s.get("top_k") is not None else None
        degradations = [  # applied progressively on 400-style rejections
            lambda kw: {**kw, "extra_body": extras},
            lambda kw: kw,                                    # drop top_k
            lambda kw: {k: v for k, v in kw.items() if k not in ("tools", "tool_choice")},
            lambda kw: {**{k: v for k, v in kw.items() if k != "max_tokens"},
                        "max_completion_tokens": base["max_tokens"]},
        ]
        last = None
        for di, decorate in enumerate(degradations):
            kw = dict(base)
            if tools:
                kw["tools"] = tools
                kw["tool_choice"] = "auto"
            kw = decorate(kw)
            for attempt in range(6):
                try:
                    return self.client.chat.completions.create(**kw)
                except Exception as e:
                    last = e
                    msg = str(e)
                    if ("400" in msg or "Bad Request" in msg or "unexpected keyword" in msg) \
                            and di < len(degradations) - 1:
                        break   # move to the next degradation
                    if attempt == 5:
                        raise
                    time.sleep(min(60.0, 2.0 * (2 ** attempt)))
        raise last


def extract_tool_calls(message):
    """Return (calls, mode). calls: list of {id, name, arguments(dict)}.
    Native server-parsed tool_calls take precedence; otherwise parse tool-call JSON
    (e.g. <|tool_call|> tagged or fenced) from the raw content text."""
    calls = []
    for tc in (getattr(message, "tool_calls", None) or []):
        fn = getattr(tc, "function", None)
        raw = getattr(fn, "arguments", "{}") if fn else "{}"
        try:
            args = json.loads(raw or "{}")
        except Exception:
            args = {"_invalid_arguments": (raw or "")[:500]}
        if not isinstance(args, dict):
            args = {"_invalid_arguments": str(args)[:500]}
        calls.append({"id": getattr(tc, "id", None) or f"call_{len(calls)}",
                      "name": getattr(fn, "name", ""), "arguments": args})
    if calls:
        return calls, "native"
    content = getattr(message, "content", None) or ""
    if not isinstance(content, str):
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    decoder = json.JSONDecoder()
    for i, ch in enumerate(content):
        if ch not in "{[":
            continue
        try:
            obj, _ = decoder.raw_decode(content, i)
        except ValueError:
            continue
        for item in (obj if isinstance(obj, list) else [obj]):
            if not isinstance(item, dict):
                continue
            fn = item.get("function") or {}
            name = item.get("name") or fn.get("name")
            args = item.get("arguments", fn.get("arguments", {}))
            if not name:
                continue
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {"_invalid_arguments": args[:500]}
            if not isinstance(args, dict):
                args = {"_invalid_arguments": str(args)[:500]}
            calls.append({"id": f"call_{len(calls)}", "name": name, "arguments": args})
    return calls, ("text" if calls else "none")


# Continuation nudges — mirror agent_runner.py's nudge table verbatim.
NUDGE_UNCLOSED = ("Your previous response reached the token limit before the tool call finished "
                  "closing (<|tool_call|> was cut off). Do NOT repeat your prior reasoning in "
                  "thought—emit your next tool call immediately, and if calling edit_file or "
                  "write_file, split the change into smaller incremental edits.")
NUDGE_MAXTOKENS = ("Your previous response reached the token limit while thinking before a tool "
                   "call was completed. Do NOT repeat your analysis in thought—keep reasoning "
                   "under a few sentences and emit your next tool call immediately, or call "
                   "submit_patch when you have completed and verified your changes.")
NUDGE_CONTINUE = ("Please continue your work using the available tools, or call submit_patch "
                  "when you have completed and verified your changes.")


def render_system_prompt(system_prompt, task):
    out = system_prompt
    out = out.replace("{problem_description}", task["problem_statement"])
    if task.get("hints_text"):
        out = out.replace("{hints}", task["hints_text"])
    return out


def _workspace_layout(ws, max_entries=150):
    entries = []
    for dirpath, dirnames, filenames in os.walk(ws):
        dirnames[:] = [d for d in dirnames
                       if d not in (".git", "__pycache__", ".pytest_cache")
                       and not d.endswith(".egg-info")]
        for f in sorted(filenames):
            if f.endswith(".pyc"):
                continue
            rel = os.path.relpath(os.path.join(dirpath, f), ws).replace("\\", "/")
            entries.append("./" + rel)
            if len(entries) >= max_entries:
                return "\n".join(entries)
    return "\n".join(entries)


def build_task_prompt(task, budget, workspace_dir):
    """Mirror swegemma's build_agent_prompt: up to 7 structured sections."""
    secs = [f"You are evaluating a software engineering task for repository {task['repo']}.\n\n"
            f"Problem Statement:\n{task['problem_statement']}"]
    if task.get("hints_text"):
        secs.append(f"## Hints:\n{task['hints_text']}")
    secs.append("## Task Budget (Session terminates when any budget is exhausted)\n"
                f"- Time allowance: {budget['max_time_minutes']} minutes\n"
                f"- Tool calls allowance: {budget['max_tool_calls']} calls\n"
                f"- Max loop iterations: {budget['max_turns']} turns")
    secs.append("## Execution Environment Rules\n"
                f"- Single command timeout: {budget['timeout_seconds']} seconds (commands exceeding this fail without ending the session)\n"
                "- Command output limit: 5000 characters\n"
                "- File view limit: 150 lines per read_file call\n"
                "- File character limit: 10000 characters per read_file call\n"
                "- Environment is offline (no network/PyPI access). All repository and test "
                "dependencies are ALREADY pre-installed. Do NOT attempt to run pip install or "
                "download packages.")
    secs.append("## Instructions\n"
                "0. Work strictly under /workspace.\n"
                "1. Inspect existing code and conventions before editing.\n"
                "2. Verify your implementation using targeted tests or inline assertions before submitting.\n"
                "3. Call submit_patch once complete.\n"
                "4. Return a final text completion response.")
    secs.append("## Code Intelligence Tools\n"
                "This repository has pre-built code graph and embedding data. Use these tools "
                "for fast, targeted navigation:\n"
                "- search_similar_code(query): Find semantically similar functions/classes by keyword.\n"
                "- get_code_neighbors(node): Find callers, callees, and definitions related to a symbol.\n"
                "- get_code_subgraph(nodes): Get the induced subgraph for a set of symbols.")
    # Avoid flooding small-context models with a recursive repository listing.
    secs.append("## Workspace Layout\nRepository files are available under /workspace. "
                "Use targeted commands and read_file to inspect relevant files.")
    return "\n\n".join(secs)


def _compact_messages(messages, max_chars=60000, keep_recent=8):
    """Rough context management in the spirit of the harness's EventsCompactionConfig:
    elide old, oversized tool outputs once the estimated prompt gets large."""
    total = sum(len(str(m.get("content") or "")) for m in messages)
    if total <= max_chars:
        return messages
    idx = [i for i, m in enumerate(messages) if len(str(m.get("content") or "")) > 3000]
    for pos, i in enumerate(idx):
        if total <= max_chars or pos >= len(idx) - keep_recent:
            break
        total -= len(str(messages[i].get("content"))) - 45
        messages[i] = {**messages[i], "content": "[older tool output elided for context management]"}
    return messages


def run_agent_task(task, ctx, chat, system_prompt, budget, verbose=True):
    """Drive one task: model turns -> tool execution -> nudges -> patch extraction."""
    def log(*a):
        if verbose:
            print(*a, flush=True)

    messages = [{"role": "system", "content": render_system_prompt(system_prompt, task)},
                {"role": "user", "content": build_task_prompt(task, budget, ctx.workspace)}]
    trace, tokens = [], {"prompt": 0, "completion": 0}
    inspected_files = set()
    consecutive_nudges = 0
    while True:
        if ctx.turns_used >= budget["max_turns"]:
            log(f"[loop] turn budget exhausted ({budget['max_turns']})")
            break
        if ctx.remaining_seconds() <= 0:
            log("[loop] time budget exhausted")
            break
        _compact_messages(messages)
        resp = chat.chat(messages, tools=TOOL_SPECS)
        choice = resp.choices[0]
        msg = choice.message
        usage = getattr(resp, "usage", None)
        if usage:
            tokens["prompt"] += getattr(usage, "prompt_tokens", 0) or 0
            tokens["completion"] += getattr(usage, "completion_tokens", 0) or 0
        ctx.turns_used += 1
        calls, mode = extract_tool_calls(msg)
        if mode == "native":
            messages.append({"role": "assistant", "content": msg.content or "",
                             "tool_calls": [{"id": c["id"], "type": "function",
                                             "function": {"name": c["name"],
                                                          "arguments": json.dumps(c["arguments"])}}
                                            for c in calls]})
        else:
            messages.append({"role": "assistant", "content": msg.content or ""})
        if not calls:
            consecutive_nudges += 1
            if consecutive_nudges > 3:
                log("[loop] no usable tool calls 3 turns in a row — stopping")
                break
            content = msg.content or ""
            finish = (getattr(choice, "finish_reason", "") or "")
            if "<|tool_call" in content:
                nudge = NUDGE_UNCLOSED
            elif finish.lower() in ("length", "max_tokens"):
                nudge = NUDGE_MAXTOKENS
            else:
                nudge = NUDGE_CONTINUE
            log(f"[loop] no tool calls (finish={finish!r}) -> nudge {consecutive_nudges}/3")
            messages.append({"role": "user", "content": nudge})
            continue
        consecutive_nudges = 0
        text_mode_results = []
        for call in calls:
            log(f"[turn {ctx.turns_used}] {call['name']} {json.dumps(call['arguments'])[:160]}")
            result = ctx.execute(call["name"], call["arguments"])
            if call["name"] == "read_file":
                filepath = str(call["arguments"].get("filepath", "")).strip().strip("\"\'")
                if filepath in inspected_files:
                    result = ("Repeated read of " + filepath + ". This file was already inspected. "
                              "Use the earlier result; move on to a different file, a targeted edit, "
                              "a test, or submit_patch.")
                else:
                    inspected_files.add(filepath)
            trace.append({"turn": ctx.turns_used, "tool": call["name"],
                          "arguments": call["arguments"], "result": result[:2000]})
            if mode == "native":
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
            else:
                try:
                    text_mode_results.append({"tool": call["name"], "result": json.loads(result)})
                except Exception:
                    text_mode_results.append({"tool": call["name"], "result": result[:4000]})
        if text_mode_results:
            messages.append({"role": "user",
                             "content": "TOOL RESULTS:\n" + json.dumps(text_mode_results)[:12000]
                                        + "\nContinue with your next action, or call submit_patch."})
        if ctx.patch_submitted:
            log("[loop] patch submitted")
            break
    if not ctx.patch_submitted:
        ctx.extract_fallback_patch()
        log(f"[loop] session ended without submit_patch; fallback diff extracted "
            f"({len(ctx.submitted_patch)} bytes)")
    return {"patch": ctx.submitted_patch, "trace": trace, "turns": ctx.turns_used,
            "tool_calls": ctx.tool_calls_used, "tool_errors": ctx.tool_errors,
            "tokens": tokens, "patch_submitted": ctx.patch_submitted,
            "wall_seconds": round(time.time() - ctx.start_time, 1)}
