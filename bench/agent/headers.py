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
  a bare `python3 -c`. `kernel/prelude.py` therefore ships *this file's source* to the child in
  KERNEL_HEADERS, execs it into a private `_HEADERS` namespace, and exposes one verb,
  `guide()`, so the verb and `python3 bench/guide.py` run identical code. Everything here is
  stdlib-only (`_OPEN` pins builtins.open before the prelude replaces it), side-effect-free,
  and resolves the tree from the current working directory when `root` is omitted — which is
  why it works unchanged in both processes, and why it must never import `bench` or read
  `__file__`.

ENTRY POINTS
  python3 bench/guide.py [--query TEXT | --module PATH | --json | --check]
  guide(root=None, query=None, path=None, limit=None, chars=20000, offset=0, headers=0) -> dict
  index(root=None) -> list[dict];  problems(root=None)
  header(path) -> (header text, line count);  split(text) -> (title, sections)
  render(rows, chars=20000) -> str

INVARIANTS
  - Read-only. It opens source files and returns dicts; no writes, no network, no imports of
    `bench`, no state between calls. Calling it twice on the same tree is the same answer.
  - A file is "documented" when its docstring / leading block comment carries at least one
    section; `problems()` is stricter and demands all four. A half-written or unparsable
    module yields an empty header, never an exception.
  - `SKIP_DIRS` is the contract surface: `node_modules`, `__pycache__`, every fixture tree
    (`fixtures`, `*_fixtures`, `*_hidden`), the generated/archived evidence (`bench/ws`,
    `bench/traces`, `bench/archive`, `bench/.tmp`, `bench/.cache`), `dist`, `build`, plus any
    dot-directory. Task workspaces, recorded evidence and the retired suites are inputs and
    outputs, not code an agent maintains.
  - `guide(query=...)` scores a term three times in path+title and once everywhere else,
    sorts by score then path, and caps at `limit`. Deterministic: same tree, same order.
  - `headers=N` inlines the full header of the top N hits in the same response, so "which
    module owns this, and what is its contract" is one call, not a second `guide(path)`.
  - A `path` that is not an indexed module is `ok:false`/`not_found` with the nearest paths
    (with a list of paths, the ones that exist are rendered and the rest are noted);
    a query that matches nothing stays `ok:true` with a note (a miss is an answer about the
    index, and the note names the fallback).
  - Rendering truncates each section at `chars` and always keeps the path line, so a card
    never hides which module it is talking about.

WHERE TO CHANGE
  The header grammar (sections, delimiters) ... SECTIONS, _SECTION_RE, _QUOTES, split()
  Which files are indexed ................ SKIP_DIRS, LANGUAGES, _walk()
  Ranking, limits, truncation ............ guide(), render()
  The CLI ............................... bench/guide.py
