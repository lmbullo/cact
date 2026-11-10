"""check_spec: the plan entries (CheckSpec) and the three ways a plan runs.
WHAT IT IS
  CheckSpec.parse(raw) reads the plan from a command string or a JSON list of
  {name, cmd, cheap, scope}; this module only runs what it is given, never owns finish policy.

ENTRY POINTS
  CheckSpec(name, cmd, cheap=False, scope=())  .parse(raw) -> list[CheckSpec]; .applies(paths)
  run_plan(specs, cwd, paths=None) -> (Check | None, {cmd: Check});  run_specs(...) -> combined verdict
  run_each(specs, cwd, paths=None, name=None, cache=None) -> list[dict]  {name, ok, exit,
    first_line, detail?} row per spec, reusing a cached green command

INVARIANTS
  order = contract: the plan runs in the order given (cheap first), so a broken tree is reported
  before the slow suite; first failure returned, the rest skipped.
  unrunnable (check_run.py) survives the plan: a command that cannot run is a broken check,
  never a broken project.
  scope = path prefixes: with `paths` a spec runs only if a changed path is under its scope
  (a docs-only edit does not typecheck the TUI); no `paths` means every spec.
  plan = data, never a shell string: a parsed command plus its name, cheap flag and scope.

WHERE TO CHANGE
  plan parsing / scope ............... CheckSpec.parse; CheckSpec.applies
  how a plan runs / returns ........... run_plan / run_specs / run_each
  throttle / cache / goal policy ...... check_verifier.Verifier;  one command ... check_run.run_check
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .check_run import Check, run_check


@dataclass
class CheckSpec:
    """One plan entry: a named command, whether it belongs to the cheap subset, and its scope."""

    name: str
    cmd: str
    cheap: bool = False
    scope: tuple[str, ...] = ()

    def applies(self, paths: Iterable[str] | None) -> bool:
        """Does this spec cover the changed `paths`? No scope, or no path list, means yes."""
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
        """A command string or a JSON-ish list of dicts -> specs; anything else is empty."""
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
    """Run the applicable specs in order; stop at the first failure. (None, {}) when none apply."""
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
    """run_plan's verdict only: the first failing Check, or a synthetic passed, or None."""
    return run_plan(specs, cwd, paths)[0]


def run_each(specs: Iterable[CheckSpec], cwd: str,
             paths: Iterable[str] | None = None, name: str | None = None,
             cache: dict | None = None) -> list[dict]:
    """One `{name, ok, exit, first_line}` row per applicable spec, in plan order.

    `name` selects one spec. A command whose cache entry is green for this tree is reused, so a
    second `checks()` call never re-runs a spec the cheap check already passed. Every other
    command goes through `run_check`, exactly as the goal and the cheap subset do.
    """
    rows: list[dict] = []
    seen: set[str] = set()
    for spec in specs:
        if name is not None and spec.name != name:
            continue
        if not spec.applies(paths) or spec.cmd in seen:
            continue
        seen.add(spec.cmd)
        cached = (cache or {}).get(spec.cmd)
        if isinstance(cached, dict) and cached.get("ok"):
            row = {"name": spec.name, "ok": True, "exit": 0, "first_line": ""}
            if cached.get("detail"):
                row["detail"] = cached["detail"]
            rows.append(row)
            continue
        result = run_check(spec.cmd, cwd)
        detail = result.summary() if result.ok else ""
        row = {"name": spec.name, "ok": bool(result.ok),
               "exit": result.exit_code,
               "first_line": "" if result.ok else result.first_failure()}
        if detail:
            row["detail"] = detail
        rows.append(row)
        if isinstance(cache, dict):
            cache[spec.cmd] = {"text": result.text(600), "ok": bool(result.ok),
                               "detail": detail}
    return rows
