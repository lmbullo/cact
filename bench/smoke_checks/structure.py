"""structure: the agent-facing source index and the file-size gate.

WHAT IT IS
  `bench/index.py` turns every documented module into a module row with public symbol ranges.
  This group proves the index resolves a real symbol and enforces the file budget: a new
  source module over `MAX_LINES` fails. The current large modules are an explicit allowlist,
  so the debt is visible and cannot grow silently.

ENTRY POINTS
  run(check)   called by bench/smoke.py after the env/index groups

INVARIANTS
  - The allowlist is the current debt, not a target. Removing a path from it is the split.
  - The index is built from `bench/agent/headers.py`; no second tree walk.
  - A missing symbol is a failure only for a module that should have one; data/string modules
    still have their header row.

WHERE TO CHANGE
  The line budget or the debt list .... MAX_LINES, ALLOW
  The symbol assertion ................. run
"""
from __future__ import annotations

from pathlib import Path

from bench import index

ROOT = Path(__file__).resolve().parents[2]
MAX_LINES = 250

# The current large modules. Each line is a split that is still outstanding; the gate only
# blocks a new file above the budget or a file reappearing after its split.
ALLOW = frozenset({
    "bench/agent/kernel/run_prelude.py",
    "bench/agent/tool_loop.py",
    "tui/src/client.tsx",
    "bench/agent/kernel/prelude_verbs.py",
    "bench/agent/kernel/prelude_read.py",
    "tui/src/server.ts",
    "bench/task_journal.py",
    "bench/smoke_checks/verbs.py",
    "bench/agent/kernel/driver.py",
    "bench/agent/state_loop.py",
    "tui/python/worker.py",
    "bench/agent/verbs.py",
    "bench/agent/kernel/prelude_journal.py",
    "tui/src/list-model.ts",
    "bench/evals/loops.py",
    "bench/agent/kernel/prelude_index.py",
    "bench/smoke_checks/helpers.py",
    "bench/live.py",
    "bench/agent/kernel/process.py",
    "bench/agent/headers.py",
    "tui/src/log-view.tsx",
    "bench/smoke_checks/turns.py",
    "tui/src/board-view.tsx",
    "bench/remote.py",
    "bench/evals/live.py",
    "bench/agent/check_plan.py",
    "bench/smoke_checks/files.py",
    "bench/agent/repl.py",
    "tui/tests/log.test.tsx",
})


def run(check):
    """The index resolves a real symbol, and no new source file exceeds the budget."""
    data = index.build(ROOT)
    modules = data["modules"]
    too_big = [row["path"] for row in modules
               if row["lines"] > MAX_LINES and row["path"] not in ALLOW]
    check("structure: no new source file exceeds %d lines" % MAX_LINES,
          not too_big, ", ".join(too_big)[:300])
    tool_loop = next((row for row in modules if row["path"] == "bench/agent/tool_loop.py"), {})
    names = {symbol["name"] for symbol in tool_loop.get("symbols") or []}
    check("structure: the index resolves ToolLoop.run with a line range",
          "ToolLoop.run" in names, str(sorted(names))[:300])
    check("structure: every indexed module has a header title and a path",
          all(row.get("path") and row.get("title") for row in modules),
          str([row["path"] for row in modules if not row.get("title")])[:300])
