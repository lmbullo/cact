"""The header index: every module's contract as data, one implementation for both halves.

WHAT IT IS
  No human reads this code, so every source module opens with a WHAT IT IS / ENTRY POINTS /
  INVARIANTS / WHERE TO CHANGE header. This module turns those headers into structure so an
  agent can ask "which module owns this?" instead of reading every source file:

    index(root)     every source file as a row: path, language, lines, title, sections
    guide(root, q)  ranked rows plus rendered text; also the `guide()` verb in a kernel cell
    render(rows)     the text a model reads
    problems(root)   rows whose header is missing a section (the gate)
    SECTIONS         the four names, in order

  Kernel cells cannot import `bench`: their cwd is a task workspace and their interpreter is
  a bare `python3 -c`. `kernel.py` therefore ships *this file's source* to the child in
  KERNEL_HEADERS, execs it into a private `_HEADERS` namespace, and exposes one verb,
  `guide()`, so the verb and `python3 bench/map.py` run identical code. Everything here is
  stdlib-only (`_OPEN` pins builtins.open before the prelude replaces it), side-effect-free,
  and resolves the tree from the current working directory when `root` is omitted — which is
  why it works unchanged in both processes, and why it must never import `bench` or read
  `__file__`.

ENTRY POINTS
  python3 bench/map.py [--query TEXT | --module PATH | --json | --check]
  guide(root=None, query=None, path=None, limit=6, chars=900) -> dict
  index(root=None) -> list[dict];  documented(root=None);  problems(root=None)
  header(path) -> (header text, line count);  split(text) -> (title, sections)
  render(rows, chars=900) -> str

INVARIANTS
  - Read-only. It opens source files and returns dicts; no writes, no network, no imports of
    `bench`, no state between calls. Calling it twice on the same tree is the same answer.
  - A file is "documented" when its docstring / leading block comment carries at least one
    section; `problems()` is stricter and demands all four. A half-written or unparsable
    module yields an empty header, never an exception.
  - `SKIP_DIRS` is the contract surface: `node_modules`, `__pycache__`, `bench/fixtures`,
    `bench/edge_fixtures`, `bench/hard_fixtures`, `bench/hard_hidden`, `bench/ws`,
    `bench/traces`, `bench/.tmp`, `bench/.cache`, plus any dot-directory. Task workspaces and
    recorded evidence are inputs and outputs, not code an agent maintains.
  - `guide(query=...)` scores a term three times in path+title and once everywhere else,
    sorts by score then path, and caps at `limit`. Deterministic: same tree, same order.
  - Rendering truncates each section at `chars` and always keeps the path line, so a card
    never hides which module it is talking about.

WHERE TO CHANGE
  The header grammar (sections, delimiters) ... SECTIONS, _SECTION_RE, _QUOTES, split()
  Which files are indexed ................ SKIP_DIRS, LANGUAGES, _walk()
  Ranking, limits, truncation ............ guide(), render()
  The CLI ............................... bench/map.py
"""
from __future__ import annotations

import os
import re

SECTIONS = ("WHAT IT IS", "ENTRY POINTS", "INVARIANTS", "WHERE TO CHANGE")
SKIP_DIRS = frozenset({
    ".git", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".tmp", ".cache", "dist", "build", "fixtures", "edge_fixtures", "hard_fixtures",
    "hard_hidden", "traces", "ws",
})
LANGUAGES = {".py": "python", ".ts": "ts", ".tsx": "tsx", ".js": "javascript"}
READ_LIMIT = 400_000
_QUOTES = ('"' * 3, "'" * 3)
_SECTION_RE = re.compile(
    r"^[ \t]*(WHAT IT IS|ENTRY POINTS|INVARIANTS|WHERE TO CHANGE)[ \t]*$", re.M)
_TS_HEADER = re.compile(r"[ \t]*/\*\*(.*?)\*/", re.S)
_TERMS = re.compile(r"[^0-9a-zA-Z_]+")
# Bound at module load so the kernel prelude's journaling open() (injected after this source)
# cannot reroute a header read through the mutation journal.
_OPEN = open


