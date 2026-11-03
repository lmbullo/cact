"""The check plan and its Verifier: an ordered list of project commands, cheap checks first.

WHAT IT IS
  `CheckSpec.parse(raw)` reads the plan from a command string or a JSON list of
  `{name, cmd, cheap, scope}`; `run_plan(specs, cwd, paths)` runs it and returns the first
  failure as a `Check` plus the per-command results (or `None` when no spec applied),
  `run_specs` returns just the combined verdict, and a synthetic `passed` means every
  command that ran passed.

  `Verifier` is the single owner of a verification plan: the specs, the cheap subset, the
  `scope` filter, the 20s throttle window, the check cache handed to the kernel, and the
  `Check` rendering. One object, two roles:
    goal    `authoritative()` runs the whole plan on every finish attempt (tool_loop:
            GOAL_UNMET); production keeps `goal_stops=False`.
    health  `should_run(paths, now)` decides whether the cheap subset may run after a
            mutating cell; `run(paths)` runs it and fills `last` + the cache, which is what
            the `[check]` line of the state card renders.

ENTRY POINTS
  CheckSpec(name, cmd, cheap=False, scope=())  .parse(raw) -> list[CheckSpec]
                                               .applies(paths) filters by changed paths
  run_plan(specs, cwd, paths=None) -> (Check | None, {cmd: Check})
  run_specs(specs, cwd, paths=None) -> Check | None   the combined verdict
  Verifier(specs=(), cwd="", *, check=None, window=HEALTH_WINDOW, legacy=False,
           cheap_enabled=True)
    .should_run(paths, now) -> bool   throttle + scope decision (reserves the window)
    .run(paths) -> {cmd: Check}        cheap subset; sets `.last`, fills `.cache()`
    .authoritative() -> (ok, Check | None)   whole plan, every finish attempt
    .cache() / .invalidate()           kernel injection / drop cache + last on mutation
    .goal_value()                      Check | bool for ReplLoop's `evaluate`

INVARIANTS
  - Order is the contract: put the cheap, fast checks (smoke, typecheck) first so a broken
    tree is reported before the slow suite runs. The first failure is returned and the rest
    are skipped.
  - `unrunnable` from check.py survives the plan: a command that cannot run is a broken
    check, never a broken project.
  - `scope` is a list of path prefixes; when a caller passes `paths` (the cheap run), a
    spec runs only if one changed path is under its scope, so a docs-only edit does not
    typecheck the TUI. `authoritative()` passes no paths, so the goal runs every spec.
  - The cheap subset runs at most every `HEALTH_WINDOW` (20s); `should_run` both decides and
    reserves the window, matching the old inline throttle.
  - `invalidate()` drops the cache and `last` on a mutating cell; the throttle timestamp is
    not reset, so a mutation inside the window clears the card without re-running.
  - `check=` is the benchmark mode: a single callable that returns a `Check` (or a bool)
    stands in for the plan, and its results are never injected as `check_cache` entries
    (there is no command to key them by).
  - `legacy=True` makes `authoritative()` answer `(ok, None)`, so the loop's rejection has a
    bare pass/fail and no failure lines. Production keeps it off.
  - A plan is data, never a shell string. detect-checks.ts detects the default plan
    (CODE_TUI_CHECKS, else CODE_TUI_CHECK, else bench/smoke.py + package.json scripts);
    server.ts passes it to worker.py as `--checks <json>`, and bench/live.py forwards
    `--checks` the same way.

WHERE TO CHANGE
  Plan parsing .................... CheckSpec.parse
  Scope matching .................. CheckSpec.applies
  How a plan runs and what it returns ... run_plan / run_specs
  Throttle / cache / goal policy ... Verifier
  The default plan for a workspace ...... tui/src/detect-checks.ts::detectChecks
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

from .check import Check, run_check

HEALTH_WINDOW = 20.0


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


class Verifier:
    """Owns one verification plan: goal, cheap health subset, throttle, cache, rendering.

    `specs` is a `CheckSpec` plan (production) or `check=` is one callable (the benchmark
    suite's per-task oracle). Both feed the same intent methods, so the loop never touches
    a spec, a timer or a cache itself.
    """

    def __init__(
        self,
        specs: Iterable[CheckSpec] | None = None,
        cwd: str = "",
        *,
        check: Callable[[], Any] | None = None,
        window: float = HEALTH_WINDOW,
        legacy: bool = False,
        cheap_enabled: bool = True,
    ) -> None:
        self.specs = list(specs or ())
        self.cwd = cwd
        self.check = check
        self.window = window
        self.legacy = legacy
        self._cheap = [spec for spec in self.specs if spec.cheap] if cheap_enabled else []
        if check is not None and cheap_enabled:
            self._cheap = [CheckSpec(name="check", cmd="<check>", cheap=True)]
        self.last: Check | None = None
        self._cache: dict[str, dict] = {}
        self._last_at = float("-inf")

    @property
    def has_goal(self) -> bool:
        return bool(self.specs) or self.check is not None

    @property
    def cheap_specs(self) -> list[CheckSpec]:
        return list(self._cheap)

    def should_run(self, paths: Iterable[str] | None, now: float) -> bool:
        """May the cheap subset run for `paths` at `now`? Reserves the window when yes."""
        paths = list(paths or ())
        if not self._cheap:
            return False
        if not any(spec.applies(paths) for spec in self._cheap):
            return False
        if now - self._last_at < self.window:
            return False
        self._last_at = now
        return True

    def run(self, paths: Iterable[str] | None = None) -> dict[str, Check]:
        """Run the cheap subset and return its per-command results; fill `last` and the cache."""
        if self.check is not None:
            result = self.check()
            check = result if isinstance(result, Check) else Check(
                cmd="check", state="passed" if result else "failed")
            self.last = check
            return {check.cmd: check}
        check, results = run_plan(self._cheap, self.cwd, paths)
        self.last = check
        self._cache = {cmd: {"text": item.text(600), "ok": bool(item.ok)}
                       for cmd, item in results.items() if item is not None}
        return results

    def authoritative(self) -> tuple[bool, Check | None]:
        """The finish gate: the whole plan, every attempt."""
        if self.check is not None:
            try:
                result = self.check()
            except Exception as exc:  # noqa: BLE001 - a broken goal is unrunnable, not a crash
                check = Check(cmd="<goal>", state="unrunnable",
                              stderr=f"{type(exc).__name__}: {exc}")
                return False, (None if self.legacy else check)
            check = result if isinstance(result, Check) else Check(
                cmd="check", state="passed" if result else "failed")
            return check.ok, (None if self.legacy else check)
        if not self.specs:
            return True, None
        check = run_specs(self.specs, self.cwd)
        ok = bool(check and check.ok)
        return ok, (None if self.legacy else check)

    def goal_value(self) -> Any:
        """A `Check | bool` for a ReplLoop `evaluate(goal)` call."""
        ok, check = self.authoritative()
        if self.legacy:
            return ok
        return check if check is not None else ok

    def cache(self) -> dict[str, dict]:
        return self._cache

    def invalidate(self) -> None:
        """A mutating cell: drop the cached results and the last card check."""
        self._cache = {}
        self.last = None