"""
from __future__ import annotations

import difflib
import os
import re

SECTIONS = ("WHAT IT IS", "ENTRY POINTS", "INVARIANTS", "WHERE TO CHANGE")
SKIP_DIRS = frozenset({
    ".git", "archive", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".tmp", ".cache", "dist", "build", "fixtures", "edge_fixtures", "harness_fixtures",
    "hard_fixtures", "hard_hidden", "sandbox_fixtures", "sandbox_hidden", "traces", "ws",
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
    lines = len(text.splitlines()) if text else 0
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


def problems(root=None):
    """Rows whose header is missing a section. Empty means the tree conforms."""
    return [{"path": row["path"], "missing": row["missing"]}
            for row in index(root) if row["missing"]]


def render(rows, chars=20000):
    """Rows -> text: path/language/size/title, then sections under a total `chars` budget."""
    blocks = []
    remaining = max(0, int(chars or 0))
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
            if remaining <= 0:
                parts.append("  ...[header budget reached]...")
                break
            if len(body) > remaining:
                body = body[:remaining].rstrip() + " ..."
            remaining -= len(body)
            parts.append("  " + name)
            parts.extend("    " + line for line in body.splitlines())
        blocks.append("\n".join(parts))
    return "\n\n".join(blocks)


def _short_title(title):
    title = str(title or "").strip()
    if ". " in title:
        return title.split(". ", 1)[0].rstrip() + "."
    if len(title) > 140:
        return title[:137].rsplit(" ", 1)[0].rstrip() + " ..."
    return title


def _non_indexed_note(root, query, rows):
    """A query that names a real non-source file is not an index miss; say where to look."""
    base = root or os.getcwd()
    indexed = {row["path"] for row in rows}
    text = str(query).lower()
    try:
        names = os.listdir(base)
    except OSError:
        names = []
    for name in names:
        stem = os.path.splitext(name)[0].lower()
        if name in indexed or len(stem) < 3:
            continue
        # Match the filename as a token, not as a substring: a file called `a.txt` must not
        # turn the query "read" into a docs miss.
        if not re.search(r"(?<![\w.-])" + re.escape(stem) + r"(?![\w.-])", text):
            continue
        if os.path.isfile(os.path.join(base, name)):
            return ("%s is not in the contract index (only Python/TS source headers are); "
                    "use map() to list files, outline() for structure, search() for text" % name)
    return None


def guide(root=None, query=None, path=None, limit=None, chars=20000, offset=0, headers=0):
    """Answer "which module owns this?" over the headers.

    No query, no path -> one line per source file (the map). `path` -> that module's header,
    or one header per entry when `path` is a list (several modules, one call).
    `query` -> the best `limit` documented matches, ranked, with their sections; `headers=N`
    also inlines the full header of the top N hits.
    """
    explicit = limit is not None
    page_size = 10 if limit is None else max(1, int(limit))
    rows = index(root)
    if path:
        wants = ([str(item) for item in path] if isinstance(path, (list, tuple, set))
                 else [str(path)])
        hits, missing = [], []
        for raw in wants:
            want = raw.replace(os.sep, "/").lstrip("./")
            found = [row for row in rows
                     if row["path"] == want or row["path"].endswith("/" + want)]
            if found:
                hits.extend(found)
            else:
                missing.append(want)
        if not hits:
            near = difflib.get_close_matches(wants[0], [row["path"] for row in rows],
                                             n=3, cutoff=0.3)
            message = "no module at %s; map() lists the files, search() finds text" % ", ".join(wants)
            return {"ok": False, "kind": "not_found", "path": wants[0], "modules": [],
                    "shown": 0, "total": 0, "next": None, "did_you_mean": near,
                    "error": {"code": "not_found", "message": message,
                              "use": "guide(query=...) ranks modules; map(path) lists files"},
                    "note": message, "text": message}
        text = render(hits, chars)
        result = {"ok": True, "kind": "guide" if len(wants) == 1 else "guides",
                  "modules": hits, "shown": len(hits), "total": len(hits), "next": None,
                  "text": text}
        if missing:
            result["note"] = "no module at " + ", ".join(missing)
            result["text"] = text + "\nnote: " + result["note"]
        return result
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
        note = _non_indexed_note(root, query, rows)
        start = max(0, int(offset or 0))
        hits = [item[2] for item in scored[start:start + page_size]]
        public = [{k: row[k] for k in ("path", "language", "lines", "title")} for row in hits]
        lines = ["%s  (%s, %d lines)  %s" % (
            row["path"], row["language"], row["lines"],
            _short_title(row["title"]) or "<no header>")
            for row in public]
        end = start + len(public)
        if end < len(scored):
            lines.append("(showing %d-%d of %d; pass offset=%d or limit=%d)"
                         % (start + 1, end, len(scored), end, len(scored)))
        if note:
            # The query named a real non-source file. Do not pad it with weak header rows:
            # ``guide`` is the source-contract index, not the docs search.
            return {"ok": True, "kind": "guide", "query": str(query), "modules": [],
                    "shown": 0, "total": 0, "next": None,
                    "note": note, "text": note}
        if not lines:
            message = "no documented module matches %r; try map() or search()" % str(query)
            return {"ok": True, "kind": "guide", "query": str(query), "modules": [],
                    "shown": 0, "total": 0, "next": None,
                    "note": message, "text": message}
        nxt = {"offset": end, "unit": "module"} if end < len(scored) else None
        inlined = ""
        if headers:
            # Inline the first N hits' headers in the same answer: one round-trip instead of
            # guide(query) then guide(path). `chars` still bounds the whole block.
            inlined = render(hits[:max(1, int(headers))], chars)
        body = "\n".join(lines)
        if inlined:
            body = body + "\n\n" + inlined
        return {"ok": True, "kind": "guide", "query": str(query), "modules": public,
                "shown": len(public), "total": len(scored), "offset": start, "next": nxt,
                "unit": "module", "truncated": end < len(scored) and not explicit,
                "note": note, "text": body}
    public = [{k: row[k] for k in ("path", "language", "lines", "title")} for row in rows]
    return {"ok": True, "kind": "guide", "modules": public,
            "shown": len(public), "total": len(public), "next": None,
            "text": "\n".join("%s  %s" % (row["path"], _short_title(row["title"]) or "<no header>")
                              for row in public),
            "documented": len([row for row in rows if row["sections"]]), "files": len(rows)}
