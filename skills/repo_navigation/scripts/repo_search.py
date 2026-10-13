#!/usr/bin/env python3
"""Lightweight repository search utility for SWE Gemma agents in sandboxed environments.

Usage:
  python3 repo_search.py --symbol MyClass
  python3 repo_search.py --pattern "error_message"
  python3 repo_search.py --func my_function
"""

import argparse
import ast
import os
import re
import sys

IGNORE_DIRS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "build",
    "dist",
    ".tox",
    "venv",
    ".venv",
    "site-packages",
}


def search_ast_symbols(root_dir, symbol_name, symbol_type=None):
    results = []
    symbol_lower = symbol_name.lower()

    for dirpath, dirnames, filenames in os.walk(root_dir):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS and not d.endswith(".egg-info")]

        for fname in filenames:
            if not fname.endswith(".py"):
                continue

            filepath = os.path.join(dirpath, fname)
            rel_path = os.path.relpath(filepath, root_dir).replace("\\", "/")

            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                tree = ast.parse(content, filename=rel_path)
            except Exception:
                continue

            for node in ast.walk(tree):
                matched = False
                matched_kind = ""
                name = ""

                if isinstance(node, ast.ClassDef):
                    name = node.name
                    matched_kind = "class"
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    name = node.name
                    matched_kind = "def"

                if name and (name == symbol_name or name.lower() == symbol_lower):
                    if symbol_type is None or symbol_type == matched_kind:
                        matched = True

                if matched:
                    results.append((rel_path, node.lineno, f"{matched_kind} {name}"))

    return results


def search_text_pattern(root_dir, pattern_str, is_regex=False):
    results = []
    flags = re.IGNORECASE
    try:
        regex = re.compile(pattern_str if is_regex else re.escape(pattern_str), flags)
    except re.error as e:
        print(f"Error compiling regex: {e}", file=sys.stderr)
        return results

    for dirpath, dirnames, filenames in os.walk(root_dir):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS and not d.endswith(".egg-info")]

        for fname in filenames:
            if fname.endswith((".pyc", ".so", ".tar", ".gz", ".zip", ".png", ".jpg")):
                continue

            filepath = os.path.join(dirpath, fname)
            rel_path = os.path.relpath(filepath, root_dir).replace("\\", "/")

            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    for lineno, line in enumerate(f, start=1):
                        if regex.search(line):
                            results.append((rel_path, lineno, line.strip()))
                            if len(results) >= 50:
                                return results
            except Exception:
                continue

    return results


def main():
    parser = argparse.ArgumentParser(description="Search repository symbols and patterns.")
    parser.add_argument("--root", default=".", help="Root directory to search (default: .)")
    parser.add_argument("--symbol", help="Search for class or function by symbol name")
    parser.add_argument("--pattern", help="Search for text or regex pattern across files")
    parser.add_argument("--regex", action="store_true", help="Treat pattern as regular expression")
    parser.add_argument("--max-results", type=int, default=30, help="Maximum results to return")

    args = parser.parse_args()

    if not args.symbol and not args.pattern:
        parser.print_help()
        sys.exit(1)

    if args.symbol:
        matches = search_ast_symbols(args.root, args.symbol)
        print(f"=== Symbol matches for '{args.symbol}' ({len(matches)} found) ===")
        for path, line, desc in matches[:args.max_results]:
            print(f"{path}:{line}: {desc}")

    elif args.pattern:
        matches = search_text_pattern(args.root, args.pattern, is_regex=args.regex)
        print(f"=== Pattern matches for '{args.pattern}' ({len(matches)} found) ===")
        for path, line, text in matches[:args.max_results]:
            truncated_text = text[:100] + ("..." if len(text) > 100 else "")
            print(f"{path}:{line}: {truncated_text}")


if __name__ == "__main__":
    main()
