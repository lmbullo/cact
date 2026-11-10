#!/usr/bin/env python3
"""eval: CLI front for bench/evals; offline loop contracts, live failures.
WHAT IT IS
  Thin entry point: resolve repo root, call bench.evals.runner.main.
  Offline is the default; --live needs DEEPSEEK_API_KEY.
ENTRY POINTS
  python3 bench/eval.py --list                 every case, its kind and failure tag
  python3 bench/eval.py                        the offline loop pass
  python3 bench/eval.py --tasks L04_bad_args   one case
  python3 bench/eval.py --live                 the live failure-mode suite
  python3 -m bench.eval ...                    package mode
INVARIANTS
  a missing key can only fail --live, never turn the offline suite green or empty.
  exit 0 only when every selected case passes.
  evidence under bench/evals/results/ unless --no-write.
  script mode inserts the repo root; package mode uses the relative import; keep both.
WHERE TO CHANGE
  case/run logic .... bench/evals/runner.py
  a new flag ........ runner.main (argv forwarded unchanged)
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
