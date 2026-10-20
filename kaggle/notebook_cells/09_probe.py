# ============================== TOOL-CALL PROBE ==============================
# One cheap round-trip to confirm which mode the server + model produce:
# 'native' (server-parsed tool_calls) or 'text' (parsed from output text by the runner).
PROBE_SPEC = {"type": "function", "function": {"name": "echo",
              "description": "Echo the given text back.",
              "parameters": {"type": "object", "properties": {"text": {"type": "string"}},
                             "required": ["text"]}}}

try:
    probe_client = ChatClient(base_url=f"http://127.0.0.1:{PORT}/v1", model=SERVED_NAME,
                              sampling=SUB["sampling"])
    resp = probe_client.chat([{"role": "user", "content": 'Call the echo tool with text="probe".'}],
                             tools=[PROBE_SPEC])
    calls, mode = extract_tool_calls(resp.choices[0].message)
    print(f"Tool-call probe: mode={mode}, calls={[c['name'] for c in calls]}")
    if mode == "native":
        print("Native tool calling is active.")
    elif mode == "text":
        print("Text-protocol fallback active — tool calls are parsed from output text "
              "and results are returned as user messages.")
    else:
        print("WARNING: the model did not emit a tool call for the probe; inspect the response:")
        print((resp.choices[0].message.content or "")[:500])
except Exception as e:
    print("Probe failed:", e)
