# ============================== RUN EVALUATION ==============================
import json, os, time

EVAL_DIR = "/kaggle/working/eval_results"
os.makedirs(EVAL_DIR, exist_ok=True)
os.makedirs(WORK_ROOT, exist_ok=True)
os.makedirs(VERIFY_ROOT, exist_ok=True)

chat = ChatClient(base_url=f"http://127.0.0.1:{PORT}/v1", model=SERVED_NAME,
                  sampling=SUB["sampling"])
results = []

t_all = time.time()
for i, task in enumerate(TASKS):
    tid = task["instance_id"]
    print(f"\n{'=' * 70}\n[{i + 1}/{len(TASKS)}] {tid}  ({task['repo']} @ {task['base_commit'][:12]})\n{'=' * 70}")
    rec = {"instance_id": tid, "repo": task["repo"]}
    try:
        ws, install_log = setup_agent_workspace(task, REPO_CACHE, WORK_ROOT, pip_install=True)
        if install_log:
            print("pip install log tail:", install_log[-300:])
    except Exception as e:
        rec.update(resolved=False, classification="env_setup_failed", details=str(e))
        results.append(rec)
        print("workspace setup failed:", e)
        continue
    ctx = SwegemmaContext(ws, budget=BUDGET, index=CodeIndex(ws))
    try:
        run = run_agent_task(task, ctx, chat, SUB["system_prompt"], BUDGET, verbose=True)
    except Exception as e:
        rec.update(resolved=False, classification="agent_crashed",
                   details=f"{type(e).__name__}: {e}")
        results.append(rec)
        print("agent crashed:", e)
        _rmtree(ws)
        continue
    with open(os.path.join(EVAL_DIR, f"{tid}.patch"), "w", encoding="utf-8", newline="\n") as f:
        f.write(run["patch"])
    with open(os.path.join(EVAL_DIR, f"{tid}.trace.json"), "w", encoding="utf-8") as f:
        json.dump(run["trace"], f, indent=1)
    rec.update(tool_calls=run["tool_calls"], turns=run["turns"],
               tool_errors=run["tool_errors"],
               prompt_tokens=run["tokens"]["prompt"],
               completion_tokens=run["tokens"]["completion"],
               wall_seconds=run["wall_seconds"], patch_bytes=len(run["patch"]))
    if VERIFY:
        v = verify_task(task, run["patch"], ensure_repo_cache(task["repo"], REPO_CACHE),
                        VERIFY_ROOT, pip_install=True)
        rec.update(resolved=bool(v.get("resolved")),
                   classification=v.get("classification", "?"),
                   missing_required=v.get("missing_required"),
                   details=v.get("details"))
        if v.get("pytest_tail"):
            rec["pytest_tail"] = v["pytest_tail"]
    else:
        rec.update(resolved=None, classification="not_verified")
    results.append(rec)
    print(f"\n[{tid}] classification={rec['classification']} resolved={rec.get('resolved')}")
    if rec.get("details"):
        print("details:", str(rec.get("details"))[:500])
    _rmtree(ws)   # free disk between tasks

print(f"\nAll tasks finished in {(time.time() - t_all) / 60:.1f} minutes.")
