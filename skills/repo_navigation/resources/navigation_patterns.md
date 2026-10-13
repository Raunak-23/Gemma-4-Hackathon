# Python Repository Navigation Patterns

This resource outlines best practices and common architectural patterns for locating bugs in large Python repositories.

---

## 1. Common Repository Layouts

### The `src/` Layout
- Source code is placed inside `src/<package_name>/`.
- Example: `src/urllib3/connectionpool.py`.
- Tests are located under `tests/` or `test/`.

### The Flat Layout
- Package folder sits directly in the repository root.
- Example: `flask/app.py` or `django/db/models/query.py`.
- Root modules often import top-level symbols in `__init__.py`.

---

## 2. Navigating from Problem Descriptions to Code

1. **Extract Key Identifiers**:
   - Look for function names, class names, or specific parameter names mentioned in the issue.
   - Look for exception types (e.g. `TypeError`, `ValueError`, `KeyError`) or exact error strings.
2. **Search using AST / Embedding Graph First**:
   - Query `search_similar_code(query="<identifier>")` or run `repo_search.py --symbol <identifier>`.
   - Never grep the whole workspace with unbounded output; always filter out test directories and caches.
3. **Trace Call Stacks**:
   - For an error originating in function `A`, use `get_code_neighbors(node="A", edge_type="CALLS")` to discover what calls `A` and what `A` delegates to.
4. **Identify the Single Point of Failure**:
   - Avoid spreading edits across multiple files unless strictly necessary.
   - Most SWE-bench tasks require changes to fewer than 20 lines in 1–2 files.

---

## 3. Preserving Context Window

- Always inspect code with `read_file(filepath=..., start_line=..., end_line=...)`.
- Keep slice ranges to 50–100 lines around the target definition.
- If unsure of the exact lines, inspect the symbol definition line first from `repo_search.py`.
