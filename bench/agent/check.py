"""The goal check: run a project command, parse its failures, decide pass/fail.

WHAT IT IS
  `run_check(cmd, cwd)` runs a shell command (tsc, bun test, pytest, ...) and turns the
  output into a `Check`. `evaluate(goal)` calls a goal callable and normalises the result,
  so the loops never need to know whether a goal returns a bool or a Check.

ENTRY POINTS
  Check(cmd, state, exit_code, duration_ms, stdout, stderr, failures)
    state: "passed" | "failed" | "unrunnable"
    .ok, .text(limit)   the model-facing rendering (unrunnable is called out explicitly)
  run_check(cmd, cwd, timeout=600.0) -> Check
  evaluate(goal) -> (ok, Check | None)

INVARIANTS
  - "unrunnable" (broken command, timeout, crash) is NOT "failed": the text says the check
    itself is broken so the model repairs the project instead of chasing a missing tool.
    The TUI maps an unrunnable check to a failed task only via the loop, never here.
  - `failures` is a bounded, de-duplicated list of {line, text} parsed by `_PATTERNS`
    (bun `(fail)`, pytest `FAILED`, tsc `file(line,col): error TSxxxx`, assertion lines).
  - A goal that raises becomes `unrunnable`, not an exception in the loop.
  - Goals are callables, not command strings: tui/python/worker.py builds a
    `checks.Verifier`, whose `authoritative()` runs the whole plan on every finish attempt.
    Several commands (a cheap health subset plus the full suite) live in checks.py.

WHERE TO CHANGE
  Recognise another test runner's failure lines ... _PATTERNS, _failures
  Several checks / a cheap health subset ......... checks.py (CheckSpec, run_specs)
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
    cmd: str
    state: str
    exit_code: int | None = None
    duration_ms: int = 0
    stdout: str = ""
    stderr: str = ""
    failures: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.state == "passed"

    def text(self, limit: int = 3000) -> str:
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


_PATTERNS = (
    re.compile(r"^\s*\(fail\)\s*(.+)$"),
    re.compile(r"^\s*FAILED\s+(.+)$"),
    re.compile(r"^\s*([^\s:()]+\.\w+\(\d+,\d+\)):\s*(error\s+TS\d+:.*)$"),
    re.compile(r"^\s*([^\s:()]+\.\w+:\d+:\d+):\s*(error:.*)$"),
    re.compile(r"^\s*([^\s:()]+\.\w+:\d+):\s*(error:.*)$"),
    re.compile(r"^\s*(?:AssertionError|Error|Exception):\s*(.+)$"),
)


def _failures(stdout: str, stderr: str, limit: int = 20) -> list[dict]:
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


def run_check(cmd: str, cwd: str, timeout: float = 600.0) -> Check:
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
    if proc.returncode == 0:
        return Check(cmd=cmd, state="passed", exit_code=0, duration_ms=duration,
                     stdout=proc.stdout or "", stderr=proc.stderr or "")
    return Check(cmd=cmd, state="failed", exit_code=proc.returncode, duration_ms=duration,
                 stdout=proc.stdout or "", stderr=proc.stderr or "",
                 failures=_failures(proc.stdout or "", proc.stderr or ""))


def evaluate(goal: Any) -> tuple[bool, Check | None]:
    if goal is None:
        return True, None
    try:
        result = goal()
    except Exception as exc:
        return False, Check(cmd="<goal>", state="unrunnable", stderr=f"{type(exc).__name__}: {exc}")
    if isinstance(result, Check):
        return result.ok, result
    return bool(result), None