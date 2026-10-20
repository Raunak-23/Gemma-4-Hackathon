# Gemma 4 Developer Agent

Autonomous coding agent for the **Google – The Gemma 4 Developer Agent Competition** on Kaggle. Given a Python repository snapshot and a written issue (bug report or feature request), the agent localizes the defect, applies a minimal patch, verifies it with tests, and submits a unified git diff.

- **Base model** (fixed by the rules): `gemma-4-31b-it-qat-w4a16-ct`
- **Environment**: offline sandbox, 4x NVIDIA L4, 32,768-token context (vLLM)
- **Submission**: declarative YAML only, < 3 GiB unpacked, no Python entrypoints

---

## 1. Repository Layout

```text
gemma4/
├── agent.yaml                  # Root agent config (LlmAgent "swe_coder")
├── eval_config.yaml            # Per-task budgets requested from the harness
├── configs/sampling.yaml       # Generation parameters (via !include)
├── prompts/system.md           # System instructions (via !include)
├── skills/repo_navigation/     # ADK skill: SKILL.md + AST search script + notes
├── sample_submission/          # Official starter, kept as reference
├── package_submission.py       # Validate + build submission.zip
├── run_smoke_test.py           # Offline validation of the full submission contract
├── kaggle/
│   ├── gemma4_agent_eval.ipynb     # Kaggle notebook: real eval on GPU (see §4)
│   ├── build_notebook.py           # Regenerates the notebook from notebook_cells/
│   ├── notebook_cells/             # Per-cell notebook sources
│   └── selftest_local.py          # CPU-only end-to-end test of the agent core
├── data/tasks.jsonl            # Task schema reference (synthetic demo tasks)
└── HARNESS_README.md           # Official harness & competitor guide
```

## 2. Agent Design

**Single direct-tool agent** (`agent.yaml`): the root `LlmAgent` binds all 9 built-in tools (`run_command`, `read_file`, `edit_file`, `write_file`, `get_status`, `submit_patch`, `search_similar_code`, `get_code_neighbors`, `get_code_subgraph`) plus the `repo_navigation` skill. An earlier `code_analyzer` AgentTool variant was dropped: delegation duplicated the system prompt in a second context and lost fidelity on whitespace-sensitive edits, with no resolution gain to show for it.

**Compact system prompt** ([prompts/system.md](prompts/system.md), ~40 lines): the prompt deliberately stays small — long system prompts burn context and invite hallucinated rules. It encodes only the failure modes that actually cost tasks:

| Rule | Failure mode it prevents |
| :--- | :--- |
| Scratch files in `/tmp/` only, written via `run_command` | `write_file` resolves inside `/workspace`; leftover files leak into the extracted patch (`git add -N . && git diff`) |
| Never touch test files / `conftest.py` / `pytest.ini` | Grading resets them anyway; edits are wasted budget |
| `search_similar_code` with exact symbol names first | Prose queries miss the embedding index; blind graph/subgraph dumps flood the context |
| 30–50 line `read_file` windows around the located line | Whole-file reads truncate at 150 lines and waste calls |
| 2–3 anchor context lines in `edit_file` | Non-unique `old_string` matches fail without applying |
| `pytest -q -x`, pipe long output through `tail` | 5,000-char output truncation hides test results |
| Reproduce in `/tmp/repro.py` → fail → fix → pass → test → `submit_patch` | Submitting unverified changes or a polluted workspace |

The prompt intentionally contains **no `{problem_description}` / `{hints}` placeholders**: the harness populates `hints` in session state only when a task has hints, and the task text already arrives in the harness's user prompt, so templating the system instruction adds failure risk for zero information gain.

**Sampling** ([configs/sampling.yaml](configs/sampling.yaml)): greedy decoding (`temperature: 0.0`) for reliable, reproducible tool calls; `top_p: 0.95`, `top_k: 40` (inert at T=0, kept for the vLLM contract); `max_output_tokens: 16384`; `thinking_budget: 4096` with `include_thoughts: true` — per the harness note, `thinking_level` is omitted because it maps to OpenAI's `reasoning_effort` and is unreliable under vLLM + LoRA serving.

**Budgets** ([eval_config.yaml](eval_config.yaml)): 300 s per command, 100 tool calls, 60 min per task, 200 turns — tool-call budget and the 3-nudge stop rule bind long before the turn cap.

## 3. Validate & Package

```bash
python run_smoke_test.py .        # archive, schema, tools, sampling, skills, templates
python package_submission.py . submission.zip
```

`submission.zip` is what gets uploaded to Kaggle (it is gitignored — build it fresh).

## 4. Evaluation on Kaggle GPUs

No local GPU is needed or used. [kaggle/gemma4_agent_eval.ipynb](kaggle/gemma4_agent_eval.ipynb) runs the real thing on Kaggle:

1. Serves `gemma-4-31b-it-qat-w4a16-ct` via vLLM (auto tensor-parallel; 2xT4 or 4xL4).
2. Loads the submission exactly as the harness compiles it (`agent.yaml` + `!include`s) — auto-detects the repo attached as a Kaggle Dataset, else falls back to an embedded snapshot.
3. Runs the two-phase lifecycle per task: agent workspace at `base_commit` with the 9 tools, budget gating, and continuation nudges → fresh verification workspace with 4-pass patch application, anti-tampering reset, `test_patch`, and hermetic `pytest` + JUnit scoring (`resolved` = exit 0 AND all FAIL_TO_PASS / PASS_TO_PASS pass).
4. Default tasks: a slice of SWE-bench Lite (real repos, real commits, downloaded via `datasets`). Point `TASKS_JSONL` at the competition's task file once published.

```bash
python kaggle/selftest_local.py    # CPU-only: validates the agent core end-to-end
python kaggle/build_notebook.py    # regenerate the notebook after editing prompts
```

The self-test drives the real tool implementations, 3-tier edit matcher, agent loop, and verification pipeline against a toy repository with a scripted model — no GPU or network required.

## 5. Tuning Workflow

1. Run the notebook on a small task set; inspect `eval_results/<id>.trace.json` (every tool call + result) and `results.csv`.
2. Diagnose failures by category: `patch_apply_failed` (edit discipline), `not_resolved` (wrong fix / missing verification), `env_setup_failed` (task infrastructure, not the agent).
3. Adjust `prompts/system.md` / `configs/sampling.yaml` in small steps; keep the prompt compact — each rule should be traceable to a failure you actually observed.
4. Re-run and compare resolution rate, `tool_errors`, and token usage; then rebuild `submission.zip`.

## 6. Internal Documents

Course reports, presentation decks, and experiment write-ups live in `experiments/` and the repo root but are **gitignored** — they are local working documents, not part of the public repository or the competition submission.
