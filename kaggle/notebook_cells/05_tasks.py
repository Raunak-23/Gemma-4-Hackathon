# ============================== TASKS ==============================
import glob, json


def _parse_list(v):
    if not v:
        return []
    if isinstance(v, list):
        return [str(x) for x in v]
    try:
        return [str(x) for x in json.loads(v)]
    except Exception:
        return []


def load_tasks():
    if TASK_SOURCE in ("swe_bench_lite", "swe_bench_verified"):
        from datasets import load_dataset
        name = {"swe_bench_lite": "princeton-nlp/SWE-bench_Lite",
                "swe_bench_verified": "princeton-nlp/SWE-bench_Verified"}[TASK_SOURCE]
        rows = list(load_dataset(name, split="test"))
    else:
        path = TASKS_JSONL or next(iter(glob.glob("/kaggle/input/**/tasks.jsonl", recursive=True)), None)
        assert path, "TASKS_JSONL is not set and no tasks.jsonl was found under /kaggle/input"
        rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    tasks = []
    for r in rows:
        tasks.append(dict(
            instance_id=r["instance_id"], repo=r["repo"], base_commit=r["base_commit"],
            problem_statement=r["problem_statement"], hints_text=r.get("hints_text") or "",
            test_patch=r.get("test_patch") or "",
            fail_to_pass=_parse_list(r.get("fail_to_pass")),
            pass_to_pass=_parse_list(r.get("pass_to_pass"))))
    if TASK_IDS:
        wanted = set(TASK_IDS)
        tasks = [t for t in tasks if t["instance_id"] in wanted]
    return tasks[:MAX_TASKS]


TASKS = load_tasks()
print(f"{len(TASKS)} task(s) loaded:")
for t in TASKS:
    print(f"  - {t['instance_id']}  ({t['repo']} @ {t['base_commit'][:12]})")
