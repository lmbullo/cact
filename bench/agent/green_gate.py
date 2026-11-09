"""green_gate: one cell to finish after the check plan passes; a refusal otherwise.

WHAT IT IS
  Production inline-finish gate. After a green `checks`, exactly one code cell may carry
  `final_answer`; mutation reopens the gate; a foreign gate command is refused with the plan.

ENTRY POINTS
  GreenGate()  .passed / .cells / .invalidate() / .note_checks(cell) / .refusal(name) /
               .suffix(name)   gate state + one-line decisions
  exec_commands(args) -> list[str]        command lines an `exec` argument would run
  is_plan_command(args, specs) -> bool   one of them is exactly a plan command
  is_foreign_gate(args, specs) -> bool   one of them is a gate the plan lacks
  EXEC_REFUSAL, CELL_REFUSAL, FOREIGN_GATE_NOTE, PASS_NOTE ... exact model-facing text

INVARIANTS
  passed opens only on a `checks` payload with ok=true AND >=1 row; empty plan is never green;
  a mutation closes it; nothing else does.
  refusal never crashes and never stops the run: refusal() -> (GREEN, notice); caller writes it
  to CallRecord; the model may still reply with the report.
  cells = code cells spent after the pass; exactly one may carry the finish; resets only on a
  fresh passing `checks` (note_checks).
  is_foreign_gate compares whole words per command line; a plan command is never foreign, so
  running the plan is always allowed.

WHERE TO CHANGE
  refusal wording ............... EXEC_REFUSAL / CELL_REFUSAL / FOREIGN_GATE_NOTE,
                                  GreenGate.refusal, GreenGate.suffix
  foreign-gate test ............. GATE_WORDS, is_foreign_gate
  open/close ................... GreenGate.note_checks / invalidate (called from tool_loop.py)
"""
from __future__ import annotations

import json

from .stops import GREEN

EXEC_REFUSAL = ("[runtime] the check plan already passes for this tree; no more "
                "processes or gates. Reply with the final report, or call "
                "final_answer(...) inside a silent code cell.")
CELL_REFUSAL = ("[runtime] the check plan already passes; this is the final "
                "turn. Reply with the report now.")
FOREIGN_GATE_NOTE = ("[observation] that command is a gate outside this task's check "
                     "plan; the plan is authoritative. Run checks() instead of adding "
                     "a lint/typecheck/format gate.")
PASS_NOTE = "[runtime] the check plan passes; stop running gates and finish."
GATE_WORDS = frozenset({"typecheck", "tsc", "lint", "clippy", "fmt", "format", "ruff"})


class GreenGate:
    """The post-plan gate state: whether the plan passed, and how many cells have spent it."""

    def __init__(self) -> None:
        """Start closed: nothing is known to pass until a `checks` says so."""
        self.passed = False
        self.cells = 0

    def invalidate(self) -> None:
        """A mutation made the pass stale; the next `checks` run decides again."""
        self.passed = False

    def note_checks(self, cell) -> None:
        """Open the gate when a `checks` cell's payload is ok and carries at least one row."""
        try:
            payload = json.loads(cell.result or "{}")
        except (TypeError, ValueError):
            payload = {}
        if payload.get("ok") and payload.get("rows"):
            self.passed = True
            self.cells = 0

    def refusal(self, name: str) -> tuple[str, str] | None:
        """`(GREEN, notice)` when `name` is refused under a passing plan; None when it may run."""
        if not self.passed:
            return None
        if name in ("exec", "checks"):
            return GREEN, EXEC_REFUSAL
        if name in ("code", "python"):
            self.cells += 1
            if self.cells > 1:
                return GREEN, CELL_REFUSAL
        return None

    def suffix(self, name: str) -> str:
        """The `\\n`-prefixed pass line a `checks` observation carries; '' for any other call."""
        return "\n" + PASS_NOTE if self.passed and name == "checks" else ""


def exec_commands(args: dict) -> list[str]:
    """The command lines an `exec` argument would run: argv, each commands row, and shell."""
    commands = []
    if isinstance(args.get("argv"), list):
        commands.append(" ".join(str(part) for part in args["argv"]))
    for row in args.get("commands") or ():
        if isinstance(row, list):
            commands.append(" ".join(str(part) for part in row))
    if args.get("shell"):
        commands.append(str(args["shell"]))
    return commands


def is_plan_command(args: dict, specs) -> bool:
    """True when an `exec` argument is exactly one of the check plan's commands."""
    if not specs:
        return False
    wanted = {" ".join(str(spec.cmd).split()) for spec in specs}
    return any(" ".join(command.split()) in wanted for command in exec_commands(args))


def is_foreign_gate(args: dict, specs) -> bool:
    """True when `exec` runs a lint/typecheck/format gate the plan does not contain."""
    if is_plan_command(args, specs):
        return False
    return any(word in command.lower().split()
               for command in exec_commands(args) for word in GATE_WORDS)
