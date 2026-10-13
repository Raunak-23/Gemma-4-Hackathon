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
├── adapters/                   # Fine-tuned PEFT LoRA adapters or model weights
│   ├── main_lora/
│   │   ├── adapter_config.json
│   │   └── adapter_model.safetensors
│   └── tool_lora/
│       ├── adapter_config.json
│       └── adapter_model.safetensors
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

- **`agent.yaml`**: The primary ADK `LlmAgent` configuration (`swe_coder`). Bound to all 9 execution, workspace, and graph tools, plus the `code_analyzer` AgentTool.
- **`configs/sampling.yaml`**: Configures temperature (`0.2`), top-p (`0.95`), top-k (`40`), max output tokens (`16,384`), and thinking budget (`4,096`) compatible with vLLM 32k context.
- **`prompts/system.md`**: Systematic 6-stage engineering instructions (Understand $\to$ Localize $\to$ Reproduce $\to$ Edit $\to$ Verify $\to$ Submit) incorporating critical gotchas (e.g. scratch files in `/tmp/`, never modifying `conftest.py` or `pytest.ini`).
- **`prompts/analyzer.md`**: Specialized read-only instructions for the `code_analyzer` sub-agent.
- **`sub_agents/code_analyzer.yaml`**: Configures an isolated `AgentTool` (`skip_summarization: true`) for read-only exploration and graph inspection.
- **`adapters/`**: Houses PEFT LoRA configurations and safetensors for `main_lora` and `tool_lora`.
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

## 5. Local Evaluation

To evaluate locally using the official harness CLI:

```bash
swegemma eval \
  --tasks tasks.jsonl \
  --snapshots-dir snapshots \
  --submission-dir . \
  --results-dir results/run_01 \
  --sandbox docker \
  --max-tool-calls 50 \
  --max-time-minutes 30
```
