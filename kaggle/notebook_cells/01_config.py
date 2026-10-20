# ============================== CONFIGURATION ==============================
# Edit this cell, then Run All. Everything below is tunable without touching any other cell.

# ---- Model ----
HF_MODEL_ID = "google/gemma-4-31b-it-qat-w4a16-ct"   # HF repo id (competition alias)
LOCAL_MODEL_DIR = ""    # path to a local model dir (e.g. /kaggle/input/...) to skip the HF download
KAGGLE_MODEL = ""       # Kaggle Models slug for kagglehub, e.g. "google/gemma-4/transformers/..."
SERVED_NAME = "gemma-4-31b-it-qat-w4a16-ct"           # name the API is served under
MAX_MODEL_LEN = 32768
GPU_MEM_UTIL = 0.90

# ---- Submission (agent.yaml / prompts / configs) ----
SUBMISSION_DIR = ""     # e.g. /kaggle/input/<dataset>/gemma4 — auto-detected when empty

# ---- Tasks ----
TASK_SOURCE = "swe_bench_lite"   # "swe_bench_lite" | "swe_bench_verified" | "jsonl"
TASKS_JSONL = ""                 # path to a competition-format tasks.jsonl when TASK_SOURCE="jsonl"
TASK_IDS = []                    # restrict to these instance_ids (optional)
MAX_TASKS = 3                    # how many tasks to evaluate

# ---- Per-task agent budget ----
BUDGET = dict(timeout_seconds=300, max_tool_calls=100, max_time_minutes=25, max_turns=200)
USE_SUBMISSION_BUDGET = False    # True: the submission's eval_config.yaml overrides BUDGET

# ---- Runtime ----
PORT = 8000
VERIFY = True                    # False: collect patches only (faster prompt iteration)
REPO_CACHE = "/kaggle/working/repocache"    # local git object cache (cloned once per repo)
WORK_ROOT = "/kaggle/working/workspaces"    # per-task agent workspaces
VERIFY_ROOT = "/kaggle/working/verify"      # per-task verification workspaces

import os

def _gpu_info():
    try:
        import torch
        n = torch.cuda.device_count()
        names = [torch.cuda.get_device_name(i) for i in range(n)]
        total = sum(torch.cuda.get_device_properties(i).total_memory for i in range(n))
        return n, names, total
    except Exception:
        return 0, [], 0

NGPU, GPU_NAMES, GPU_TOTAL = _gpu_info()
TP_SIZE = max(1, NGPU) if NGPU else 1
print(f"GPUs: {NGPU} x {GPU_NAMES or ['none']} | tensor_parallel_size={TP_SIZE}")
if NGPU and GPU_TOTAL < 20 * 1024**3:
    print("WARNING: <20GB total VRAM — a 31B QAT w4a16 model (~17GB) may not fit.")
if not (os.environ.get("HF_TOKEN") or LOCAL_MODEL_DIR or KAGGLE_MODEL):
    print("NOTE: HF_TOKEN not set. If the model repo is gated, add it under Add-ons -> Secrets -> HF_TOKEN.")
