"""suites: the suites are importable and their ground truth is intact.

WHAT IT IS
  The cheapest possible contract check: every benchmark module imports and still exposes the
  task count and ids the runner and the TUI assume (read/mutation, edge, harness, sandbox),
  plus the two grading helpers the harness uses for the docs task.

ENTRY POINTS
  run(check)   called first by bench/smoke.py::main

INVARIANTS
  - Task counts are asserted, so adding a task without updating this file fails loudly here
    rather than silently changing a suite's shape.
  - Ground truth is computed at import time; this checks it is non-zero, not its value.

WHERE TO CHANGE
  A new suite ............................ the count check for it
  A task count ........................... the expected number in the same check
"""
from __future__ import annotations

import bench.edge as edge
import bench.harness as harness
import bench.sandbox as sandbox
import bench.tasks as tasks


def run(check):
    """Every suite imports, its task ids/counts are intact, and its ground truth is computed."""
    # --- suites are importable, their ground truth is intact -----------------------------
    check("tasks: read suite", len(tasks.TASKS) == 5, str(len(tasks.TASKS)))
    check("tasks: mutation suite", len(tasks.MUTATION_TASKS) == 3, str(len(tasks.MUTATION_TASKS)))
    check("tasks: by_id", tasks.by_id("T1_count").id == "T1_count")
    check("edge: edge suite", len(edge.EDGE_TASKS) == 10, str(len(edge.EDGE_TASKS)))
    check("harness: loop suite", len(harness.HARNESS_TASKS) == 8, str(len(harness.HARNESS_TASKS)))
    check("sandbox: one mutable feature probe",
          len(sandbox.TASKS) == 1 and sandbox.by_id("S1_feature").mutates,
          str(len(sandbox.TASKS)))

    class _PathRun:
        errors = 0
        repeats = 0
        turns = [{"calls": [{"name": "read", "args": {"path": harness.CURRENT_PATH}}]}]

    class _GuessRun:
        errors = 0
        repeats = 0
        turns = [{"calls": [{"name": "python", "args": {"code": "print('guess')"}}]}]
    ok, _ = harness.grade_current_docs(harness.DOC_FACT, _PathRun())
    check("harness: docs task accepts the documented path", ok, "read CURRENT.md")
    wrong, why = harness.grade_current_docs(harness.DOC_FACT, _GuessRun())
    check("harness: docs task rejects a right answer from prior knowledge",
          not wrong and "never inspected" in why, why)
    check("edge: by_id", edge.by_id("E1_tests").id == "E1_tests")
    check("edge: ground truth computed", edge.TOTAL_CENTS > 0 and edge.RECORDS > 0)
