"""check_run: one project command -> one `Check`. The unit; the plan lives in check_plan.py.

WHAT IT IS
  `run_check(cmd, cwd)` runs a shell command (tsc, bun test, pytest, ...) and turns the
  output into a `Check`. `evaluate(goal)` calls a goal callable and normalises the result,
  so the loops never need to know whether a goal returns a bool or a Check.

ENTRY POINTS
  Check(cmd, state, exit_code, duration_ms, stdout, stderr, failures)
    state: "passed" | "failed" | "unrunnable"
    .ok, .text(limit)   the model-facing rendering (unrunnable is called out explicitly)
  run_check(cmd, cwd, timeout=600.0) -> Check
  from_output(cmd, exit_code, stdout, stderr, duration_ms) -> Check  an already-run command
  evaluate(goal) -> (ok, Check | None)

INVARIANTS
  - "unrunnable" (broken command, timeout, crash) is NOT "failed": the text says the check
    itself is broken so the model repairs the project instead of chasing a missing tool.
    The TUI maps an unrunnable check to a failed task only via the loop, never here.
  - `failures` is a bounded, de-duplicated list of {line, text} parsed by `_PATTERNS`
    (bun `(fail)`, pytest `FAILED`, tsc `file(line,col): error TSxxxx`, assertion lines).
  - A goal that raises becomes `unrunnable`, not an exception in the loop.
  - Goals are callables, not command strings: tui/python/worker.py builds a
    `check_plan.Verifier`, whose `authoritative()` runs the whole plan on every finish attempt.
    Several commands (a cheap health subset plus the full suite) live in check_plan.py.

WHERE TO CHANGE
  Recognise another test runner's failure lines ... _PATTERNS, _failures
  Several checks / a cheap health subset ......... check_plan.py (CheckSpec, run_specs)
  How a check is executed ........................ run_check (timeout, bash, cwd)
  What the model is told on failure .............. Check.text
"""
from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Check:
    """One finished command run. `state` is passed | failed | unrunnable; nothing else."""

    cmd: str
    state: str
    exit_code: int | None = None
    duration_ms: int = 0
    stdout: str = ""
    stderr: str = ""
    failures: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True only for a run that exited 0."""
        return self.state == "passed"

    def first_failure(self) -> str:
        """The first failing line `_PATTERNS` found, else the first non-empty output line."""
        for hit in self.failures or ():
            text = str(hit.get("text") or "").strip() if isinstance(hit, dict) else str(hit).strip()
            if text:
                return text
        for stream in (self.stderr, self.stdout):
            for line in str(stream or "").splitlines():
                if line.strip():
                    return line.strip()
        return ""

    def text(self, limit: int = 3000) -> str:
        """The model-facing line(s): verdict first, then the parsed failure lines or a tail."""
        if self.state == "passed":
            return f"{self.cmd}: passed ({self.duration_ms}ms)"
        if self.state == "unrunnable":
            detail = (self.stderr or self.stdout or "").strip()[-limit:]
            return f"{self.cmd}: could not run. This is not a failed test; the check itself is broken.\n{detail}"
        if self.exit_code is None:
            head = f"{self.cmd}: failed (not run)"
        else:
            head = f"{self.cmd}: failed (exit {self.exit_code}, {self.duration_ms}ms)"
        if self.failures:
            body = "\n".join(f"  - {hit['text']}" for hit in self.failures)
            return f"{head}\n{body}"
        detail = (self.stderr or self.stdout or "").strip()[-limit:]
        return f"{head}\n{detail}"


# Test-runner failure lines, one pattern per dialect. Order matters: first match wins.
_PATTERNS = (
    re.compile(r"^\s*\(fail\)\s*(.+)$"),
    re.compile(r"^\s*FAILED\s+(.+)$"),
    re.compile(r"^\s*([^\s:()]+\.\w+\(\d+,\d+\)):\s*(error\s+TS\d+:.*)$"),
    re.compile(r"^\s*([^\s:()]+\.\w+:\d+:\d+):\s*(error:.*)$"),
    re.compile(r"^\s*([^\s:()]+\.\w+:\d+):\s*(error:.*)$"),
    re.compile(r"^\s*(?:AssertionError|Error|Exception):\s*(.+)$"),
)


def _failures(stdout: str, stderr: str, limit: int = 20) -> list[dict]:
    """De-duplicated {line, text} rows, stderr first, capped at `limit`."""
    hits: list[dict] = []
    seen: set[str] = set()
    for number, line in enumerate((stderr + "\n" + stdout).splitlines(), 1):
        row = line.rstrip()
        if not row:
            continue
        for pattern in _PATTERNS:
            match = pattern.match(row)
            if not match:
                continue
            text = " ".join(part for part in match.groups() if part).strip()
            if "0 fail" in text.lower() or text in seen:
                break
            seen.add(text)
            hits.append({"line": number, "text": text[:400]})
            break
        if len(hits) >= limit:
            break
    return hits


def from_output(cmd: str, exit_code: int | None, stdout: str = "", stderr: str = "",
                duration_ms: int = 0) -> Check:
    """Classify a command someone else ran (bench/live.py --sandbox daytona) like run_check does."""
    if exit_code is None:
        return Check(cmd=cmd, state="unrunnable", duration_ms=duration_ms,
                     stdout=stdout, stderr=stderr)
    if exit_code == 0:
        return Check(cmd=cmd, state="passed", exit_code=0, duration_ms=duration_ms,
                     stdout=stdout, stderr=stderr)
    return Check(cmd=cmd, state="failed", exit_code=exit_code, duration_ms=duration_ms,
                 stdout=stdout, stderr=stderr, failures=_failures(stdout, stderr))


def run_check(cmd: str, cwd: str, timeout: float = 600.0) -> Check:
    """Run `cmd` in `cwd` through bash and classify it; never raises."""
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd, shell=True, cwd=cwd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, executable="/bin/bash",
        )
    except subprocess.TimeoutExpired as exc:
        return Check(cmd=cmd, state="unrunnable", duration_ms=int((time.perf_counter() - started) * 1000),
                     stdout=(exc.stdout or "") if isinstance(exc.stdout, str) else "",
                     stderr=f"check timed out after {timeout:.0f}s")
    except OSError as exc:
        return Check(cmd=cmd, state="unrunnable", duration_ms=int((time.perf_counter() - started) * 1000),
                     stderr=f"{type(exc).__name__}: {exc}")
    duration = int((time.perf_counter() - started) * 1000)
    return from_output(cmd, proc.returncode, proc.stdout or "", proc.stderr or "", duration)


def evaluate(goal: Any) -> tuple[bool, Check | None]:
    """Normalise a goal callable to (ok, Check|None); `None` means no goal, so (True, None)."""
    if goal is None:
        return True, None
    try:
        result = goal()
    except Exception as exc:
        return False, Check(cmd="<goal>", state="unrunnable", stderr=f"{type(exc).__name__}: {exc}")
    if isinstance(result, Check):
        return result.ok, result
    return bool(result), None
