# Gemma 4 Developer Agent — Kaggle Evaluation Notebook

End-to-end evaluation of the competition agent on real SWE tasks, running the actual base model (`gemma-4-31b-it-qat-w4a16-ct`) via vLLM on Kaggle GPUs. It mirrors the two-phase `swegemma` lifecycle documented in `HARNESS_README.md`:

- **Phase 1 (agent)**: fresh git workspace per task, the 9 sandboxed tools (`run_command`, `read_file`, `edit_file`, `write_file`, `get_status`, `submit_patch`, `search_similar_code`, `get_code_neighbors`, `get_code_subgraph`), budget gating with continuation nudges, patch extraction via `git add -N . && git diff`.
- **Phase 2 (verification)**: fresh workspace at `base_commit` → 4-pass resilient patch application → protected test/config reset → `test_patch` application → hermetic `pytest` with JUnit XML scoring (`resolved` = exit code 0 AND every FAIL_TO_PASS / PASS_TO_PASS test passes).

## Setup

1. **GPU**: Settings → Accelerator → GPU. 2xT4 works (tensor parallel = 2); 4xL4 matches the competition hardware.
2. **Model access**: if `google/gemma-4-31b-it-qat-w4a16-ct` is gated on Hugging Face, add `HF_TOKEN` under Add-ons → Secrets. Or set `LOCAL_MODEL_DIR` / `KAGGLE_MODEL` in the config cell to an attached model copy.
3. **Submission files**: attach this repository as a Kaggle Dataset (it contains `agent.yaml`). If none is attached, an embedded snapshot of the current submission is written and used — regenerate it with `python kaggle/build_notebook.py` after editing prompts/configs.
4. **Tasks**: default is a slice of SWE-bench Lite (downloaded via `datasets`, needs internet). Point `TASKS_JSONL` at the competition's task file once published.
5. Run All. Loading the model into vLLM takes 10–25 min; each task then runs agent + verification (minutes).

## Notes

- The agent core here re-implements the documented harness protocol (tools, budgets, nudges, patch extraction, verification). If the competition publishes the real `swegemma` packages for participant-side use, prefer their CLI for exact-fidelity runs; use this notebook when they are unavailable.
- `python kaggle/selftest_local.py` (repo, CPU-only, no model) validates the agent core end-to-end with a scripted model.
