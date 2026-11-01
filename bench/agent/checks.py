"""The check plan: an ordered list of project commands, cheap health checks first.

WHAT IT IS
  `CheckSpec.parse(raw)` reads the plan from a command string or a JSON list of
  `{name, cmd, cheap}`; `run_specs(specs, cwd)` runs it and returns the first failure as a
  `Check`, or a synthetic `passed` when every command passes.

  One plan, two roles:
    goal    the whole plan is what `final_answer` must satisfy (tool_loop: GOAL_UNMET)
    health  the `cheap` subset re-runs after a mutating cell and shows up as the `[check]`
            line of the state card (tool_loop `health=`, state_loop `_observe`)

ENTRY POINTS
  CheckSpec(name, cmd, cheap=False)       .parse(raw) -> list[CheckSpec]
  run_specs(specs, cwd) -> Check | None   None when the plan is empty

INVARIANTS
  - Order is the contract: put the cheap, fast checks (smoke, typecheck) first so a broken
    tree is reported before the slow suite runs. The first failure is returned and the rest
    are skipped.
  - `unrunnable` from check.py survives the plan: a command that cannot run is a broken
    check, never a broken project.
  - A plan is data, never a shell string. server.ts detects the default plan
    (CODE_TUI_CHECKS, else CODE_TUI_CHECK, else bench/smoke.py + package.json scripts) and
    passes it to worker.py as `--checks <json>`; bench/live.py forwards `--checks` the same way.

WHERE TO CHANGE
  Plan parsing .................... CheckSpec.parse
  How a plan runs and what it returns ... run_specs
  The default plan for a workspace ...... tui/src/server.ts::detectChecks
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .check import Check, run_check


@dataclass
class CheckSpec:
    name: str
    cmd: str
    cheap: bool = False

    @staticmethod
    def parse(raw: Any) -> list["CheckSpec"]:
        if isinstance(raw, str):
            return [CheckSpec(name="check", cmd=raw, cheap=False)] if raw.strip() else []
        if not isinstance(raw, list):
            return []
        specs: list[CheckSpec] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            cmd = str(item.get("cmd") or item.get("command") or "").strip()
            if not cmd:
                continue
            specs.append(CheckSpec(
                name=str(item.get("name") or cmd[:40]),
                cmd=cmd,
                cheap=bool(item.get("cheap", False)),
            ))
        return specs


def run_specs(specs: Iterable[CheckSpec], cwd: str) -> Check | None:
    specs = list(specs)
    if not specs:
        return None
    for spec in specs:
        result = run_check(spec.cmd, cwd)
        if not result.ok:
            if result.state == "unrunnable":
                return Check(cmd=f"{spec.name}: {spec.cmd}", state="unrunnable",
                             stdout=result.stdout, stderr=result.stderr)
            return Check(cmd=f"{spec.name}: {spec.cmd}", state="failed",
                         exit_code=result.exit_code, duration_ms=result.duration_ms,
                         stdout=result.stdout, stderr=result.stderr, failures=result.failures)
    return Check(cmd=", ".join(spec.name for spec in specs), state="passed",
                 exit_code=0, duration_ms=0)
