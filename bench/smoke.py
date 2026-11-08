"""Offline self-check for the agent and the evals: no network, no API key, no fixtures
touched.

WHAT IT IS
  The one command that tells a coding agent "the code still works" without spending tokens.
  This file is the runner: it imports every module, then calls one check group per area, in
  dependency order. The checks themselves live in `bench/smoke_checks/` (one module per
  area, each with its own header) and share the harness in `bench/smoke_support.py`.

    evals     the eval registry is intact and a fast loop subset passes
    helpers   pure helpers: reply parsing, card digests, budgets, generated verb specs
    index     the header index and `bench/guide.py --check`
    docs      every repository path a document names still exists
    plans     the check plan, the Verifier, read coverage, run_check's three states
    protocol  a stale child is restarted, the current PROTOCOL is accepted
    files     the journalled file action space (read/write/edit/undo/diff/map/guide)
    verbs     the typed verbs (map/read/search/splice/exec/outline/help) via Kernel.call
    gates     process gates, recall, orphan reaping, apply/commit, the state card
    revert    revert_check: a test that passes on HEAD, a dirty tree, a failed restore
    turns     the turn machine: staged finishes, stop vocabulary, tool-call pairing
    repl      the fenced-block dialect reuses that machine
    wiring    every journal kind is tested and every verb has a smoke case
    style     the tree lints clean under ruff.toml and every public name is documented

  python3 bench/smoke.py        exits 0 when every check passes, 1 otherwise

ENTRY POINTS
  python3 bench/smoke.py   the only entry point; `main()` returns the exit code
  bench/smoke_support.py   check(), summary(), workspace(), kernel_for() and the fakes
  bench/smoke_checks/*.py  one `run(...)` per area; the runner decides the order

INVARIANTS
  - Runs in a temporary directory; nothing under the repo is written, no task is graded, no
    trace is recorded.
  - It is a *contract* test: a failure here means the interface an agent relies on changed
    (kernel prelude, Cell fields, envelopes), not that a model misbehaved.
  - Keep it fast (a few seconds). Network or model calls belong in bench/live.py.
  - The kernel groups share ONE temp workspace and ONE kernel child: `files` then `verbs`
    then `gates`, in that order, because later checks reuse files earlier ones created.

WHERE TO CHANGE
  A new kernel helper or Cell field ....... bench/smoke_checks/files.py or verbs.py
  A new eval case or registry count ....... bench/smoke_checks/evals.py
  A new check group ....................... add bench/smoke_checks/<area>.py and call it here
  The harness, fakes or fixtures ........... bench/smoke_support.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # script mode: make `bench` importable before the package imports
    sys.path.insert(0, str(ROOT))

from bench.smoke_checks import (  # noqa: E402
    docs, evals, files, gates, helpers, index, plans, protocol, repl, revert, style, turns, verbs,
    wiring,
)
from bench.smoke_support import check, kernel_for, summary  # noqa: E402


def main() -> int:
    """Run every group in order and print the pass/fail line; 0 only when all checks pass."""
    evals.run(check)
    helpers.run(check)
    index.run(check)
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
    turns.run(check)
    repl.run(check)
    wiring.run(check)
    style.run(check)
    return summary()


if __name__ == "__main__":
    raise SystemExit(main())
