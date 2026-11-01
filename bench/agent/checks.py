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
  CheckSpec(name, cmd, cheap=False, scope=())  .parse(raw) -> list[CheckSpec]
                                               .applies(paths) filters by changed paths
  run_plan(specs, cwd, paths=None) -> (Check | None, {cmd: Check})
  run_specs(specs, cwd, paths=None) -> Check | None   the combined verdict

INVARIANTS
  - Order is the contract: put the cheap, fast checks (smoke, typecheck) first so a broken
    tree is reported before the slow suite runs. The first failure is returned and the rest
    are skipped.
  - `unrunnable` from check.py survives the plan: a command that cannot run is a broken
    check, never a broken project.
  - `scope` is a list of path prefixes; a spec runs only when the cell changed a path under
    one of them, so a docs-only edit does not typecheck the TUI.
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
    scope: tuple[str, ...] = ()

    def applies(self, paths: Iterable[str] | None) -> bool:
        if not self.scope or paths is None:
            return True
        for path in paths:
            text = str(path).lstrip("./")
            for prefix in self.scope:
                if prefix in ("", "."):
                    return True
                if text == prefix or text.startswith(prefix + "/"):
                    return True
        return False

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
            scope = item.get("scope") or item.get("paths") or []
            if isinstance(scope, str):
                scope = [scope]
            specs.append(CheckSpec(
                name=str(item.get("name") or cmd[:40]),
                cmd=cmd,
                cheap=bool(item.get("cheap", False)),
                scope=tuple(str(entry).strip().strip("/") for entry in scope if str(entry).strip()),
            ))
        return specs


def run_plan(specs: Iterable[CheckSpec], cwd: str,
             paths: Iterable[str] | None = None) -> tuple[Check | None, dict[str, Check]]:
    ran: list[CheckSpec] = []
    results: dict[str, Check] = {}
    for spec in specs:
        if not spec.applies(paths):
            continue
        if spec.cmd in results:
            continue
        result = run_check(spec.cmd, cwd)
        results[spec.cmd] = result
        ran.append(spec)
        if not result.ok:
            if result.state == "unrunnable":
                return Check(cmd=f"{spec.name}: {spec.cmd}", state="unrunnable",
                             stdout=result.stdout, stderr=result.stderr), results
            return Check(cmd=f"{spec.name}: {spec.cmd}", state="failed",
                         exit_code=result.exit_code, duration_ms=result.duration_ms,
                         stdout=result.stdout, stderr=result.stderr, failures=result.failures), results
    if not ran:
        return None, results
    return Check(cmd=", ".join(spec.name for spec in ran), state="passed",
                 exit_code=0, duration_ms=0), results


def run_specs(specs: Iterable[CheckSpec], cwd: str,
              paths: Iterable[str] | None = None) -> Check | None:
    return run_plan(specs, cwd, paths)[0]
