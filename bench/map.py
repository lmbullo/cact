#!/usr/bin/env python3
"""The contract CLI: what every module is, and which one owns the thing you are about to change.

WHAT IT IS
  `bench/agent/headers.py` turns the WHAT IT IS / ENTRY POINTS / INVARIANTS / WHERE TO CHANGE
  header of every module into rows; this is the terminal front for those rows. It is the
  first command an agent should run in this repo: one screen instead of thirty files, and a
  gate (`--check`) that fails when a module stops explaining itself.

ENTRY POINTS
  python3 bench/map.py                     the map: one line per module
  python3 bench/map.py --query checks      ranked modules that own "checks", with their headers
  python3 bench/map.py --module bench/agent/check.py     one module's full header
  python3 bench/map.py --json [--query Q]  the same data as JSON, for a program
  python3 bench/map.py --check             exit 1 if a header is missing a section
  main(argv=None) -> exit code; the flags are mutually exclusive except --json

INVARIANTS
  - Read-only and offline: it prints, it never writes, and it needs no key and no workspace.
  - It delegates every parse to bench/agent/headers.py, the same source a kernel cell runs, so
    the CLI and the `guide()` verb cannot disagree.
  - `--check` is the contract gate: exit 0 means every indexed source file carries all four
    sections. It is the reason a new module cannot land undocumented.
  - `--json` never mixes prose into stdout; the human table only exists without it.

WHERE TO CHANGE
  The data ......... bench/agent/headers.py (parsing, ranking, rendering)
  A new flag ....... main(); keep `--check` green when you add a module
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bench.agent import headers  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bench/map.py",
        description="The repo contract index: module -> what it is, where to change it.")
    parser.add_argument("--query", "-q", default=None,
                        help="terms to match against paths, titles and headers")
    parser.add_argument("--module", "-m", default=None, help="print one module's header")
    parser.add_argument("--limit", type=int, default=6, help="max rows for --query (default 6)")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    parser.add_argument("--check", action="store_true",
                        help="exit 1 when a module header is missing a section")
    args = parser.parse_args(argv)

    if args.check:
        gaps = headers.problems(ROOT)
        if args.json:
            print(json.dumps({"ok": not gaps, "problems": gaps}, indent=2))
        else:
            for gap in gaps:
                print("%s  missing: %s" % (gap["path"], ", ".join(gap["missing"])))
            print("%d module(s) with a header gap" % len(gaps) if gaps
                  else "every module header is complete")
        return 1 if gaps else 0

    result = headers.guide(ROOT, query=args.query, path=args.module, limit=args.limit)
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    if args.query or args.module:
        print(result["text"])
        return 0
    print(result["text"])
    print("\n%d files, %d documented; --query TEXT finds an owner, --module PATH reads one header"
          % (result["files"], result["documented"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
