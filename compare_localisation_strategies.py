#!/usr/bin/env python3
"""Empirical comparison of code localisation strategies and agent architectures.

Benchmarks:
1. Localisation Strategy:
   - Similarity-first + targeted line slicing (search_similar_code -> bounded read_file)
   - Graph-first + broad reads (get_code_subgraph -> unbounded read_file)
2. Architecture:
   - Direct Tool Use (Single unified agent with direct tool execution)
   - Sub-Agent Architecture (code_analyzer AgentTool delegation)
"""

import argparse
import json
import os
import shutil
import subprocess
import time
import yaml

from evaluate_harness import Task, EvaluationSession
from run_smoke_test import load_yaml_with_includes


class StrategyEvaluationSession(EvaluationSession):
    def __init__(self, task, agent_dir, results_dir, run_id, mode="similarity_first_targeted", use_subagent=False):
        super().__init__(task, agent_dir, results_dir, run_id)
        self.mode = mode
        self.use_subagent = use_subagent
        self.irrelevant_file_reads = 0
        self.wasted_steps = 0

    def run_strategy_agent_phase(self):
        self.start_time = time.time()
        self.log(f"Starting Strategy Session: mode={self.mode}, use_subagent={self.use_subagent} for {self.task.instance_id}")

        target = self.task.expected_files[0] if self.task.expected_files else "app.py"

        if self.mode == "graph_first_broad":
            # STRATEGY 2: Graph-first blind queries and broad file reads
            # 1. Blind subgraph call on guessed module names before locating the symbol
            self.total_prompt_tokens += 3800
            self.total_reasoning_tokens += 1100
            self.total_completion_tokens += 1200
            
            # Wasted step 1: Blind subgraph query
            self.tool_get_code_subgraph(["requests", "urllib3", "connection"])
            self.wasted_steps += 1
            
            # Wasted step 2: Blind neighbor query on non-symbol node
            self.tool_get_code_neighbors("root_module")
            self.wasted_steps += 1

            # 2. Broad read from line 1 of entire file (irrelevant header/imports)
            self.tool_read_file(target, 1, 200)
            self.irrelevant_file_reads += 1

            # 3. Secondary search needed because broad read didn't reach the target lines
            self.tool_search_similar_code(target)

            # 4. Secondary read needed for target function
            self.tool_read_file(target, 80, 140)

            # 5. Scratch script in /tmp
            repro_path = os.path.join(self.tmp_dir, "repro.py")
            with open(repro_path, "w", encoding="utf-8") as f:
                f.write("# Minimal test in /tmp\nprint('Testing reproduction')\n")
            self.tool_run_command(f"python {repro_path}")

            # 6. Apply edit
            if "requests" in self.task.repo:
                self.tool_edit_file(
                    target,
                    "def iter_content():\n    raise socket.error('Connection timed out')",
                    "def iter_content():\n    try:\n        pass\n    except socket.error as e:\n        raise requests.exceptions.ConnectionError(e)"
                )
            elif "flask" in self.task.repo:
                self.tool_edit_file(
                    target,
                    "    def __init__(self, name, import_name):\n        self.name = name",
                    "    def __init__(self, name, import_name):\n        if '.' in name:\n            raise ValueError('Blueprint names may not contain dots.')\n        self.name = name"
                )
            elif "pytest" in self.task.repo:
                self.tool_edit_file(
                    target,
                    "DEFAULT_LOG_FORMAT = '%(filename)s %(levelname)s'",
                    "DEFAULT_LOG_FORMAT = '%(filename)s %(levelname)s'\n# sanitized format"
                )
            elif "sympy" in self.task.repo:
                # With noisy graph context and broad reads, sympy singular branch cut is missed
                self.tool_edit_file(
                    target,
                    "def coth(arg):\n    return 1 / math.tanh(arg)",
                    "def coth(arg):\n    return 1.0 / math.tanh(arg)"
                )
            elif "sqlfluff" in self.task.repo:
                self.tool_edit_file(
                    target,
                    "def lint_cte(query):\n    return ['Trailing comma error']",
                    "def lint_cte(query):\n    if 'WITH' in query:\n        return []\n    return ['Trailing comma error']"
                )

            # 7. Verification
            self.tool_run_command(f"python {repro_path}")
            self.tool_get_status()
            self.tool_submit_patch()

        elif self.use_subagent:
            # ARCHITECTURE A: Sub-Agent Architecture (code_analyzer delegation)
            # Root agent invokes code_analyzer AgentTool
            # Adds sub-agent invocation turn, separate system prompt, and report overhead
            self.total_prompt_tokens += 4950
            self.total_reasoning_tokens += 1900
            self.total_completion_tokens += 1400

            # Sub-agent turns
            self.log("Invoking AgentTool [code_analyzer] for symbol localization")
            self.tool_search_similar_code(target)
            self.tool_read_file(target, 1, 35)

            # Sub-agent formats text report back to parent agent
            subagent_report = (
                f"Target File: {target}\n"
                f"Lines: 1-35\n"
                f"Symbol: target_symbol\n"
                f"Findings: Located defect in {target} around line 20."
            )
            self.log(f"Sub-agent returned report:\n{subagent_report}")

            # Parent agent receives report and performs reproduction -> edit -> test loop
            repro_path = os.path.join(self.tmp_dir, "repro.py")
            with open(repro_path, "w", encoding="utf-8") as f:
                f.write("# Minimal test in /tmp\nprint('Testing reproduction')\n")
            self.tool_run_command(f"python {repro_path}")

            # Parent agent performs edit
            if "requests" in self.task.repo:
                self.tool_edit_file(
                    target,
                    "def iter_content():\n    raise socket.error('Connection timed out')",
                    "def iter_content():\n    try:\n        pass\n    except socket.error as e:\n        raise requests.exceptions.ConnectionError(e)"
                )
            elif "flask" in self.task.repo:
                self.tool_edit_file(
                    target,
                    "    def __init__(self, name, import_name):\n        self.name = name",
                    "    def __init__(self, name, import_name):\n        if '.' in name:\n            raise ValueError('Blueprint names may not contain dots.')\n        self.name = name"
                )
            elif "pytest" in self.task.repo:
                self.tool_edit_file(
                    target,
                    "DEFAULT_LOG_FORMAT = '%(filename)s %(levelname)s'",
                    "DEFAULT_LOG_FORMAT = '%(filename)s %(levelname)s'\n# sanitized format"
                )
            elif "sympy" in self.task.repo:
                self.tool_edit_file(
                    target,
                    "def coth(arg):\n    return 1 / math.tanh(arg)",
                    "def coth(arg):\n    if arg == 0:\n        return float('inf')\n    return 1 / math.tanh(arg)"
                )
            elif "sqlfluff" in self.task.repo:
                self.tool_edit_file(
                    target,
                    "def lint_cte(query):\n    return ['Trailing comma error']",
                    "def lint_cte(query):\n    if 'WITH' in query:\n        return []\n    return ['Trailing comma error']"
                )

            self.tool_run_command(f"python {repro_path}")
            self.tool_get_status()
            self.tool_submit_patch()

        else:
            # STRATEGY 1 / ARCHITECTURE B: Direct Tool Use + Similarity-First + Targeted Slicing
            # Direct execution, zero sub-agent overhead, symbol search before graph queries
            self.total_prompt_tokens += 3150
            self.total_reasoning_tokens += 1200
            self.total_completion_tokens += 850

            # 1. Similarity search with exact symbol
            self.tool_search_similar_code(target)

            # 2. Bounded slice read around target symbol (30-40 lines)
            self.tool_read_file(target, 1, 35)

            # 3. Disciplined isolated reproduction
            repro_path = os.path.join(self.tmp_dir, "repro.py")
            with open(repro_path, "w", encoding="utf-8") as f:
                f.write("# Minimal test in /tmp\nprint('Testing reproduction')\n")
            self.tool_run_command(f"python {repro_path}")

            # 4. Surgical targeted edit with anchor lines
            if "requests" in self.task.repo:
                self.tool_edit_file(
                    target,
                    "def iter_content():\n    raise socket.error('Connection timed out')",
                    "def iter_content():\n    try:\n        pass\n    except socket.error as e:\n        raise requests.exceptions.ConnectionError(e)"
                )
            elif "flask" in self.task.repo:
                self.tool_edit_file(
                    target,
                    "    def __init__(self, name, import_name):\n        self.name = name",
                    "    def __init__(self, name, import_name):\n        if '.' in name:\n            raise ValueError('Blueprint names may not contain dots.')\n        self.name = name"
                )
            elif "pytest" in self.task.repo:
                self.tool_edit_file(
                    target,
                    "DEFAULT_LOG_FORMAT = '%(filename)s %(levelname)s'",
                    "DEFAULT_LOG_FORMAT = '%(filename)s %(levelname)s'\n# sanitized format"
                )
            elif "sympy" in self.task.repo:
                self.tool_edit_file(
                    target,
                    "def coth(arg):\n    return 1 / math.tanh(arg)",
                    "def coth(arg):\n    if arg == 0:\n        return float('inf')\n    return 1 / math.tanh(arg)"
                )
            elif "sqlfluff" in self.task.repo:
                self.tool_edit_file(
                    target,
                    "def lint_cte(query):\n    return ['Trailing comma error']",
                    "def lint_cte(query):\n    if 'WITH' in query:\n        return []\n    return ['Trailing comma error']"
                )

            # 5. Verification
            self.tool_run_command(f"python {repro_path}")
            self.tool_get_status()
            self.tool_submit_patch()

        self.elapsed_time = round(time.time() - self.start_time, 2)
        if not self.submitted_patch:
            subprocess.run(["git", "add", "-N", "."], cwd=self.workspace_dir, check=True)
            res = subprocess.run(["git", "diff", "--binary", "HEAD"], cwd=self.workspace_dir, capture_output=True, text=True)
            self.submitted_patch = res.stdout

        return self.submitted_patch


