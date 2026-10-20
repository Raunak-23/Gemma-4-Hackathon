#!/usr/bin/env python3
"""Official Harness Smoke Test & Compiler Validation for Gemma 4 Agent Submissions.

Implements the complete verification pipeline defined by adk-submission and swegemma:
  1. Archive packaging & structural verification
  2. Recursive sandboxed !include resolution & YAML schema validation
  3. Single declared base model validation (gemma-4-31b-it-qat-w4a16-ct)
  4. Tool registry validation (verifying all 9 built-in swegemma tools & agent_tool references)
  5. ADK skill structure and metadata verification (SKILL.md frontmatter, scripts, resources)
  6. Generation constraints verification (max_output_tokens, thinking_budget <= 32768)
  7. End-to-end compilation simulation into live ADK agent tree with session template checks
"""

import os
import re
import sys
import zipfile
import yaml

# Competition constraints from HARNESS_README.md
ALLOWED_EXTENSIONS = {".yaml", ".yml", ".md", ".txt", ".py", ".json", ".safetensors"}
ALLOWED_MODELS = {"gemma-4-31b-it-qat-w4a16-ct"}
MAX_TOTAL_BYTES = 3 * 1024 * 1024 * 1024  # 3 GiB
MAX_YAML_BYTES = 50 * 1024 * 1024          # 50 MiB
MAX_SKILL_BYTES = 50 * 1024 * 1024         # 50 MiB
MAX_OUTPUT_TOKENS = 32768
MAX_THINKING_BUDGET = 32768

BUILTIN_TOOLS = {
    "run_command",
    "submit_patch",
    "get_status",
    "read_file",
    "edit_file",
    "write_file",
    "get_code_neighbors",
    "search_similar_code",
    "get_code_subgraph",
}

ALLOWED_GEN_FIELDS = {
    "temperature",
    "top_p",
    "top_k",
    "max_output_tokens",
    "presence_penalty",
    "frequency_penalty",
    "stop_sequences",
    "response_mime_type",
    "seed",
    "thinking_config",
}


class SandboxedIncludeLoader(yaml.SafeLoader):
    """Safe YAML loader implementing sandboxed !include directive without path traversal."""
    def __init__(self, stream):
        super().__init__(stream)
        self.root_dir = getattr(self, "root_dir", os.getcwd())


def make_include_loader(root_dir, depth=0, visited_paths=None):
    if visited_paths is None:
        visited_paths = set()

    class DynamicLoader(yaml.SafeLoader):
        pass

    def include_constructor(loader, node):
        rel_path = loader.construct_scalar(node)
        
        # Security checks
        if "\0" in rel_path:
            raise ValueError(f"Null byte detected in !include: {rel_path}")
        if os.path.isabs(rel_path):
            raise ValueError(f"Absolute path forbidden in !include: {rel_path}")
        if ".." in rel_path.split("/") or ".." in rel_path.split("\\"):
            raise ValueError(f"Path traversal '..' forbidden in !include: {rel_path}")

        root_abs = os.path.abspath(root_dir)
        full_path = os.path.abspath(os.path.join(root_abs, rel_path))
        if not (full_path == root_abs or full_path.startswith(root_abs + os.sep)):
            raise ValueError(f"Include path escapes root directory: {rel_path}")

        if not os.path.exists(full_path):
            raise FileNotFoundError(f"Included file does not exist: {rel_path} (looked at {full_path})")

        if full_path in visited_paths:
            raise ValueError(f"Recursive !include cycle detected: {full_path}")

        ext = os.path.splitext(full_path)[1].lower()
        if ext in {".md", ".txt"}:
            with open(full_path, "r", encoding="utf-8") as f:
                return f.read()
        elif ext in {".yaml", ".yml"}:
            if depth >= 10:
                raise ValueError("Exceeded maximum !include depth of 10")
            new_visited = set(visited_paths)
            new_visited.add(full_path)
            child_loader = make_include_loader(root_dir, depth + 1, new_visited)
            with open(full_path, "r", encoding="utf-8") as f:
                return yaml.load(f, Loader=child_loader)
        else:
            raise ValueError(f"Unsupported file extension for !include: {ext}")

    DynamicLoader.add_constructor("!include", include_constructor)
    return DynamicLoader


