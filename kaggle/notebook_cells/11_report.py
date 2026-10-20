# ============================== REPORT ==============================
import json, os
from collections import Counter
import pandas as pd

EVAL_DIR = "/kaggle/working/eval_results"

df = pd.DataFrame(results)
cols = [c for c in ["instance_id", "classification", "resolved", "tool_calls", "turns",
                    "tool_errors", "prompt_tokens", "completion_tokens", "wall_seconds",
                    "patch_bytes"] if c in df.columns]
print(df[cols].to_string(index=False))

resolved = sum(1 for r in results if r.get("resolved"))
setup_failures = sum(1 for r in results if r.get("classification") == "env_setup_failed")
attempted = len(results) - setup_failures
print(f"\nResolution rate: {resolved}/{len(results)} overall | "
      f"{resolved}/{attempted} excluding setup failures "
      f"({(resolved / attempted * 100 if attempted else 0):.1f}%)")
print("Classifications:", dict(Counter(r.get("classification", "?") for r in results)))
print(f"\nArtifacts in {EVAL_DIR}: per-task .patch and .trace.json, results.json, results.csv")

with open(os.path.join(EVAL_DIR, "results.json"), "w", encoding="utf-8") as f:
    json.dump(results, f, indent=1, default=str)
df.to_csv(os.path.join(EVAL_DIR, "results.csv"), index=False)
