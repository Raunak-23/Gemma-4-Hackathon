#!/usr/bin/env python3
"""Assemble kaggle/gemma4_agent_eval.ipynb from kaggle/notebook_cells/*.

Cell sources live in notebook_cells/ (sorted by filename; .py = code cell, .md =
markdown cell, .txt = code cell kept verbatim for IPython magics). The submission
snapshot embedded in the notebook is read from the live repo files, so re-run this
after editing prompts/configs:

    python kaggle/build_notebook.py
"""

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CELLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "notebook_cells")
OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gemma4_agent_eval.ipynb")

# Files embedded as the fallback submission snapshot (exact competition layout).
SNAPSHOT_FILES = [
    "agent.yaml",
    "eval_config.yaml",
    "configs/sampling.yaml",
    "prompts/system.md",
    "skills/repo_navigation/SKILL.md",
    "skills/repo_navigation/scripts/repo_search.py",
    "skills/repo_navigation/resources/navigation_patterns.md",
]


def build_snapshot():
    snap = {}
    for rel in SNAPSHOT_FILES:
        path = os.path.join(ROOT, rel)
        if not os.path.isfile(path):
            raise SystemExit(f"missing snapshot file: {rel}")
        with open(path, encoding="utf-8") as f:
            snap[rel] = f.read()
    return snap


def main():
    cells = []
    for fname in sorted(os.listdir(CELLS_DIR)):
        path = os.path.join(CELLS_DIR, fname)
        if not os.path.isfile(path) or fname.startswith("."):
            continue
        with open(path, encoding="utf-8") as f:
            src = f.read()
        if fname.endswith(".md"):
            cells.append({"cell_type": "markdown", "source": src})
        else:
            if "@EMBED_SNAPSHOT@" in src:
                snapshot = json.dumps(build_snapshot(), ensure_ascii=True, indent=1)
                src = src.replace("@EMBED_SNAPSHOT@", snapshot)
            cells.append({"cell_type": "code", "source": src})

    nb_cells = []
    for i, cell in enumerate(cells):
        nb_cells.append({
            "cell_type": cell["cell_type"],
            "id": f"cell-{i:02d}",
            "metadata": {},
            "source": cell["source"],
            **({"outputs": [], "execution_count": None}
               if cell["cell_type"] == "code" else {}),
        })

    notebook = {
        "cells": nb_cells,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    with open(OUT_PATH, "w", encoding="utf-8", newline="\n") as f:
        json.dump(notebook, f, indent=1, ensure_ascii=False)
    print(f"wrote {OUT_PATH} with {len(nb_cells)} cells "
          f"({', '.join(c['id'] for c in nb_cells)})")


if __name__ == "__main__":
    main()
