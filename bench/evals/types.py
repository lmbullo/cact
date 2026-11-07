"""types: the two records every eval produces, shared by the loop and live suites.

WHAT IT IS
  `Case` is one eval definition: an id, the failure it targets, a title, whether it needs a
  live model, and the callable that runs it. `CaseResult` is its outcome: pass/fail, a detail
  line, and the loop counters the runner aggregates. `Context` is what a case receives: a
  fresh workspace, a client factory and the run options.

ENTRY POINTS
  Case(id, failure, title, kind, fn, tags=())
  CaseResult(ok, detail, stop="", steps=0, usage=None, final="", errors=0, meta=None)
  Context(workspace, client_factory, options)

INVARIANTS
  - `kind` is "loop" for a scripted, offline case and "live" for one that needs a key. The
    runner never mixes the two in one pass, so a keyless run cannot silently skip a live
    case and call the suite green.
   - `failure` is a stable tag (`context`, `tooling`, `mutation`, `finish`, `verification`,
     `cost`, `autonomy`), not prose: the summary groups by it so a regression names the
     broken mechanism.
  - A case that raises is reported as a failed CaseResult by the runner, never as a crash.

WHERE TO CHANGE
  A new tag ............... the `failure` values the catalog uses
  A result field .......... CaseResult (then the runner's row)
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
