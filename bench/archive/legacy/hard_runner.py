#!/usr/bin/env python3
"""The held-out oracle runner for the hard suite: hidden tests in, failure lines out.

WHAT IT IS
  `bench/hard.py::hidden_check` runs this script with a directory under `bench/hard_hidden/`
  and cwd=the task workspace. It discovers the hidden tests, runs them with unittest, and
  prints one machine-readable line per failure plus a total, which is what the check plan
  parses.

ENTRY POINTS
  python3 bench/hard_runner.py <hidden-test-dir>
  main(argv) -> exit code: 0 only when every hidden test passes

INVARIANTS
  - The tests live outside the workspace, so the agent can see pass/fail and the failure text
    but cannot read or edit them.
  - Stdout is one `FAILED <test>: <message>` line per failure plus a `SUMMARY n/m passed`
    line; unittest's own output goes to /dev/null so the workspace never sees a traceback it
    could special-case.
  - Both the workspace and the hidden directory go on sys.path, so a test imports the code it
    grades rather than an installed copy.

WHERE TO CHANGE
  Failure-line shape ... keep it in sync with check_run.py::_PATTERNS
  Test discovery ....... the unittest loader in main()
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path


def main(argv: list[str]) -> int:
    """Run the held-out tests in argv[1] against the cwd; one `FAILED` line per failure."""
    hidden = Path(argv[1]).resolve()
    sys.path.insert(0, os.getcwd())
    sys.path.insert(0, str(hidden))
    loader = unittest.TestLoader()
    suite = loader.discover(str(hidden), pattern="test_*.py", top_level_dir=str(hidden))
    with open(os.devnull, "w") as sink:
        result = unittest.TextTestRunner(stream=sink, verbosity=0).run(suite)
    total = result.testsRun
    failed = len(result.failures) + len(result.errors)
    for test, trace in result.failures + result.errors:
        lines = [line for line in trace.strip().splitlines() if line.strip()]
        detail = lines[-1] if lines else "failed"
        print(f"FAILED {test.id()}: {detail[:300]}")
    print(f"SUMMARY {total - failed}/{total} passed")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
