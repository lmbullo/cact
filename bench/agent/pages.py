"""pages: generate docs/agent from module headers, so the terse pages cannot drift.

WHAT IT IS
  render_all(root) turns bench/agent/headers.py::index plus bench/index.py::build into the
  agent-only documentation set: one small index (docs/agent/README.md) and one page per subsystem
  (<= MAX_LINES), each mapping its modules to the header's ENTRY POINTS and indexed public symbols.
  --write persists; --check fails on a diff.

ENTRY POINTS
  python3 -m bench.agent.pages [--write | --check]
  render_all(root=None) -> dict[str, str]   relative docs path -> whole file text
  subsystem(path) -> str                    owning page name, or '' when unassigned
  DOCS                                      every generated file, repo-relative
  PAGES                                     (subsystem, page file) in index order

INVARIANTS
  pages are rendered from header rows and symbol rows, never hand-edited; a stale byte is a gate
  failure in bench/smoke_checks/docs.py; an unassigned path is a render error, not an omission.
  a page is key=value or one-line facts: no prose, no blank-line tutorials; modules are sorted
  by path and subsystems follow PAGES order.

WHERE TO CHANGE
  page names/ownership .. PAGES, subsystem();  row .. _page(), _entry();  MAX_LINES;  main()
"""
from __future__ import annotations

import argparse
from pathlib import Path

from bench import index as symbol_index
from bench.agent import headers

ROOT = Path(__file__).resolve().parents[2]
MAX_LINES = 100
INDEX = "docs/agent/README.md"
PAGES = (
    ("bench-core", "bench-core.md"),
    ("agent", "agent.md"),
    ("kernel", "kernel.md"),
    ("kernel-run", "kernel-run.md"),
    ("evals", "evals.md"),
    ("smoke", "smoke.md"),
    ("tui", "tui.md"),
    ("tui-tests", "tui-tests.md"),
    ("tui-server", "tui-server.md"),
)
DOCS = (INDEX,) + tuple("docs/agent/" + page for _, page in PAGES)


def subsystem(path):
    """Owning page name for an indexed module path; '' when no page claims it."""
    path = str(path).replace("\\", "/")
    if path.startswith("bench/agent/kernel/run_prelude_"):
        return "kernel-run"
    if path.startswith("bench/agent/kernel/"):
        return "kernel"
    if path.startswith("bench/agent/"):
        return "agent"
    if path.startswith("bench/evals/"):
        return "evals"
    if path.startswith("bench/smoke_checks/"):
        return "smoke"
    if path.startswith("bench/") and path.count("/") == 1:
        return "bench-core"
    if path.startswith("tui/src/server"):
        return "tui-server"
    if path.startswith("tui/tests/"):
        return "tui-tests"
    if path.startswith("tui/"):
        return "tui"
    return ""


def _entry(row):
    """The ENTRY POINTS section flattened to one semicolon-separated fact line."""
    body = row["sections"].get("ENTRY POINTS", "")
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    return " | ".join(lines) or "-"


def _symbols(root):
    """Indexed module path -> comma-joined public symbol names."""
    return {
        row["path"]: ",".join(symbol["name"] for symbol in row["symbols"])
        for row in symbol_index.build(root)["modules"]
    }


def _page(name, page, rows, symbols):
    """One subsystem page: header facts, then one module + one entry-points line each."""
    lines = [
        "# " + name,
        "generated_by=bench/agent/pages.py",
        "source_headers=bench/agent/headers.py",
        "source_symbols=bench/index.py",
        "subsystem=" + name,
        "page=docs/agent/" + page,
        "modules=" + str(len(rows)),
    ]
    for row in sorted(rows, key=lambda item: item["path"]):
        lines.append("module=%s symbols=%s" % (
            row["path"], symbols.get(row["path"]) or "-"))
        lines.append("entry=" + _entry(row))
    return "\n".join(lines) + "\n"


def _index(groups):
    """The small index: subsystem -> page file -> the anchor module's entry points."""
    lines = [
        "# agent docs index",
        "generated_by=bench/agent/pages.py",
        "source_headers=bench/agent/headers.py",
        "source_symbols=bench/index.py",
        "rule=one page per subsystem; edit the header, then regenerate",
    ]
    for name, page in PAGES:
        rows = groups.get(name, [])
        if not rows:
            lines.append("subsystem=%s file=docs/agent/%s modules=0 entry=-" % (name, page))
            continue
        anchor = sorted(rows, key=lambda item: item["path"])[0]
        lines.append(
            "subsystem=%s file=docs/agent/%s modules=%d anchor=%s entry=%s"
            % (name, page, len(rows), anchor["path"], _entry(anchor)))
    return "\n".join(lines) + "\n"


def render_all(root=None):
    """Every generated docs file as {repo-relative path: text}; raises on an unowned module."""
    base = str(Path(root or ROOT).resolve())
    groups = {name: [] for name, _ in PAGES}
    for row in headers.index(base):
        name = subsystem(row["path"])
        if not name:
            raise ValueError("no agent-docs subsystem owns %s" % row["path"])
        groups[name].append(row)
    symbols = _symbols(base)
    out = {INDEX: _index(groups)}
    for name, page in PAGES:
        out["docs/agent/" + page] = _page(name, page, groups.get(name, []), symbols)
    return out


def main(argv=None):
    """Write the generated docs, check them for drift, or print the planned paths."""
    parser = argparse.ArgumentParser(description="Generate docs/agent from module headers.")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    base = Path(args.root).resolve()
    rendered = render_all(base)
    if args.write:
        for rel, text in rendered.items():
            target = base / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        print("wrote %d agent docs under %s/docs/agent" % (len(rendered), base))
        return 0
    stale = []
    for rel, text in rendered.items():
        try:
            actual = (base / rel).read_text(encoding="utf-8")
        except OSError:
            actual = None
        if actual != text:
            stale.append(rel)
    if args.check:
        for rel in stale:
            print("stale: " + rel)
        print("pages: %d stale file(s)" % len(stale))
        return 1 if stale else 0
    for rel in rendered:
        print(rel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
