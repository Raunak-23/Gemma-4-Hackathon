---
name: repo_navigation
description: Structured methodology and sandboxed Python utilities for navigating large Python codebases, querying AST call graphs, and pinpointing issue locations.
---

# Repository Navigation Skill

This skill provides autonomous agents with structured methodologies and executable scripts to efficiently explore unfamiliar codebases, locate fault locations, and navigate call graphs without overflowing context windows.

---

## 1. When to Use This Skill

- When starting an issue on an unfamiliar repository.
- When the problem description references specific classes, functions, or exception messages, but the file path is unknown.
- When an issue requires tracing caller/callee relationships across multiple modules.
- When searching across files with high token efficiency.

---

## 2. Navigating the Codebase: Recommended Workflow

### Step 1: Pre-Computed Graph & Embedding Lookup
1. If the repository provides pre-computed code graphs and embeddings, use:
   - `search_similar_code(query="<symbol_name>")`: Locate candidate nodes. Use exact identifier names (e.g. `HTTPConnection` or `parse_header`), NOT free-form prose.
   - `get_code_neighbors(node="<node_name>")`: Find incoming and outgoing edges (callers, callees, imports).
   - `get_code_subgraph(nodes=["<node1>", "<node2>"])`: View interconnected components.

### Step 2: Sandboxed Python Script Search
If symbol names are ambiguous or graph tools return no match, run the bundled helper script:
```bash
python3 skills/repo_navigation/scripts/repo_search.py --symbol <SymbolName>
```
or search for literal patterns / error strings:
```bash
python3 skills/repo_navigation/scripts/repo_search.py --pattern "<search_text>"
```
This utility automatically ignores `.git`, `__pycache__`, build artifacts, and virtual environments, returning concise line references within the 5,000 character output limit.

### Step 3: Targeted File Reading
- Never read an entire file if only one function is relevant.
- Use `read_file(filepath="...", start_line=..., end_line=...)` around the discovered definition or usage lines.

---

## 3. Bundled Utilities & Resources

- `scripts/repo_search.py`: Lightweight AST and text scanner designed to run within the sandbox environment.
- `resources/navigation_patterns.md`: Deep dive into common Python repository architectures, locating reproduction hooks, and tracing exception stacks.