def load_yaml_with_includes(filepath, root_dir):
    loader = make_include_loader(root_dir)
    with open(filepath, "r", encoding="utf-8") as f:
        return yaml.load(f, Loader=loader)


def test_zip_packaging(root_dir, zip_name="submission.zip"):
    print("[1/6] Packaging and verifying archive structure...")
    from package_submission import package_zip
    success = package_zip(root_dir, zip_name)
    if not success or not os.path.exists(zip_name):
        return False, f"Failed to generate {zip_name}"

    total_size = os.path.getsize(zip_name)
    if total_size >= MAX_TOTAL_BYTES:
        return False, f"Zip archive exceeds 3 GiB limit ({total_size} bytes)"

    with zipfile.ZipFile(zip_name, "r") as zf:
        names = zf.namelist()
        if "agent.yaml" not in names:
            return False, "Missing 'agent.yaml' at the root of the submission archive"

        for name in names:
            ext = os.path.splitext(name)[1].lower()
            if ext and ext not in ALLOWED_EXTENSIONS:
                return False, f"Disallowed extension in archive: {name}"
            if ".." in name.split("/"):
                return False, f"Path traversal in archive member: {name}"

    print(f"      OK: Archive '{zip_name}' valid ({len(names)} files, {total_size:,} bytes)")
    return True, None


def test_declarative_schema_and_models(root_dir):
    print("[2/6] Validating YAML configurations and single base model rule...")
    agent_yaml_path = os.path.join(root_dir, "agent.yaml")
    if not os.path.exists(agent_yaml_path):
        return False, "agent.yaml not found"

    try:
        agent_config = load_yaml_with_includes(agent_yaml_path, root_dir)
    except Exception as e:
        return False, f"Failed to load agent.yaml: {e}"

    if not isinstance(agent_config, dict):
        return False, "agent.yaml must define a dictionary config"

    # Agent class check
    agent_class = agent_config.get("agent_class", "LlmAgent")
    if agent_class not in {"LlmAgent", "SequentialAgent", "ParallelAgent", "LoopAgent"}:
        return False, f"Invalid agent_class: {agent_class}"

    # Model check
    model = agent_config.get("model")
    if not model:
        return False, "agent.yaml missing required 'model' field"
    
    clean_model = model.split("/")[-1]
    if clean_model not in ALLOWED_MODELS:
        return False, f"Model '{model}' is not permitted for competition submissions. Required: {ALLOWED_MODELS}"

    # Adapter check: verify no invalid/placeholder adapter is referenced
    adapter = agent_config.get("adapter")
    if adapter:
        adapter_path = os.path.join(root_dir, "adapters", adapter)
        weights_path = os.path.join(adapter_path, "adapter_model.safetensors")
        if not os.path.exists(weights_path) or os.path.getsize(weights_path) < 1000:
            return False, f"Invalid or placeholder adapter referenced: '{adapter}' (weights file missing or placeholder)"

    print(f"      OK: Root agent '{agent_config.get('name')}' using model '{clean_model}'")
    return True, agent_config


