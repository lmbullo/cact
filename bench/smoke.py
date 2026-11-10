"""smoke: offline self-check for the agent and the evals: no network, no API key, no fixtures touched.

WHAT IT IS
  The one command that says "the code still works" without spending tokens; this file is the runner: it imports every
  module and calls one check group per area in dependency order. Checks live in bench/smoke_checks/ (one module per area),
  harness in bench/smoke_support.py. groups: evals, helpers, index, env, structure, docs, plans, protocol, files, verbs,
  gates, revert, serve, handoff, limits, turns, repl, wiring, style.

ENTRY POINTS
  python3 bench/smoke.py   the only entry point; main() returns the exit code
  bench/smoke_support.py   check(), summary(), workspace(), kernel_for() and the fakes
  bench/smoke_checks/*.py  one run(...) per area; the runner decides the order

INVARIANTS
  runs in a temporary directory; nothing under the repo is written, no task is graded, no trace is recorded.
  contract test: a failure means the interface an agent relies on changed (prelude, Cell fields, envelopes),
  not that a model misbehaved. Keep it fast (a few seconds); network or model calls belong in bench/live.py.
  the kernel groups share ONE temp workspace and ONE kernel child: files -> verbs -> gates, in that order.

WHERE TO CHANGE
  new kernel helper or Cell field ... bench/smoke_checks/files.py or verbs.py   new eval case ... evals.py
  new check group ... add bench/smoke_checks/<area>.py and call it here   harness/fakes ... bench/smoke_support.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # script mode: make `bench` importable before the package imports
    sys.path.insert(0, str(ROOT))

from bench.smoke_checks import (  # noqa: E402
    docs, env, evals, files, gates, handoff, helpers, index, limits, plans, protocol, repl,
    revert, serve, structure, style, turns, verbs, wiring,
)
from bench.smoke_support import check, kernel_for, summary  # noqa: E402


def main() -> int:
    """Run every group in order and print the pass/fail line; 0 only when all checks pass."""
    evals.run(check)
    helpers.run(check)
    index.run(check)
    env.run(check)
    structure.run(check)
    docs.run(check)

    # The workspace and the kernel child are shared by every later group: plans creates the
    # tree, then one child serves files -> verbs -> gates (they depend on each other's files).
    root = plans.run(check)
    protocol.run(check, root)
    with kernel_for(root) as kernel:
        files.run(check, root, kernel)
        verbs.run(check, root, kernel)
        gates.run(check, root, kernel)

    revert.run(check)
    serve.run(check)
    handoff.run(check)
    limits.run(check)
    turns.run(check)
    repl.run(check)
    wiring.run(check)
    style.run(check)
    return summary()


if __name__ == "__main__":
    raise SystemExit(main())
