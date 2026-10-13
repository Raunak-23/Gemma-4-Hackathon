#!/usr/bin/env python3
"""Build and validate submission.zip for Google Gemma 4 Developer Agent Competition.

Validates and packages the submission files:
  - Exactly one root config (agent.yaml)
  - Single base model (gemma-4-31b-it-qat-w4a16-ct) across all agents
  - Allowed file extensions only (.yaml, .yml, .md, .txt, .py, .json, .safetensors)
  - Submission size < 3 GiB
  - Safe YAML parsing and structure check
"""

import os
import sys
import zipfile
import yaml

ALLOWED_EXTENSIONS = {".yaml", ".yml", ".md", ".txt", ".py", ".json", ".safetensors"}
ALLOWED_MODELS = {"gemma-4-31b-it-qat-w4a16-ct"}
MAX_TOTAL_BYTES = 3 * 1024 * 1024 * 1024  # 3 GiB

SUBMISSION_ROOT_ITEMS = [
    "agent.yaml",
    "agent.yml",
    "root_agent.yaml",
    "root_agent.yml",
    "eval_config.yaml",
    "configs",
    "prompts",
    "sub_agents",
    "adapters",
    "skills",
]


def collect_submission_files(base_dir):
    all_files = []
    # If base_dir contains an agent.yaml alongside repo files (e.g. gemma4 root),
    # collect only submission items
    for item in SUBMISSION_ROOT_ITEMS:
        item_path = os.path.join(base_dir, item)
        if os.path.isfile(item_path):
            all_files.append((item, item_path, os.path.getsize(item_path)))
        elif os.path.isdir(item_path):
            for root, dirs, files in os.walk(item_path):
                dirs[:] = [d for d in dirs if d != "__pycache__"]
                for f in files:
                    fp = os.path.join(root, f)
                    rel = os.path.relpath(fp, base_dir).replace("\\", "/")
                    all_files.append((rel, fp, os.path.getsize(fp)))
    return all_files


def validate_submission(base_dir):
    errors = []
    warnings = []

    if not os.path.isdir(base_dir):
        errors.append(f"Directory '{base_dir}' does not exist.")
        return errors, warnings, [], 0

    all_files = collect_submission_files(base_dir)

    # 1. Check Root Config
    root_configs = [rel for rel, _, _ in all_files if rel in ["agent.yaml", "agent.yml", "root_agent.yaml", "root_agent.yml"]]
    if len(root_configs) == 0:
        errors.append("Missing root config: Expected 'agent.yaml' at root.")
    elif len(root_configs) > 1:
        errors.append(f"Multiple root configs found: {root_configs}. Only one is allowed.")

    # 2. Check Allowed Extensions and Sizes
    total_size = 0
    declared_models = set()

    for relpath, filepath, size in all_files:
        ext = os.path.splitext(filepath)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            errors.append(f"Disallowed file extension '{ext}' for file '{relpath}'. Allowed: {sorted(ALLOWED_EXTENSIONS)}")

        total_size += size

        if ext in {".yaml", ".yml"}:
            try:
                with open(filepath, "r", encoding="utf-8") as yf:
                    class CustomLoader(yaml.SafeLoader):
                        pass
                    def include_constructor(loader, node):
                        return f"!include {node.value}"
                    CustomLoader.add_constructor("!include", include_constructor)
                    data = yaml.load(yf, Loader=CustomLoader)
                    if isinstance(data, dict):
                        model = data.get("model")
                        if model:
                            clean_model = model.split("/")[-1]
                            declared_models.add(clean_model)
            except Exception as e:
                warnings.append(f"YAML parsing notice for '{relpath}': {e}")

    # 3. Check Single Model Rule
    if declared_models:
        if len(declared_models) > 1:
            errors.append(f"Single model rule violated! Multiple models declared: {declared_models}")
        for model in declared_models:
            if model not in ALLOWED_MODELS:
                warnings.append(f"Declared model '{model}' differs from default competition model {sorted(ALLOWED_MODELS)}.")

    # 4. Check Size Limit
    if total_size >= MAX_TOTAL_BYTES:
        errors.append(f"Total unpacked size ({total_size} bytes) exceeds 3 GiB limit.")

    return errors, warnings, all_files, total_size


def package_zip(base_dir=".", zip_path="submission.zip"):
    errors, warnings, all_files, total_size = validate_submission(base_dir)

    print("=" * 60)
    print("SUBMISSION VALIDATION SUMMARY")
    print("=" * 60)
    print(f"Source Directory: {os.path.abspath(base_dir)}")
    print(f"Total Files:      {len(all_files)}")
    print(f"Total Size:       {total_size:,} bytes ({total_size / 1024:.1f} KB)")

    if warnings:
        print("\nWarnings:")
        for w in warnings:
            print(f"  [!] {w}")

    if errors:
        print("\nValidation Errors:")
        for e in errors:
            print(f"  [X] {e}")
        print("\nAborting package creation due to errors.")
        return False

    print("\nValidation PASSED. Creating zip archive...")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for relpath, filepath, _ in all_files:
            zf.write(filepath, arcname=relpath)

    zip_size = os.path.getsize(zip_path)
    print(f"Successfully created '{zip_path}' ({zip_size:,} bytes).")
    print("=" * 60)
    return True


if __name__ == "__main__":
    src_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    out_zip = sys.argv[2] if len(sys.argv) > 2 else "submission.zip"
    success = package_zip(src_dir, out_zip)
    sys.exit(0 if success else 1)
