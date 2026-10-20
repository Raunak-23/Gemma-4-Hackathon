# ============================== SUBMISSION FILES ==============================
# The runner tests exactly what the competition harness compiles: agent.yaml with
# !include'd prompts/configs. Preference order:
#   1. SUBMISSION_DIR set in the config cell
#   2. any attached Kaggle Dataset containing agent.yaml + prompts/system.md
#   3. an embedded snapshot of this repo's submission (written to ./submission)
# Regenerate the snapshot with `python kaggle/build_notebook.py` after editing prompts.

EMBEDDED_SUBMISSION = json.loads(r"""@EMBED_SNAPSHOT@""")

import os, glob, json, yaml


def _find_submission(preferred):
    cands = []
    if preferred:
        cands.append(preferred)
    cands += sorted(glob.glob("/kaggle/input/**/agent.yaml", recursive=True))
    cands += [os.path.join(os.getcwd(), "submission", "agent.yaml")]
    for c in cands:
        d = os.path.dirname(os.path.abspath(c))
        if os.path.isfile(c) and os.path.isfile(os.path.join(d, "prompts", "system.md")):
            return d
    return None


def _make_include_loader(root):
    class Loader(yaml.SafeLoader):
        pass

    def include_constructor(loader, node):
        rel = loader.construct_scalar(node)
        if os.path.isabs(rel) or ".." in rel.replace("\\", "/").split("/"):
            raise ValueError(f"bad !include path: {rel}")
        full = os.path.abspath(os.path.join(root, rel))
        if not full.startswith(os.path.abspath(root)):
            raise ValueError("include escapes submission root")
        if rel.endswith((".md", ".txt")):
            return open(full, encoding="utf-8").read()
        if rel.endswith((".yaml", ".yml")):
            return yaml.load(open(full, encoding="utf-8"), Loader=_make_include_loader(root))
        raise ValueError(f"unsupported include type: {rel}")

    Loader.add_constructor("!include", include_constructor)
    return Loader


def load_submission(sub_dir):
    agent = yaml.load(open(os.path.join(sub_dir, "agent.yaml"), encoding="utf-8"),
                      Loader=_make_include_loader(sub_dir))
    budget = dict(BUDGET)
    ec = os.path.join(sub_dir, "eval_config.yaml")
    if os.path.isfile(ec):
        evaluation = (yaml.safe_load(open(ec, encoding="utf-8")) or {}).get("evaluation") or {}
        for k in budget:
            if k in evaluation:
                budget[k] = evaluation[k]
    return {
        "system_prompt": agent.get("instruction") or "",
        "sampling": agent.get("generate_content_config") or {},
        "budget": budget,
        "agent_name": agent.get("name"),
    }


SUBMISSION_DIR = _find_submission(SUBMISSION_DIR)
if SUBMISSION_DIR is None:
    SUBMISSION_DIR = os.path.join(os.getcwd(), "submission")
    for rel, content in EMBEDDED_SUBMISSION.items():
        p = os.path.join(SUBMISSION_DIR, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
    print("No attached submission found — wrote embedded snapshot to", SUBMISSION_DIR)

SUB = load_submission(SUBMISSION_DIR)
if USE_SUBMISSION_BUDGET:
    BUDGET.update(SUB["budget"])
print(f"Submission dir : {SUBMISSION_DIR}")
print(f"Agent          : {SUB['agent_name']}")
print(f"Sampling       : {SUB['sampling']}")
print(f"Per-task budget: {BUDGET}")
print("--- system prompt (first 300 chars) ---")
print(SUB["system_prompt"][:300])
