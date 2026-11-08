"""index: generate the agent-facing symbol index for the source tree.

WHAT IT IS
  `build(root)` reads every module the header index already walks, adds the public symbols
  and their line ranges, and returns one JSON-shaped document. `--write` persists it as
  `agents.index.json`; `--check` fails when a module has no four-section header. The index is
  generated, never hand-written: the source is the authority.

ENTRY POINTS
  python3 bench/index.py [--root .] [--write | --check | --json]
  build(root=".") -> dict          {"root": ..., "modules": [...]}
  symbols(path, source) -> list[dict]   public classes/functions/methods, with ranges

INVARIANTS
  - The header walk is `agent/headers.py`; this module does not invent a second file walk.
  - A Python symbol is public when its name does not start with `_`; a class contributes its
    public methods as `Class.method`. A parse failure yields no symbols, never an exception.
  - TS/TSX contribute exported declarations by a narrow regex; a missed symbol is a gap, not
    a false row.
  - The output is deterministic: modules sorted by path, symbols by line.

WHERE TO CHANGE
  What a module row carries ............ build
  What a symbol row carries ............ _row, symbols
  The CLI ............................... main
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # script mode: make `bench` importable before the package imports
    sys.path.insert(0, str(ROOT))

from bench.agent import headers  # noqa: E402

_EXPORT = re.compile(
    r"^[ \t]*export[ \t]+(?:default[ \t]+)?(?:async[ \t]+)?"
    r"(?:function|class|const|interface|type|enum)[ \t]+([A-Za-z_$][\w$]*)", re.M)


def build(root: str | Path = ".") -> dict:
    """Every indexed module with its header title and public symbol ranges."""
    base = Path(root).resolve()
    modules = []
    for row in headers.index(str(base)):
        path = base / row["path"]
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            source = ""
        modules.append({
            **row,
            "symbols": symbols(path, source),
        })
    return {"root": str(base), "modules": modules}


def symbols(path: str | Path, source: str) -> list[dict]:
    """Public Python symbols with ranges; TS/TSX exported declarations by regex."""
    if str(path).endswith(".py"):
        return _python_symbols(source)
    return _export_symbols(source)


def _python_symbols(source: str) -> list[dict]:
    """Top-level public classes/functions and their public methods, with line ranges."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out: list[dict] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if not node.name.startswith("_"):
            out.append(_row(node, "class" if isinstance(node, ast.ClassDef) else "function", source))
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                        and not child.name.startswith("_"):
                    out.append(_row(child, "method", source, owner=node.name))
    return sorted(out, key=lambda item: item["line"])


def _row(node, kind: str, source: str, owner: str = "") -> dict:
    """One symbol row: name, kind, line range, signature and the first docstring line."""
    signature = ""
    if not isinstance(node, ast.ClassDef):
        try:
            signature = ast.unparse(node.args)
        except (AttributeError, ValueError):
            signature = ""
    doc = ast.get_docstring(node) or ""
    name = f"{owner}.{node.name}" if owner else node.name
    return {"name": name, "kind": kind, "line": node.lineno,
            "end": getattr(node, "end_lineno", node.lineno),
            "signature": signature, "doc": doc.splitlines()[0] if doc else ""}


def _export_symbols(source: str) -> list[dict]:
    """Exported TS/TSX declarations; line numbers from the match offset."""
    out = []
    for match in _EXPORT.finditer(source):
        out.append({"name": match.group(1), "kind": "export",
                    "line": source[:match.start()].count("\n") + 1,
                    "end": source[:match.start()].count("\n") + 1,
                    "signature": "", "doc": ""})
    return out


def main(argv: list[str] | None = None) -> int:
    """Build, write, check or print the index; exit 1 only for a header problem or write error."""
    parser = argparse.ArgumentParser(description="Build the agent symbol index.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    base = Path(args.root).resolve()
    if args.check:
        problems = headers.problems(str(base))
        for row in problems:
            print("%s missing %s" % (row["path"], ", ".join(row["missing"])))
        print("index: %d header problem(s)" % len(problems))
        return 1 if problems else 0
    data = build(base)
    if args.write:
        target = base / "agents.index.json"
        target.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print("wrote %s (%d modules)" % (target, len(data["modules"])))
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
    elif not args.write:
        print("index: %d modules, %d symbols" % (
            len(data["modules"]), sum(len(row["symbols"]) for row in data["modules"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