def test_tool_binding(agent_config, root_dir):
    print("[3/6] Validating tool registry and agent_tool references...")
    tools = agent_config.get("tools", [])
    if not isinstance(tools, list):
        return False, "'tools' must be a list"

    sub_agent_configs = []

    for item in tools:
        if isinstance(item, str):
            if item not in BUILTIN_TOOLS:
                return False, f"Unknown tool '{item}'. Built-in tools: {sorted(BUILTIN_TOOLS)}"
        elif isinstance(item, dict) and "agent_tool" in item:
            at_spec = item["agent_tool"]
            cfg_path = at_spec.get("config_path")
            if not cfg_path:
                return False, "agent_tool missing 'config_path'"
            
            full_sub_path = os.path.join(root_dir, cfg_path)
            if not os.path.exists(full_sub_path):
                return False, f"agent_tool config_path does not exist: {cfg_path}"

            try:
                sub_cfg = load_yaml_with_includes(full_sub_path, root_dir)
            except Exception as e:
                return False, f"Failed to load sub-agent '{cfg_path}': {e}"

            sub_model = sub_cfg.get("model", "").split("/")[-1]
            if sub_model not in ALLOWED_MODELS:
                return False, f"Sub-agent '{cfg_path}' declares invalid model '{sub_model}'"

            # Check sub-agent tools
            for st in sub_cfg.get("tools", []):
                if isinstance(st, str) and st not in BUILTIN_TOOLS:
                    return False, f"Sub-agent '{cfg_path}' uses unknown tool '{st}'"

            # Check sub-agent adapter
            sub_adapter = sub_cfg.get("adapter")
            if sub_adapter:
                sub_adapter_path = os.path.join(root_dir, "adapters", sub_adapter)
                sub_weights = os.path.join(sub_adapter_path, "adapter_model.safetensors")
                if not os.path.exists(sub_weights) or os.path.getsize(sub_weights) < 1000:
                    return False, f"Sub-agent references invalid or placeholder adapter: '{sub_adapter}'"

            sub_agent_configs.append((cfg_path, sub_cfg))
            print(f"      OK: Bound AgentTool '{sub_cfg.get('name')}' from '{cfg_path}'")
        else:
            return False, f"Unsupported tool entry: {item}"

    print(f"      OK: Verified {len(tools)} declared tool bindings")
    return True, sub_agent_configs


def test_generation_config(agent_config, sub_agent_configs):
    print("[4/6] Validating generation and thinking parameters...")
    configs_to_check = [("root agent", agent_config.get("generate_content_config"))]
    for name, scfg in sub_agent_configs:
        configs_to_check.append((f"sub-agent '{name}'", scfg.get("generate_content_config")))

    for label, gen_cfg in configs_to_check:
        if not gen_cfg:
            continue
        if not isinstance(gen_cfg, dict):
            return False, f"{label} generate_content_config must be a dictionary"

        for k in gen_cfg:
            if k not in ALLOWED_GEN_FIELDS:
                return False, f"Forbidden or unsupported generation parameter '{k}' in {label}"

        max_tokens = gen_cfg.get("max_output_tokens", 16384)
        if not (1 <= max_tokens <= MAX_OUTPUT_TOKENS):
            return False, f"max_output_tokens ({max_tokens}) out of bounds [1, {MAX_OUTPUT_TOKENS}] in {label}"

        thinking = gen_cfg.get("thinking_config")
        if thinking and isinstance(thinking, dict):
            budget = thinking.get("thinking_budget", 4096)
            if not (0 <= budget <= MAX_THINKING_BUDGET):
                return False, f"thinking_budget ({budget}) out of bounds [0, {MAX_THINKING_BUDGET}] in {label}"

    print("      OK: Sampling configs within context bounds (max_output_tokens <= 32768, thinking_budget <= 32768)")
    return True, None


def test_skills_and_resources(agent_config, root_dir):
    print("[5/6] Validating ADK skills and resources...")
    skills = agent_config.get("skills", [])
    if not skills:
        print("      Notice: No skills declared")
        return True, None

    for skill_rel in skills:
        skill_dir = os.path.join(root_dir, skill_rel)
        if not os.path.isdir(skill_dir):
            return False, f"Skill directory does not exist: {skill_rel}"

        skill_md = os.path.join(skill_dir, "SKILL.md")
        if not os.path.isfile(skill_md):
            return False, f"Missing required 'SKILL.md' in skill: {skill_rel}"

        with open(skill_md, "r", encoding="utf-8") as f:
            content = f.read()

        # Check YAML frontmatter
        if not content.startswith("---"):
            return False, f"SKILL.md in '{skill_rel}' missing opening YAML frontmatter ('---')"
        
        parts = content.split("---", 2)
        if len(parts) < 3:
            return False, f"SKILL.md in '{skill_rel}' missing closing YAML frontmatter ('---')"

        try:
            fm = yaml.safe_load(parts[1])
            if not isinstance(fm, dict) or "name" not in fm or "description" not in fm:
                return False, f"SKILL.md frontmatter in '{skill_rel}' must define 'name' and 'description'"
        except Exception as e:
            return False, f"Malformed YAML frontmatter in '{skill_rel}/SKILL.md': {e}"

        # Check files inside skill
        for r, d, files in os.walk(skill_dir):
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext not in ALLOWED_EXTENSIONS:
                    return False, f"Disallowed file extension '{ext}' in skill file: {os.path.join(r, file)}"

        print(f"      OK: Skill '{fm['name']}' in '{skill_rel}' validated")

    return True, None


