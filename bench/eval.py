#!/usr/bin/env python3
"""eval: the CLI front for bench/evals — loop contracts offline, agent failures live.

WHAT IT IS
  A thin entry point, not a second runner. It resolves the repo root for script mode, then
  calls `bench.evals.runner.main`. The offline pass is the regression gate (no key, no
  network); `--live` is the model-backed failure suite and needs DEEPSEEK_API_KEY.

ENTRY POINTS
  python3 bench/eval.py --list                 every case, its kind and its failure tag
  python3 bench/eval.py                        the offline loop pass
  python3 bench/eval.py --tasks L04_bad_args   one case
  python3 bench/eval.py --live                 the live failure-mode suite
  python3 -m bench.eval ...                    package mode

INVARIANTS
  - Offline is the default. A missing key can only fail a `--live` run, never turn the
    offline suite green or empty.
  - Exit 0 only when every selected case passes; the JSON evidence is written under
    `bench/evals/results/` unless `--no-write`.
   - Two import paths: script mode inserts the repo root, package mode uses the relative
     import. Keep both working (the same rule the retired `bench.run` followed).

WHERE TO CHANGE
  A case or the run logic .... bench/evals/runner.py
  A new flag ................. runner.main (this file forwards argv unchanged)
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if __package__ in (None, ""):
    from bench.evals.runner import main
else:
    from .evals.runner import main


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
