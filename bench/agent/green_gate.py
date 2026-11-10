"""green_gate: stop the verification tail after a passing check plan.

WHAT IT IS
  Post-plan gate: after a green `checks`, further `exec` and `checks` are refused. Code cells and
  typed mutations stay open; a mutation closes the gate and the next checks decides again.

ENTRY POINTS
  GreenGate()  .passed / .cells / .invalidate() / .note_checks(cell) / .refusal(name) / .suffix(name)
  exec_commands(args) -> list[str]; plan_command(args, specs); is_plan_command(args, specs);
  is_foreign_gate(args, specs)
  EXEC_REFUSAL, FOREIGN_GATE_NOTE, PASS_NOTE ... exact model-facing text

INVARIANTS
  passed opens only on a checks payload with ok=true and >=1 row; a mutation closes it.
  only exec/checks are refused; code/python are never refused; cells counts them for the record.
  is_foreign_gate compares whole words per command; a plan command is never foreign.

WHERE TO CHANGE
  refusal text ... EXEC_REFUSAL / FOREIGN_GATE_NOTE / GreenGate.refusal / .suffix
  foreign gate ... GATE_WORDS / is_foreign_gate
  open/close .... GreenGate.note_checks / invalidate (called from tool_calls.py)
"""
from __future__ import annotations

import json

from .stops import GREEN

EXEC_REFUSAL = ("[runtime] the check plan already passes for this tree; no more "
                "processes or gates. Reply with the final report, or call "
                "final_answer(...) inside a silent code cell.")
FOREIGN_GATE_NOTE = ("[observation] that command is a gate outside this task's check "
                     "plan; the plan is authoritative. Run checks() instead of adding "
                     "a lint/typecheck/format gate.")
PASS_NOTE = "[runtime] the check plan passes; stop running gates and finish."
GATE_WORDS = frozenset({"typecheck", "tsc", "lint", "clippy", "fmt", "format", "ruff"})


class GreenGate:
    """The post-plan gate state: whether the plan passed, and how many code cells ran after it."""

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
        """`(GREEN, notice)` when `name` is a verification call under a passing plan."""
        if not self.passed:
            return None
        if name in ("code", "python"):
            self.cells += 1
            return None
        if name in ("exec", "checks"):
            return GREEN, EXEC_REFUSAL
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


def plan_command(args: dict, specs):
    """The check spec an `exec` argument exactly runs, or None."""
    commands = [" ".join(command.split()) for command in exec_commands(args)]
    if len(commands) != 1 or not specs:
        return None
    for spec in specs:
        if " ".join(str(spec.cmd).split()) == commands[0]:
            return spec
    return None


def is_plan_command(args: dict, specs) -> bool:
    """True when an `exec` argument is exactly one of the check plan's commands."""
    return plan_command(args, specs) is not None


def is_foreign_gate(args: dict, specs) -> bool:
    """True when `exec` runs a lint/typecheck/format gate the plan does not contain."""
    if is_plan_command(args, specs):
        return False
    return any(word in command.lower().split()
               for command in exec_commands(args) for word in GATE_WORDS)