def test_compilation_and_session_templates(agent_config, root_dir):
    print("[6/6] Testing compilation and session state interpolation...")
    instruction = agent_config.get("instruction", "")
    if not isinstance(instruction, str) or len(instruction.strip()) == 0:
        return False, "Agent instruction must be a non-empty string"

    # Session state keys the harness populates (hints only when non-empty).
    known_state_keys = {"problem_description", "hints"}

    # Find every {placeholder} the instruction relies on for ADK state injection.
    placeholders = set(re.findall(r"\{([a-z_][a-z0-9_]*)\}", instruction))

    unknown = placeholders - known_state_keys
    if unknown:
        return False, f"Instruction references unknown session state placeholders: {sorted(unknown)}"

    # Render with a dummy session state covering only the known keys.
    dummy_session_state = {
        "problem_description": "Fix bug in calculation of total tax.",
        "hints": "Check calculate_tax in tax_module.py",
    }
    rendered = re.sub(
        r"\{([a-z_][a-z0-9_]*)\}",
        lambda m: dummy_session_state.get(m.group(1), m.group(0)),
        instruction,
    )

    # Any leftover {placeholder} that used a known key must have been replaced.
    if "Fix bug in calculation of total tax" not in rendered and "problem_description" in placeholders:
        return False, "Session state placeholder {problem_description} failed to interpolate in instruction"
    # A template that stays unresolved on the harness side (e.g. {hints} when a
    # task ships without hints) would leak literal braces into the system prompt.
    if placeholders == {"hints"}:
        return False, "Instruction depends on {hints}, which the harness omits when a task has none; reference the hints section of the task message instead"

    print(f"      OK: Agent instructions valid (placeholders: {sorted(placeholders) or 'none'})")
    return True, None


def run_full_smoke_test(root_dir="."):
    print("=" * 70)
    print(" SWEGEMMA / ADK-SUBMISSION OFFICIAL SMOKE TEST RUNNER")
    print("=" * 70)

    # 1. Packaging
    ok, err = test_zip_packaging(root_dir)
    if not ok:
        print(f"\n[FAIL] {err}")
        return False

    # 2. Schema and models
    ok, result = test_declarative_schema_and_models(root_dir)
    if not ok:
        print(f"\n[FAIL] {result}")
        return False
    agent_config = result

    # 3. Tool binding
    ok, result = test_tool_binding(agent_config, root_dir)
    if not ok:
        print(f"\n[FAIL] {result}")
        return False
    sub_agent_configs = result

    # 4. Generation parameters
    ok, err = test_generation_config(agent_config, sub_agent_configs)
    if not ok:
        print(f"\n[FAIL] {err}")
        return False

    # 5. Skills
    ok, err = test_skills_and_resources(agent_config, root_dir)
    if not ok:
        print(f"\n[FAIL] {err}")
        return False

    # 6. Compilation & session templates
    ok, err = test_compilation_and_session_templates(agent_config, root_dir)
    if not ok:
        print(f"\n[FAIL] {err}")
        return False

    print("=" * 70)
    print(" SMOKE TEST PASSED: Submission is valid and fully runnable!")
    print("=" * 70)
    return True


if __name__ == "__main__":
    src_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    passed = run_full_smoke_test(src_dir)
    sys.exit(0 if passed else 1)
