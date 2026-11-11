"""headers: every module's contract as data; the kernel `guide()` verb and the CLI gate.

WHAT IT IS
  Header = WHAT IT IS / ENTRY POINTS / INVARIANTS / WHERE TO CHANGE; this module parses it.
  index(root) rows: path, language, lines, header_lines, title, sections, missing.

ENTRY POINTS
  python3 bench/guide.py [--query TEXT | --module PATH | --json | --check]
  guide(root=None, query=None, path=None, limit=None, chars=20000, offset=0, headers=0) -> dict
  index(root=None) -> list[dict];  problems(root=None);  in_scope(path) -> bool
  header(path) -> (text, lines);  split(text) -> (title, sections);  render(rows, chars=20000)

INVARIANTS
  read-only: no writes, no network, no bench import, no state; unparsable -> empty header, no raise.
  MAX_HEADER=24; HEADER_ALLOW={path: lines} shrink-only; in_scope = bench/*.py, bench/agent/**,
  tui/src/**, tui/python/**; other paths ungated.  kernel/prelude.py execs this source.
  SKIP_DIRS skips node_modules, __pycache__, fixtures, traces/ws, dist, build, dot-dirs, rust/.
  guide: x3 path+title, x1 else; score then path; miss -> ok:false/not_found; no match -> note.
  render truncates at chars; _OPEN pins builtins.open: the journal must not reroute reads.

WHERE TO CHANGE
  grammar .... SECTIONS, _SECTION_RE, _QUOTES, split()   walk ... SKIP_DIRS, LANGUAGES, _walk()
  budget ..... MAX_HEADER, HEADER_ALLOW, in_scope(), problems()
  ranking .... guide(), render();  kernel verb ... kernel/prelude.py;  CLI ... bench/guide.py
"""
from __future__ import annotations

import difflib
import os
import re

SECTIONS = ("WHAT IT IS", "ENTRY POINTS", "INVARIANTS", "WHERE TO CHANGE")
SKIP_DIRS = frozenset({
    ".git", "archive", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".tmp", ".cache", "dist", "build", "fixtures", "edge_fixtures", "harness_fixtures",
    "hard_fixtures", "hard_hidden", "sandbox_fixtures", "sandbox_hidden", "traces", "ws", "rust",
})
LANGUAGES = {".py": "python", ".ts": "ts", ".tsx": "tsx", ".js": "javascript"}
READ_LIMIT = 400_000
MAX_HEADER = 24
_SCOPE_DIRS = ("bench/agent/", "tui/src/", "tui/python/")
# Shrink-only: a module whose header is not yet <= MAX_HEADER lines is listed here with its
# current line count. Convert it, then delete the row; never add one and never raise a count.
# An entry that reaches MAX_HEADER lines is reported as stale until it is removed.
HEADER_ALLOW = {}
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


def in_scope(path):
    """True when `path` has a header budget: bench/*.py, bench/agent/**, tui/src/**, tui/python/**."""
    text = str(path).replace(os.sep, "/")
    if text.startswith("bench/") and text.endswith(".py") and text.count("/") == 1:
        return True
    return text.startswith(_SCOPE_DIRS)


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
            "header_lines": len(text.splitlines()) if text else 0,
            "title": title,
            "sections": sections,
            "missing": [name for name in SECTIONS if not sections.get(name)],
        })
    return rows


def problems(root=None):
    """Rows breaking the header contract, each with `header_lines`; empty means conforming."""
    gaps = []
    for row in index(root):
        count = row["header_lines"]
        allowed = HEADER_ALLOW.get(row["path"])
        gated = in_scope(row["path"])
        over = gated and count > (MAX_HEADER if allowed is None else allowed)
        stale = gated and allowed is not None and count <= MAX_HEADER
        reason = "missing sections" if row["missing"] else (
            "header over %d lines" % MAX_HEADER if over else "stale allowlist entry")
        if row["missing"] or over or stale:
            gaps.append({"path": row["path"], "missing": row["missing"],
                         "header_lines": count, "budget": MAX_HEADER,
                         "allowed": allowed, "reason": reason})
    return gaps


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
        total = len(scored)
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
            if total and start >= total:
                message = ("offset %d is past the end; %d documented module(s) match %r"
                           % (start, total, str(query)))
            else:
                message = "no documented module matches %r; try map() or search()" % str(query)
            return {"ok": True, "kind": "guide", "query": str(query), "modules": [],
                    "shown": 0, "total": total, "offset": start, "next": None,
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
                "shown": len(public), "total": total, "offset": start, "next": nxt,
                "unit": "module", "truncated": end < len(scored) and not explicit,
                "note": note, "text": body}
    public = [{k: row[k] for k in ("path", "language", "lines", "title")} for row in rows]
    return {"ok": True, "kind": "guide", "modules": public,
            "shown": len(public), "total": len(public), "next": None,
            "text": "\n".join("%s  %s" % (row["path"], _short_title(row["title"]) or "<no header>")
                              for row in public),
            "documented": len([row for row in rows if row["sections"]]), "files": len(rows)}
