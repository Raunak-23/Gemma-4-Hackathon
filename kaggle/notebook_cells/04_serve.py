# ============================== vLLM SERVER ==============================
import os, subprocess, time, glob, requests


def resolve_model_path():
    if LOCAL_MODEL_DIR and os.path.isfile(os.path.join(LOCAL_MODEL_DIR, "config.json")):
        return LOCAL_MODEL_DIR
    # an attached dataset that contains the weights directly
    for cfg in sorted(glob.glob("/kaggle/input/**/config.json", recursive=True)):
        d = os.path.dirname(cfg)
        if "gemma" in d.lower() and glob.glob(os.path.join(d, "*.safetensors")):
            return d
    if KAGGLE_MODEL:
        import kagglehub
        return kagglehub.model_download(KAGGLE_MODEL)
    return HF_MODEL_ID


def start_server(model, tool_parser="gemma"):
    cmd = ["vllm", "serve", model,
           "--served-model-name", SERVED_NAME,
           "--max-model-len", str(MAX_MODEL_LEN),
           "--tensor-parallel-size", str(TP_SIZE),
           "--gpu-memory-utilization", str(GPU_MEM_UTIL),
           "--port", str(PORT),
           "--disable-log-requests"]
    if tool_parser:
        cmd += ["--enable-auto-tool-choice", "--tool-call-parser", tool_parser]
    logf = open("/kaggle/working/vllm.log", "w")
    return subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT)


def wait_ready(proc, timeout_s=2400):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        if proc.poll() is not None:
            return False
        try:
            if requests.get(f"http://127.0.0.1:{PORT}/health", timeout=2).status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(15)
    return False


def _log_tail(n=25):
    try:
        with open("/kaggle/working/vllm.log", errors="replace") as f:
            return "".join(f.readlines()[-n:])
    except Exception:
        return "(no log)"


subprocess.run(["bash", "-c", "pkill -f 'vllm serve' 2>/dev/null; sleep 2; true"])

MODEL_PATH = resolve_model_path()
print("Serving model from:", MODEL_PATH)
SERVER_PROC = start_server(MODEL_PATH, "gemma")
if not wait_ready(SERVER_PROC):
    print("server did not come up with the 'gemma' tool-call parser. Log tail:\n", _log_tail())
    if SERVER_PROC.poll() is None:
        SERVER_PROC.kill()
    print("retrying without tool-call flags (client falls back to text-protocol parsing)...")
    SERVER_PROC = start_server(MODEL_PATH, None)
    assert wait_ready(SERVER_PROC), f"vLLM never became healthy. Log tail:\n{_log_tail(60)}"
print("vLLM is healthy.")
subprocess.run(["bash", "-c", "nvidia-smi --query-gpu=memory.used,memory.total --format=csv 2>/dev/null || true"])
