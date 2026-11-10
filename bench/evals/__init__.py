"""evals: the agent's failure-mode evaluation suite: loop contracts and live runs.
WHAT IT IS
  loops.py runs deterministic scripted contracts; live.py runs the same mechanisms against a
  real model with hidden oracles. runner.py is the one registry and executor; fixtures.py
  builds inputs; oracle.py owns the answers; types.py is the shared row shape.
ENTRY POINTS
  python3 bench/eval.py                 offline loop contracts (the gate)
  python3 bench/eval.py --live          live agent failures (needs a key)
  from bench.evals.runner import REGISTRY, run
  LOOP_CASES / LIVE_CASES               the two case lists
INVARIANTS
  offline and live never mix; a keyless run cannot report a green live suite.
  every case gets a fresh temp workspace and kernel; a case exception is a failed row.
  the oracle is the verdict; the model's prose is evidence, never the pass condition.
  results are append-only under results/, with tokens/pass in every report.
WHERE TO CHANGE
  a loop mechanism ......... loops.py (and the agent module it exercises)
  a live failure ........... live.py plus an oracle
  the registry or CLI ...... runner.py
"""
from __future__ import annotations

from .live import LIVE_CASES
from .loops import LOOP_CASES
from .runner import REGISTRY, Report, run
from .types import Case, CaseResult, Context

__all__ = ["LOOP_CASES", "LIVE_CASES", "REGISTRY", "Report", "run",
           "Case", "CaseResult", "Context"]
