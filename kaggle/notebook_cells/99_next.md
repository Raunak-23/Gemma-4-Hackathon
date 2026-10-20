## Using the results

- **Per-task artifacts**: `/kaggle/working/eval_results/<instance_id>.patch` (the submitted diff) and `<instance_id>.trace.json` (every tool call + truncated result). Summary in `results.json` / `results.csv`.
- **Iterate on the prompt**: edit `prompts/system.md` or `configs/sampling.yaml` in the repo, re-upload the repo dataset (or regenerate this notebook with `python kaggle/build_notebook.py` to refresh the embedded snapshot), and re-run. Compare `resolved`, `tool_errors`, and the trace quality between runs. Keep the system prompt small — every rule in it should be observable in the traces as avoided failure modes.
- **Faster iteration**: set `VERIFY = False` to collect patches only, and pick easy `TASK_IDS` (e.g. small repos like `marshmallow`, `sqlfluff`) when tuning prompts.
- **Ship it**: run `python package_submission.py . submission.zip` in the repo and upload `submission.zip` to the competition. Validate first with `python run_smoke_test.py .`.
