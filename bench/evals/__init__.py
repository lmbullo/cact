"""evals: the agent's failure-mode evaluation suite, split into loop contracts and live runs.

WHAT IT IS
  `loops.py` runs the turn machine, kernel, journal, compaction, custom tools, skills,
  delegation and the skeptic with a scripted model — no key, no network, deterministic.
  `live.py` runs the same mechanisms against a real model with hidden oracles: the contract
  behind a green visible test, latent bugs, a fact across compaction, an on-demand skill, a
  skeptical finish and delegation. `runner.py` is the one registry and executor; `fixtures.py`
  builds inputs; `oracle.py` owns the answers; `types.py` is the shared row shape.

ENTRY POINTS
  python3 bench/eval.py                 offline loop contracts (the gate)
  python3 bench/eval.py --live          live agent failures (needs a key)
  from bench.evals.runner import REGISTRY, run
  LOOP_CASES / LIVE_CASES               the two case lists

INVARIANTS
  - Offline and live never mix. A keyless run cannot report a green live suite.
  - Every case gets a fresh temp workspace and a fresh kernel; a case exception is a failed
    row, not a suite crash.
  - The oracle is the verdict; the model's prose is evidence, never the pass condition.
  - Results are append-only under `results/`, with `tokens/pass` in every report.

WHERE TO CHANGE
  A loop mechanism ......... loops.py (and the agent module it exercises)
  A live failure ........... live.py plus an oracle
  The registry or CLI ...... runner.py
"""
from __future__ import annotations

from .live import LIVE_CASES
from .loops import LOOP_CASES
from .runner import REGISTRY, Report, run
from .types import Case, CaseResult, Context

__all__ = ["LOOP_CASES", "LIVE_CASES", "REGISTRY", "Report", "run",
           "Case", "CaseResult", "Context"]
