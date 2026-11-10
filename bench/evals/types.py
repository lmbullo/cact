"""types: the two records every eval produces, shared by the loop and live suites.
WHAT IT IS
  Case = id, failure tag, title, kind, run callable. CaseResult = pass/fail, detail and loop
  counters. Context = fresh workspace, client factory, run options.
ENTRY POINTS
  Case(id, failure, title, kind, fn, tags=())
  CaseResult(ok, detail, stop="", steps=0, usage=None, final="", errors=0, meta=None)
  Context(workspace, client_factory, options)
INVARIANTS
  kind is "loop" for scripted/offline or "live" for a key; the runner never mixes them.
  failure is a stable tag, not prose: the summary groups by it.
  a case that raises is reported as a failed CaseResult, never a crash.
WHERE TO CHANGE
  a new tag ....... the `failure` values the catalog uses
  a result field .. CaseResult (then the runner's row)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class Context:
    """What a case gets: one fresh workspace, a client factory, and the run options."""

    workspace: Path
    client_factory: Callable[[], Any]
    options: dict = field(default_factory=dict)


@dataclass
class CaseResult:
    """One case outcome, in the same counters a trace row uses."""

    ok: bool
    detail: str
    stop: str = ""
    steps: int = 0
    usage: dict = field(default_factory=dict)
    final: str = ""
    errors: int = 0
    meta: dict = field(default_factory=dict)


@dataclass
class Case:
    """One eval definition: what it targets, how it runs, and whether it needs a model."""

    id: str
    failure: str
    title: str
    kind: str
    fn: Callable[[Context], CaseResult]
    tags: tuple[str, ...] = ()