def header(path):
    """(header text, line count) for one file. '' when it has no header; never raises."""
    try:
        with _OPEN(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read(READ_LIMIT)
    except OSError:
        return "", 0
    lines = text.count("\n") + 1 if text else 0
    if str(path).endswith(".py"):
        body = re.sub(r"\A#![^\n]*\n", "", text)          # shebang line is not a docstring
        body = re.sub(r"^[ \t]*[rRuU]?", "", body, count=1)
        for quote in _QUOTES:
            if body.startswith(quote):
                end = body.find(quote, len(quote))
                return body[len(quote):end if end >= 0 else len(body)], lines
        return "", lines
    match = _TS_HEADER.match(text)
    if not match:
        return "", lines
    rows = [line.strip().lstrip("*").strip() for line in match.group(1).splitlines()]
    return "\n".join(rows), lines


def split(text):
    """Header text -> (title, {section: body}). Unknown prose before the first section is the title."""
    title, sections, current, buffer = "", {}, None, []
    for line in (text or "").splitlines():
        match = _SECTION_RE.match(line)
        if match:
            if current is not None:
                sections[current] = "\n".join(buffer).strip("\n")
            current, buffer = match.group(1), []
        elif current is None:
            if line.strip() and not title:
                title = line.strip()
        else:
            buffer.append(line)
    if current is not None:
        sections[current] = "\n".join(buffer).strip("\n")
    return title.rstrip("."), sections


def _walk(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            if os.path.splitext(name)[1] in LANGUAGES:
                yield os.path.join(dirpath, name)


def index(root=None):
    """Every source file under `root` (default: cwd) with its parsed header."""
    base = root or os.getcwd()
    rows = []
    for full in _walk(base):
        text, lines = header(full)
        title, sections = split(text)
        rows.append({
            "path": os.path.relpath(full, base).replace(os.sep, "/"),
            "language": LANGUAGES[os.path.splitext(full)[1]],
            "lines": lines,
            "title": title,
            "sections": sections,
            "missing": [name for name in SECTIONS if not sections.get(name)],
        })
    return rows


def documented(root=None):
    return [row for row in index(root) if row["sections"]]


def problems(root=None):
    """Rows whose header is missing a section. Empty means the tree conforms."""
    return [{"path": row["path"], "missing": row["missing"]}
            for row in index(root) if row["missing"]]


def render(rows, chars=900):
    """Rows -> the text form: path, language, size, title, then each section it has."""
    blocks = []
    for row in rows:
        parts = ["%s  (%s, %d lines)" % (row["path"], row["language"], row["lines"])]
        if row["title"]:
            parts.append("  " + row["title"])
        if len(row["missing"]) == len(SECTIONS):
            parts.append("  <no header: this module is not documented>")
        for name in SECTIONS:
            body = row["sections"].get(name)
            if not body:
                continue
            if len(body) > chars:
                body = body[:chars].rstrip() + " ..."
            parts.append("  " + name)
            parts.extend("    " + line for line in body.splitlines())
        blocks.append("\n".join(parts))
    return "\n\n".join(blocks)


def guide(root=None, query=None, path=None, limit=6, chars=900):
    """Answer "which module owns this?" over the headers.

    No query, no path -> one line per source file (the map). `path` -> that module's header.
    `query` -> the best `limit` documented matches, ranked, with their sections.
    """
    rows = index(root)
    if path:
        want = str(path).replace(os.sep, "/").lstrip("./")
        hits = [row for row in rows if row["path"] == want or row["path"].endswith("/" + want)]
        if not hits:
            return {"ok": True, "kind": "guide", "path": want, "modules": [],
                    "text": "no module at %s; map() lists the files, grep() searches them" % want}
        return {"ok": True, "kind": "guide", "modules": hits, "text": render(hits, chars)}
    if query:
        terms = [term for term in _TERMS.split(str(query).lower()) if term]
        scored = []
        for row in rows:
            if not row["sections"]:
                continue
            head = (row["path"] + " " + row["title"]).lower()
            hay = head + " " + " ".join(row["sections"].values()).lower()
            score = sum(3 * head.count(term) + hay.count(term) for term in terms)
            if score:
                scored.append((score, row["path"], row))
        scored.sort(key=lambda item: (-item[0], item[1]))
        hits = [item[2] for item in scored[:max(1, int(limit))]]
        if not hits:
            return {"ok": True, "kind": "guide", "query": str(query), "modules": [],
                    "text": "no documented module matches %r; try map() or grep()" % str(query)}
        return {"ok": True, "kind": "guide", "query": str(query), "modules": hits,
                "text": render(hits, chars)}
    return {"ok": True, "kind": "guide", "modules": rows,
            "text": "\n".join("%s  %s" % (row["path"], row["title"] or "<no header>")
                              for row in rows),
            "documented": len([row for row in rows if row["sections"]]), "files": len(rows)}
