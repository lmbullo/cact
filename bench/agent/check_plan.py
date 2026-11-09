"""check_plan: an ordered plan of project commands + the one Verifier that owns it.

WHAT IT IS
  CheckSpec.parse(raw) reads the plan from a command string or a JSON list of
  {name, cmd, cheap, scope}. run_plan(specs, cwd, paths) -> (Check | None, {cmd: Check}): first
  failure + per-command results (None when no spec applied). run_specs -> the combined verdict;
  synthetic `passed` = every command that ran passed. One command = check_run.run_check.
  Verifier = single owner of a verification plan: specs, cheap subset, scope filter, 20s
  throttle window, kernel check cache, Check rendering. One object, two roles:
    goal    authoritative() runs the whole plan on every finish attempt (tool_loop:
            GOAL_UNMET); production keeps goal_stops=False.
    health  should_run(paths, now) decides whether the cheap subset may run after a mutating
            cell; run(paths) runs it, fills `last` + the cache: the `[check]` line of the
            state card.

ENTRY POINTS
  CheckSpec(name, cmd, cheap=False, scope=())  .parse(raw) -> list[CheckSpec]
                                               .applies(paths) filters by changed paths
  run_plan(specs, cwd, paths=None) -> (Check | None, {cmd: Check})
  run_specs(specs, cwd, paths=None) -> Check | None   combined verdict
  run_each(specs, cwd, paths=None, name=None, cache=None) -> list[dict]
                                                   one {name, ok, exit, first_line} row per
                                                   spec, reusing a cached green command
  Verifier(specs=(), cwd="", *, check=None, window=HEALTH_WINDOW, legacy=False,
           cheap_enabled=True)
    .should_run(paths, now) -> bool   throttle + scope decision (reserves the window)
    .run(paths) -> {cmd: Check}        cheap subset; sets .last, fills .cache()
    .authoritative() -> (ok, Check | None)   whole plan, every finish attempt
    .cache() / .invalidate()           kernel injection / drop cache + last on mutation
    .goal_value()                      Check | bool for ReplLoop's evaluate

INVARIANTS
  order = contract: cheap, fast checks first (smoke, typecheck), so a broken tree is reported
  before the slow suite. First failure returned; the rest skipped.
  unrunnable (check_run.py) survives the plan: a command that cannot run is a broken check, never
  a broken project.
  scope = path prefixes. With `paths` (cheap run) a spec runs only if one changed path is under
  its scope: a docs-only edit does not typecheck the TUI. authoritative() passes no paths: the
  goal runs every spec.
  cheap subset <= once per HEALTH_WINDOW (20s); should_run decides and reserves the window.
  invalidate() drops the cache and `last` on a mutating cell; the throttle timestamp is not
  reset: a mutation inside the window clears the card without re-running.
  check= = benchmark mode: one callable returning a Check (or bool) stands in for the plan; its
  results are never injected as check_cache entries (no command to key them by).
  legacy=True -> authoritative() answers (ok, None): loop rejection has bare pass/fail, no
  failure lines. Production keeps it off (CODE_TUI_LEGACY_CHECK).
  plan = data, never a shell string. detect-checks.ts detects the default plan
  (CODE_TUI_CHECKS, else CODE_TUI_CHECK, else bench/smoke.py + package.json scripts); server.ts
  passes it to worker.py as `--checks <json>`; bench/live.py forwards `--checks` the same way.

WHERE TO CHANGE
  plan parsing ........................ CheckSpec.parse
  scope matching ...................... CheckSpec.applies
  how a plan runs / returns ........... run_plan / run_specs / run_each
  throttle / cache / goal policy ...... Verifier
  default workspace plan .............. tui/src/detect-checks.ts::detectChecks
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

from .check_run import Check, run_check

HEALTH_WINDOW = 20.0


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
            rows.append({"name": spec.name, "ok": True, "exit": 0, "first_line": ""})
            continue
        result = run_check(spec.cmd, cwd)
        rows.append({"name": spec.name, "ok": bool(result.ok),
                     "exit": result.exit_code,
                     "first_line": "" if result.ok else result.first_failure()})
        if isinstance(cache, dict):
            cache[spec.cmd] = {"text": result.text(600), "ok": bool(result.ok)}
    return rows


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
