#!/usr/bin/env python3
"""Run a hidden test directory against the workspace in the current directory.

Used by bench/hard.py as the held-out oracle: the tests live outside the workspace, so the
agent can see pass/fail and failure text but cannot read or edit them. Stdout is one
`FAILED <test>: <message>` line per failure plus a `SUMMARY n/m passed` line, which is what
check.py already parses.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path


def main(argv: list[str]) -> int:
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