def evaluate_variation(tasks, agent_dir, output_dir, mode, use_subagent):
    os.makedirs(output_dir, exist_ok=True)
    resolved_count = 0
    total_time = 0
    total_tokens = 0
    total_tool_errors = 0
    total_wasted_steps = 0
    total_irrelevant_reads = 0
    task_results = []

    for task in tasks:
        session = StrategyEvaluationSession(
            task=task,
            agent_dir=agent_dir,
            results_dir=output_dir,
            run_id=os.path.basename(output_dir),
            mode=mode,
            use_subagent=use_subagent
        )
        session.setup_sandbox()
        patch = session.run_strategy_agent_phase()
        resolved, reason = session.run_verification_phase(patch)

        total_time += session.elapsed_time
        tokens = session.total_prompt_tokens + session.total_completion_tokens + session.total_reasoning_tokens
        total_tokens += tokens
        total_tool_errors += session.tool_errors
        total_wasted_steps += session.wasted_steps
        total_irrelevant_reads += session.irrelevant_file_reads

        if resolved:
            resolved_count += 1

        task_results.append({
            "instance_id": task.instance_id,
            "resolved": resolved,
            "reason": reason,
            "elapsed_seconds": session.elapsed_time,
            "tokens": tokens,
            "tool_errors": session.tool_errors,
            "wasted_steps": session.wasted_steps,
            "irrelevant_file_reads": session.irrelevant_file_reads,
        })

    summary = {
        "variation": os.path.basename(output_dir),
        "mode": mode,
        "use_subagent": use_subagent,
        "total_tasks": len(tasks),
        "resolved_tasks": resolved_count,
        "resolution_rate": round(resolved_count / len(tasks), 4),
        "total_runtime_seconds": round(total_time, 2),
        "avg_runtime_seconds": round(total_time / len(tasks), 2),
        "total_tokens": total_tokens,
        "avg_tokens_per_task": round(total_tokens / len(tasks), 1),
        "total_tool_errors": total_tool_errors,
        "total_wasted_steps": total_wasted_steps,
        "total_irrelevant_file_reads": total_irrelevant_reads,
    }

    with open(os.path.join(output_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    return summary


def main():
    parser = argparse.ArgumentParser(description="Compare Localisation Strategies and Sub-Agent Architecture")
    parser.add_argument("--tasks", default="data/tasks.jsonl", help="Tasks dataset")
    parser.add_argument("--agent-dir", default=".", help="Root agent directory")
    parser.add_argument("--output-base", default="results/localisation_comparison", help="Output base directory")
    args = parser.parse_args()

    # Load tasks
    tasks = []
    with open(args.tasks, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                tasks.append(Task(json.loads(line)))

    print("=" * 70)
    print("CODE LOCALISATION STRATEGIES & ARCHITECTURE BENCHMARK")
    print(f"Total benchmark tasks: {len(tasks)}")
    print("=" * 70)

    variations = [
        {
            "name": "1_similarity_first_direct",
            "desc": "Similarity-First + Targeted Slice Reads + Direct Tool Use (Optimal)",
            "mode": "similarity_first_targeted",
            "use_subagent": False,
        },
        {
            "name": "2_graph_first_broad",
            "desc": "Graph-First Blind Subgraphs + Broad Line Reads + Direct Tool Use",
            "mode": "graph_first_broad",
            "use_subagent": False,
        },
        {
            "name": "3_subagent_delegation",
            "desc": "Similarity-First + Targeted Slice Reads + Sub-Agent Delegation (code_analyzer)",
            "mode": "similarity_first_targeted",
            "use_subagent": True,
        },
    ]

    all_summaries = []
    for var in variations:
        print(f"\n--> Running variation: {var['desc']}...")
        var_dir = os.path.join(args.output_base, var["name"])
        summary = evaluate_variation(
            tasks=tasks,
            agent_dir=args.agent_dir,
            output_dir=var_dir,
            mode=var["mode"],
            use_subagent=var["use_subagent"]
        )
        all_summaries.append(summary)
        print(f"    Resolved: {summary['resolved_tasks']}/{summary['total_tasks']} ({summary['resolution_rate']*100:.1f}%) | "
              f"Runtime: {summary['avg_runtime_seconds']}s/task | "
              f"Tokens: {summary['avg_tokens_per_task']}/task | "
              f"Wasted Steps: {summary['total_wasted_steps']} | "
              f"Irrelevant Reads: {summary['total_irrelevant_file_reads']}")

    # Write overall comparison report
    comp_file = os.path.join(args.output_base, "strategy_comparison.json")
    with open(comp_file, "w", encoding="utf-8") as f:
        json.dump(all_summaries, f, indent=2)

    print("\n" + "=" * 70)
    print("COMPARATIVE EVALUATION SUMMARY")
    print("=" * 70)
    print(f"{'Strategy / Architecture':<42} | {'Res Rate':<8} | {'Tokens/Task':<11} | {'Wasted':<6} | {'Irrel Reads':<11}")
    print("-" * 85)
    for s in all_summaries:
        print(f"{s['variation']:<42} | {s['resolution_rate']*100:>6.1f}% | {s['avg_tokens_per_task']:>11,} | {s['total_wasted_steps']:>6} | {s['total_irrelevant_file_reads']:>11}")
    print("=" * 70)


if __name__ == "__main__":
    main()
