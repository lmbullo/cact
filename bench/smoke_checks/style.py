"""style: the two machine-checkable legibility contracts — ruff-clean code, documented public names.

WHAT IT IS
  Two checks over the repo's own source (fixtures and generated trees excluded):
    ruff    `python3 -m ruff check .` with the committed `ruff.toml`: E/F/W/B catch undefined
            names, unused imports, loop-closure capture and silent `zip()` truncation, which are
            exactly the defects an agent introduces when it edits by pattern instead of by reading.
    docs    every public function, method and class carries a docstring. That line is the
            contract an agent reads through `inspect`, `help(tool)` and `outline`, so a missing
            one makes a module cost a full read to use.

  Ruff is a development tool, not a runtime dependency: when it is not installed the group
  reports a skip instead of failing, so `bench/smoke.py` stays stdlib-only and offline.

ENTRY POINTS
  run(check) -> None   called by bench/smoke.py::main after `index`

INVARIANTS
  - Read-only and offline: it lints, parses and prints; it writes nothing and needs no network.
  - The file list is `bench.agent.headers.index`, the same walk the header gate uses, so "source
    file" means one thing in this repo. Which files are excluded is decided in headers.SKIP_DIRS
    and ruff.toml, never here.
  - Only public names (no leading underscore) are required to be documented; a private helper is
    covered by its caller's docstring.
  - Adding a lint rule means editing ruff.toml. There is no rule list in this file.

WHERE TO CHANGE
  The lint rule set or the excludes .... ruff.toml
  What "source file" means ............. bench/agent/headers.py::_walk, SKIP_DIRS
  The commands an agent should run ..... docs/run.md §1
"""
from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
from pathlib import Path

from bench.agent import headers

ROOT = Path(__file__).resolve().parents[2]


def _undocumented(path: Path) -> list[str]:
    """`path:line name` for every public def/class in `path` without a docstring."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError as exc:
        return [f"{path}:{exc.lineno} syntax error: {exc.msg}"]
    missing = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_") and ast.get_docstring(node) is None:
                missing.append(f"{path.relative_to(ROOT)}:{node.lineno} {node.name}")
    return missing


def _lint(check) -> None:
    """Run the committed ruff config; skipped (not failed) when ruff is not installed."""
    if importlib.util.find_spec("ruff") is None:
        print("skip style: ruff is not installed (pip install ruff to enforce ruff.toml)")
        return
    proc = subprocess.run([sys.executable, "-m", "ruff", "check", ".", "--no-cache"],
                          cwd=str(ROOT), capture_output=True, text=True)
    detail = (proc.stdout + proc.stderr).strip().splitlines()
    check("style: ruff.toml is clean", proc.returncode == 0,
          "\n".join(detail[-6:]) if proc.returncode else "")


def _docstrings(check) -> None:
    """Every public def/class in a source module is documented (see the module header)."""
    missing: list[str] = []
    for row in headers.index(ROOT):
        if row["language"] != "python":
            continue
        missing.extend(_undocumented(ROOT / row["path"]))
    check("style: every public name is documented", not missing, "; ".join(missing[:6]))


def run(check):
    """Lint the tree with ruff.toml, then require a docstring on every public name."""
    _lint(check)
    _docstrings(check)
