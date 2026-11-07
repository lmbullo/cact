"""docs: the four documents stay pointers, never dead links and never copies of each other.

WHAT IT IS
  Two offline checks over the documents an agent reads before editing: `AGENTS.md`, `README.md`,
  `tui/README.md`, `bench/HARD.md`.
    paths   every backticked path a document names resolves in the tree, so a rename cannot
            leave a pointer that costs the next agent a wasted `read`.
    unique  no paragraph of one document is copied verbatim into another. Each fact has one
            owner (AGENTS.md: invariants/recipes; README.md: machinery; tui/README.md: the wire
            contract; HARD.md: measured evidence), and duplicated prose drifts until one copy
            lies, which an agent cannot detect by reading either copy.

ENTRY POINTS
  run(check)                        called by bench/smoke.py::main after `index`
  DOCS                              the documents that are checked
  _references(text)                 backticked spans that look like a repository path
  _resolves(ref, doc_dir, paths)    whether a reference names something in the tree
  _paragraphs(text)                 the comparison unit for the unique check
  _copies(docs)                     (text, path, path) for every paragraph two documents share

INVARIANTS
  - Only path-shaped spans are checked: a span needs a `/`, and its last segment must carry a
    dot (a file) or end with `/` (a directory). Field lists (`ok/op/kind`) and fractions
    (`3/5`) are prose, not paths.
  - A reference resolves if it exists from the repo root, from the document's own directory
    (`src/...` in `tui/README.md`), or as a suffix of any tracked path: headings and prose say
    `kernel/prelude.py` where the real path is `bench/agent/kernel/prelude.py`.
  - Trees in `_GENERATED` are excluded twice: a reference that points into one (`bench/ws/`,
    `bench/evals/results/`, `bench/archive/results/`, `tui/node_modules/`) is skipped, and so is
    the walk that collects the paths. Those directories are generated or append-only evidence,
    absent in a fresh clone, and not part of the contract. Committed trees are checked like any
    other path, including `bench/archive/legacy/`: a rename there is still a dead pointer.
  - The unique check compares whole paragraphs after whitespace normalisation and only above
    `_DUP_CHARS`, so a shared sentence (`You are the reader.`) or a shared code block is not a
    copy. It is exact: paraphrased duplication is a review call, not a gate.
  - Read-only: it reads the documents and lists the tree; it writes nothing and needs no key.

WHERE TO CHANGE
  The documents that are checked ....... DOCS
  What counts as a path reference ...... _references()
  Which trees are generated ............ _GENERATED (not this file's callers)
  The copy threshold ................... _DUP_CHARS, _paragraphs()
"""
from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ("AGENTS.md", "README.md", "tui/README.md", "bench/HARD.md")
_DUP_CHARS = 200
# Generated or tool-managed trees: absent in a fresh clone, so a pointer into one may dangle.
_GENERATED = frozenset({
    ".git", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".tmp", ".cache", "dist", "build", "ws", "results",
})
_SPAN = re.compile(r"`([^`\n]+)`")
_TRAILING = ".,;:"


def _references(text):
    """Backticked spans that look like a repository path (a `/` plus a file or a trailing `/`)."""
    refs = []
    for match in _SPAN.finditer(text or ""):
        span = match.group(1).strip().rstrip(_TRAILING)
        if "/" not in span or span.startswith(("/", "~", "http")):
            continue
        if any(char in span for char in " ()<>=*,:"):
            continue
        last = span.split("/")[-1]
        if not (last.endswith("/") or "." in last):
            continue
        if set(span.rstrip("/").split("/")) & _GENERATED:
            continue
        if span not in refs:
            refs.append(span)
    return refs


def _walk(root):
    """Every tracked path under `root` as a relative string, generated trees skipped."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in _GENERATED)
        for name in sorted(filenames):
            yield os.path.relpath(os.path.join(dirpath, name), root).replace(os.sep, "/")


def _resolves(ref, doc_dir, paths):
    """True when `ref` exists from the root, from `doc_dir`, or as a suffix of a tracked path."""
    want = ref.rstrip("/")
    for base in (ROOT, doc_dir):
        if (base / want).exists():
            return True
    return any(path == want or path.endswith("/" + want) for path in paths)


def _paragraphs(text):
    """Normalized whitespace paragraphs above `_DUP_CHARS`, the unit the unique check compares."""
    out = []
    for block in re.split(r"\n\s*\n", text or ""):
        flat = " ".join(block.split())
        if len(flat) >= _DUP_CHARS:
            out.append(flat)
    return out


def _copies(docs):
    """`(paragraph, first_path, second_path)` for every paragraph two documents share verbatim."""
    owner: dict[str, str] = {}
    found = []
    for name in docs:
        try:
            text = (ROOT / name).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for paragraph in _paragraphs(text):
            first = owner.setdefault(paragraph, name)
            if first != name:
                found.append((paragraph, first, name))
    return found


def run(check):
    """Every repo path named in the four documents resolves, and no document copies another."""
    paths = list(_walk(ROOT))
    dead = []
    for name in DOCS:
        doc = ROOT / name
        try:
            text = doc.read_text(encoding="utf-8", errors="replace")
        except OSError:
            dead.append(f"{name}: missing document")
            continue
        for ref in _references(text):
            if not _resolves(ref, doc.parent, paths):
                dead.append(f"{name}: {ref}")
    check("docs: every named path exists", not dead, "; ".join(dead[:6]))
    copies = [f"{second} copies {first}: {paragraph[:60]}"
              for paragraph, first, second in _copies(DOCS)]
    check("docs: no paragraph is copied between documents", not copies, "; ".join(copies[:4]))
