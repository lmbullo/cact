"""docs: checked documents stay pointers, never dead links or copies.

WHAT IT IS
  Offline checks over DOCS:  paths every backticked path resolves;  unique no paragraph of one
  document is copied verbatim into another;  agent docs/agent is byte-identical to the headers
  and covers each module once within 100 lines.

ENTRY POINTS
  run(check), DOCS                  group entry after `index`; the documents that are checked
  _agent_docs()                     agent-page drift, coverage and line-budget report
  _references(text)                 backticked spans that look like a repository path
  _resolves(ref, doc_dir, paths)    whether a reference names something in the tree
  _paragraphs(text), _copies(docs)  comparison unit; paragraph pairs two documents share

INVARIANTS
  only path-shaped spans: `/` plus a dot or trailing `/` in the last segment; field lists and
  fractions are prose; a ref resolves from the root, the document dir, or a path suffix.
  _GENERATED trees are skipped as refs and in the walk; archive/legacy is still checked;
  unique compares normalised paragraphs of >= _DUP_CHARS (200), exact copies only; read-only,
  no key; a header edit without pages regeneration fails the byte comparison.

WHERE TO CHANGE
  DOCS; _references(); _agent_docs() and bench/agent/pages.py; _GENERATED; _DUP_CHARS.
"""
from __future__ import annotations

import collections
import os
import re
from pathlib import Path

from bench.agent import headers as header_index
from bench.agent import pages as agent_pages

ROOT = Path(__file__).resolve().parents[2]
DOCS = ("AGENTS.md", "README.md", "docs/README.md", "docs/run.md", "docs/architecture.md",
        "tui/README.md", "bench/HARD.md") + agent_pages.DOCS
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


def _agent_docs():
    """Agent-doc drift: stale files, overlong pages, missing/duplicate/unknown modules."""
    base = str(ROOT)
    wanted = sorted(row["path"] for row in header_index.index(base))
    expected = agent_pages.render_all(base)
    actual, stale = {}, []
    for rel, text in expected.items():
        try:
            actual[rel] = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            actual[rel] = ""
        if actual[rel] != text:
            stale.append(rel)
    seen, long = [], []
    for rel in agent_pages.DOCS:
        text = actual.get(rel, "")
        if rel != agent_pages.INDEX and len(text.splitlines()) > agent_pages.MAX_LINES:
            long.append("%s (%d lines)" % (rel, len(text.splitlines())))
        seen.extend(re.findall(r"^module=(\S+)", text, re.M))
    counts = collections.Counter(seen)
    missing = [path for path in wanted if not counts[path]]
    duplicate = sorted(path for path, count in counts.items() if count > 1)
    unknown = sorted(path for path in counts if path not in set(wanted))
    return stale, long, missing, duplicate, unknown


def run(check):
    """Every repo path named in the checked documents resolves, and no document copies another."""
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
        for ref in re.findall(
                r"^(?:module|file|page|source_headers|source_symbols|generated_by)=(\S+)",
                text, re.M):
            if not (ROOT / ref).exists():
                dead.append(f"{name}: {ref}")
    check("docs: every named path exists", not dead, "; ".join(dead[:6]))
    copies = [f"{second} copies {first}: {paragraph[:60]}"
              for paragraph, first, second in _copies(DOCS)]
    check("docs: no paragraph is copied between documents", not copies, "; ".join(copies[:4]))
    stale, long, missing, duplicate, unknown = _agent_docs()
    drift = (["stale: " + ", ".join(stale[:4])] if stale else []) + (
        ["unknown: " + ", ".join(unknown[:4])] if unknown else [])
    check("docs: agent pages match module headers", not drift, "; ".join(drift))
    coverage = (["missing: " + ", ".join(missing[:4])] if missing else []) + (
        ["duplicate: " + ", ".join(duplicate[:4])] if duplicate else [])
    check("docs: every source module documented exactly once", not coverage,
          "; ".join(coverage))
    check("docs: agent pages fit the line budget", not long, "; ".join(long[:4]))
