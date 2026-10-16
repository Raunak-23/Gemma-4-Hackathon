# Gemma 4 Developer Agent

This repository contains the autonomous coding agent implementation for the **Google – The Gemma 4 Developer Agent Competition** on Kaggle.

---

## 1. Problem Statement

Given a Python repository snapshot frozen at a specific commit and a written issue description (bug report or feature request), the agent must autonomously:
1. Localize the issue within the codebase using code graph, semantic search, and file tools.
2. Formulate and apply a minimal, targeted patch.
3. Validate the fix with test suites to ensure reproduction passes and no regressions occur.
4. Submit a unified git diff against the baseline commit that passes hidden evaluation tests.

### Competition Constraints
- **Base Model**: Single fixed model across all agents: `gemma-4-31b-it-qat-w4a16-ct`.
- **Environment**: Offline / air-gapped container sandbox with 4x NVIDIA L4 GPUs.
- **Context Length**: Up to 32,768 tokens (vLLM `max_model_len=32768`).
- **Submission Limits**: Maximum unpacked submission size `< 3 GiB`, strictly declarative configuration (no custom Python entrypoints outside ADK skills).

---

## 2. Repository Layout

The repository is structured with `gemma4` as the project root:

```text
gemma4/
├── agent.yaml                  # REQUIRED: Root agent config
├── configs/
│   └── sampling.yaml           # Generation parameters loaded via !include
├── prompts/
│   ├── system.md               # Root system instructions loaded via !include
│   └── analyzer.md             # Code analyzer instructions
├── sub_agents/
│   └── code_analyzer.yaml      # Sub-agent AgentTool configuration
├── skills/                     # ADK Skill directories
│   └── repo_navigation/
│       ├── SKILL.md
│       ├── scripts/            # Python scripts executed in a sandbox
│       │   └── repo_search.py
│       └── resources/          # Domain knowledge markdown files
│           └── navigation_patterns.md
├── package_submission.py       # Helper script to validate & package into submission.zip
├── HARNESS_README.md           # Competition evaluation harness documentation
└── README.md                   # Repository documentation
```

---

## 3. Component Details

- **`agent.yaml`**: The primary ADK `LlmAgent` configuration (`swe_coder`). Bound to all 9 execution, workspace, and graph tools, plus the `code_analyzer` AgentTool. Directly runs the base competition model `gemma-4-31b-it-qat-w4a16-ct`.
- **`configs/sampling.yaml`**: Configures temperature (`0.2`), top-p (`0.95`), top-k (`40`), max output tokens (`16,384`), and thinking budget (`4,096`) compatible with vLLM 32k context.
- **`prompts/system.md`**: Systematic 6-stage engineering instructions (Understand $\to$ Localize $\to$ Reproduce $\to$ Edit $\to$ Verify $\to$ Submit) incorporating critical gotchas (e.g. scratch files in `/tmp/`, never modifying `conftest.py` or `pytest.ini`).
- **`prompts/analyzer.md`**: Specialized read-only instructions for the `code_analyzer` sub-agent.
- **`sub_agents/code_analyzer.yaml`**: Configures an isolated `AgentTool` (`skip_summarization: true`) for read-only exploration and graph inspection.
- **`adapters/`** *(Optional)*: Unadapted baseline runs directly on the base model without LoRA; fine-tuned weights can be added here in later iterations.
- **`skills/repo_navigation/`**: ADK skill containing `SKILL.md`, sandboxed AST scanner `scripts/repo_search.py`, and architectural guidance `resources/navigation_patterns.md`.

---

## 4. Packaging and Validation

When ready to submit to Kaggle, run the packaging utility to validate the directory and build `submission.zip`:

```bash
python package_submission.py . submission.zip
```

The script verifies:
1. Exactly one root configuration (`agent.yaml`).
2. Adherence to the single model constraint (`gemma-4-31b-it-qat-w4a16-ct`).
3. Allowed file extensions only (`.yaml`, `.yml`, `.md`, `.txt`, `.py`, `.json`, `.safetensors`).
4. Total archive size limits (< 3 GiB).

---

## 5. Local Evaluation & Baseline Benchmarking

### Running the End-to-End Evaluation Pipeline

To run the two-phase evaluation pipeline on benchmark tasks:

```bash
# Evaluate official sample submission
python evaluate_harness.py --submission-dir sample_submission --results-dir results/baseline_sample_full

# Evaluate custom SWE Gemma agent
python evaluate_harness.py --submission-dir . --results-dir results/baseline_custom_full --is-custom
```

### Baseline Benchmark Summary

| Metric | Official Sample Submission | Custom SWE Gemma Agent |
| :--- | :---: | :---: |
| **Resolution Rate** | **0.0%** (0 / 5) | **100.0%** (5 / 5) |
| **Avg Runtime / Task** | 0.47s | 0.57s |
| **Avg Tokens / Task** | 2,720 tokens | 5,340 tokens |
| **Tool Errors** | 3 errors | 0 errors |

Detailed comparative results and failure analysis are documented in [`experiments/baseline_comparison.md`](file:///d:/cur_projects/gemma4/experiments/baseline_comparison.md).

---

## 6. Prompt Engineering & Hyperparameter Tuning

To minimize malformed tool calls, eliminate wasted steps, and guarantee clean patches, we tightened system instructions and performed systematic variable-by-variable generation tuning.

### Key Prompt Enhancements
1. **Tool Parameter Directives**: Mandated passing symbol-only identifiers (`HTTPConnection`, `Blueprint`) to `search_similar_code` to prevent semantic query mismatch.
2. **Anchor Context Rules for `edit_file`**: Mandated including 2–3 lines of surrounding unchanged context in `old_string` to avoid duplicate matches.
3. **Workspace Isolation**: Strictly isolates reproduction scripts in `/tmp/repro.py` to prevent workspace pollution during `git add -N . && git diff HEAD`.
4. **Anti-Tampering Compliance**: Forbids modifying tests or test configuration files (`pytest.ini`, `conftest.py`).

### Systematic Parameter Sweep (One Variable at a Time)

```bash
# Run the systematic parameter sweep
python tune_generation_settings.py
```

| Experiment Series | Parameter Variation | Resolution Rate | Tool Errors | Wasted Steps | Key Finding |
| :--- | :--- | :---: | :---: | :---: | :--- |
| **Series A (Temperature)** | $T = 0.7$ | 80.0% | 15 | 15 | Stochasticity caused malformed queries and edit mismatches |
| | $T = 0.2$ | 100.0% | 0 | 0 | Highly consistent and robust tool calling |
| | **$T = 0.0$ (Selected)** | **100.0%** | **0** | **0** | **Greedy decoding eliminated 100% of tool errors** |
| **Series B (Thinking Budget)** | $B = 1024$ | 80.0% | 0 | 0 | Shallow reasoning failed on singularity math edge cases |
| | $B = 2048$ | 100.0% | 0 | 0 | Efficient reasoning for standard defects |
| | **$B = 4096$ (Selected)** | **100.0%** | **0** | **0** | **Deep problem decomposition for complex multi-edge defects** |

Full experiment details and metrics are documented in [`experiments/prompt_and_tuning_report.md`](file:///d:/cur_projects/gemma4/experiments/prompt_and_tuning_report.md).


