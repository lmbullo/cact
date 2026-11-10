"""check_plan: the one Verifier that owns a project's check plan, goal and health.

WHAT IT IS
  Verifier = single owner of a verification plan: specs, cheap subset, scope filter, 20s throttle window, kernel check
  cache, Check rendering. roles: goal = authoritative() runs the whole plan on every finish attempt (tool_loop:
  GOAL_UNMET; production goal_stops=False); health = should_run/run the cheap subset after a mutating cell (`[check]`).

ENTRY POINTS
  Verifier(specs=(), cwd="", *, check=None, window=HEALTH_WINDOW, legacy=False, cheap_enabled=True)
    .should_run(paths, now) -> bool (throttle + scope, reserves the window);  .run(paths) -> {cmd: Check} (sets .last, fills .cache())
    .authoritative() -> (ok, Check | None);  .cache() / .invalidate();  .goal_value() -> Check | bool
  re-exported from check_spec: CheckSpec, run_plan, run_specs, run_each; one command is check_run.run_check.
INVARIANTS
  order = contract: cheap fast checks first (smoke, typecheck), so a broken tree is reported before the slow suite;
  first failure is returned, the rest skipped (check_spec.run_plan); unrunnable (check_run.py) is a broken check, never a broken project.
  scope = path prefixes: with paths only specs with a changed path under their scope run; authoritative() passes no paths.
  cheap subset <= once per HEALTH_WINDOW (20s); should_run reserves the window. invalidate() drops cache + last on a mutation,
  the throttle timestamp is not reset.
  check= = benchmark mode: one callable returns a Check (or bool); its results are never injected as check_cache entries.
  legacy=True -> authoritative() answers (ok, None): bare pass/fail, no failure lines (CODE_TUI_LEGACY_CHECK).
  plan = data, never a shell string: detect-checks.ts detects the default; server.ts passes --checks <json>; bench/live.py forwards it.
WHERE TO CHANGE
  parsing/scope/how a plan runs ... check_spec.py (CheckSpec, run_plan, run_specs, run_each)   throttle/cache/goal ... Verifier (this file)
  default workspace plan ... tui/src/detect-checks.ts::detectChecks
"""
from __future__ import annotations

from typing import Any, Callable, Iterable

from .check_run import Check
from .check_spec import CheckSpec, run_each, run_plan, run_specs  # noqa: F401  (re-exported)

HEALTH_WINDOW = 20.0


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
        """True when this Verifier has anything to enforce at finish time."""
        return bool(self.specs) or self.check is not None

    @property
    def cheap_specs(self) -> list[CheckSpec]:
        """The health subset, copied."""
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
        """{cmd: {text, ok}} for the kernel's `check_cache` python helper."""
        return self._cache

    def invalidate(self) -> None:
        """A mutating cell: drop the cached results and the last card check."""
        self._cache = {}
        self.last = None
