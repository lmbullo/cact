"""index: the contract index reaches an agent, and the map CLI agrees with it.

WHAT IT IS
  Drives bench/agent/headers.py the way a kernel cell does: index(ROOT) must contain the modules an
  agent is told to read, guide() must page with an offset, a topic query must name the owning
  module, an unknown module must be a miss and not an exception, and a known module must read back
  whole.  Every ENTRY POINTS callable must resolve to a declared symbol, so a header cannot
  advertise a deleted function.  Then python3 bench/guide.py --check must exit clean.

ENTRY POINTS
  run(check)                    called by bench/smoke.py::main after helpers
  _declared_names()             every name the Python source declares, embedded sources included
  _entry_point_gaps(rows, names)  (module, callable) pairs a header names but nothing declares

INVARIANTS
  one example path is asserted (bench/agent/kernel/prelude.py); a kernel rename moves this check.
  the guide CLI runs in-process with redirected stdout, no child and no shell.
  the entry-point check is lenient about where a name is declared (a re-export is legitimate) and
  flags only a name that appears nowhere in the source; fixtures and generated trees are skipped
  by headers._walk.

WHERE TO CHANGE
  indexed roots or skip rules .. headers.SKIP_DIRS / LANGUAGES;  example query or module .. here
"""
from __future__ import annotations

import ast
import contextlib
import io
import pathlib
import re

import bench.agent.headers as headers
import bench.guide as guide_cli
from bench.smoke_support import ROOT

# Words that precede a `(` in header prose, not callables a module declares.
_PROSE_CALLS = frozenset({
    "and", "as", "for", "if", "in", "is", "not", "open", "or", "pass", "print",
    "python", "python3", "range", "see", "self", "use", "with", "workspace",
})
_DEF_ENTRY = re.compile(r"(?:^|;|\n)[ \t]*([A-Za-z_]\w*)[ \t]*\(")


def _tree_names(tree, depth=0):
    """Every name a parsed tree declares or imports, plus the names inside embedded sources."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif (depth < 3 and isinstance(node, ast.Constant)
              and isinstance(node.value, str)
              and ("def " in node.value or "import " in node.value)):
            try:
                names |= _tree_names(ast.parse(node.value), depth + 1)
            except SyntaxError:
                pass
    return names


def _declared_names():
    """Every name the Python source under ROOT declares, including the exec'd prelude strings."""
    names = set()
    for full in headers._walk(str(ROOT)):
        if not full.endswith(".py"):
            continue
        try:
            text = pathlib.Path(full).read_text(encoding="utf-8", errors="replace")
            names |= _tree_names(ast.parse(text))
        except (OSError, SyntaxError):
            continue
    return names


def _entry_point_gaps(rows, names):
    """(module, name) pairs a Python ENTRY POINTS section names but nothing in the source declares."""
    gaps = []
    for row in rows:
        if row["language"] != "python":
            continue
        body = row["sections"].get("ENTRY POINTS") or ""
        for match in _DEF_ENTRY.finditer(body):
            name = match.group(1)
            if name not in _PROSE_CALLS and name not in names:
                gaps.append((row["path"], name))
    return gaps


def run(check):
    """The header index reaches an agent (`guide`), pages truthfully, and agrees with the CLI."""
    # --- the contract index: every module explains itself --------------------------------
    rows = headers.index(ROOT)
    paged = headers.guide(ROOT, query="read", limit=1, offset=1)
    check("headers: guide(query) pages with offset",
          paged.get("shown") == 1 and paged.get("offset") == 1
          and (paged.get("total") or 0) >= 2,
          str(paged)[:240])

    check("headers: the index reaches the agent",
          any(r["path"] == "bench/agent/kernel/prelude.py" for r in rows),
          str([r["path"] for r in rows[:3]]))
    gaps = headers.problems(ROOT)
    check("headers: every module header is complete", not gaps, str(gaps[:3]))
    check("headers: no in-scope header exceeds %d lines" % headers.MAX_HEADER,
          not [row for row in rows if headers.in_scope(row["path"])
               and row["header_lines"] > headers.HEADER_ALLOW.get(
                   row["path"], headers.MAX_HEADER)],
          str([(row["path"], row["header_lines"]) for row in rows
               if headers.in_scope(row["path"])
               and row["header_lines"] > headers.HEADER_ALLOW.get(
                   row["path"], headers.MAX_HEADER)])[:300])
    hit = headers.guide(ROOT, query="check plan", limit=3)
    check("headers: a query names the owning module",
          bool(hit["modules"]) and hit["modules"][0]["path"] == "bench/agent/check_plan.py",
          str([r["path"] for r in hit["modules"]]))
    check("headers: an unknown module is a miss, not an exception",
          headers.guide(ROOT, path="no/such.py")["modules"] == [])
    check("headers: one module reads back whole", "ENTRY POINTS" in headers.guide(ROOT, path="bench/guide.py")["text"])
    gaps = _entry_point_gaps(rows, _declared_names())
    check("headers: every entry point names a real symbol", not gaps, str(gaps[:5]))
    with contextlib.redirect_stdout(io.StringIO()):
        quiet = guide_cli.main(["--check", "--json"])
    check("guide: --check is clean", quiet == 0)
