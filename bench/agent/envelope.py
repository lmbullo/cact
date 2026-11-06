"""The typed observation envelope: one shape for every verb result.

WHAT IT IS
  A typed verb answers with data, not with a terminal transcript. `normalize(tool, value)`
  turns whatever a PRELUDE helper returned into the one dict a loop, a trace and another
  agent can read without guessing:

    ok       bool
    op       the verb name
    kind     the helper's own kind (file/matches/map/git_diff/...)
    data     small, tool-specific fields (path, count, occurrences, ...)
    page     {shown, total, offset, next, unit, has_more, complete} or None
    handle   the `_OBS[n]` address of the full delivered text, or None
    notes    human-readable notes as a list (never the only copy of a fact)
    error    {code, message, use} when ok is false

  `page` is the only paging contract. `shown` is a count (or the inclusive [a, b] range the
  helper already reports), `next` is the next cursor, `unit` names what the cursor counts
  (line/char/hit/file/entry/module), `has_more` says the file/result continues, and
  `complete` says this request was served (an explicit read window is complete even when the
  file continues). A helper may keep its legacy top-level keys for python callers; the
  envelope does not repeat them, so a model never sees the same fact twice.

ENTRY POINTS
  normalize(tool, value, handle=None) -> dict
  page_of(kind, value) -> dict | None
  UNITS / BULK

INVARIANTS
  - Stdlib-only and side-effect-free: the parent imports it; the kernel child does not.
  - It never invents a page. `page_of` returns None when the helper returned no paging
    fields; a caller must not treat None as "complete".
  - Bulk payloads (`text`, `stdout`, `stderr`, `matches`, `files`, `entries`, `items`,
    `modules`, `hunk`) stay out of the envelope. Their delivery is stdout; their address is
    the handle. That keeps the structured half small and makes duplication impossible.
  - `complete` is false only when the helper reports `truncated` (the page limit cut the
    request) or, for non-file results, when `next`/`has_more` remains. An explicit read
    window is complete even when `has_more` is true. `has_more` is separate and always
    means the underlying file/result continues.

WHERE TO CHANGE
  A new paging field ................. `page_of` and the helper that returns it
  What the model sees structurally ... `normalize`'s `data` filter
  The rendered card ................... `bench/agent/state_loop.py`
"""
from __future__ import annotations

import json

UNITS = {
    "read": "line", "file": "line", "files": "file", "search": "hit", "grep": "hit", "matches": "hit",
    "map": "file", "maps": "file", "outline": "entry", "outlines": "entry",
    "search_sections": "section",
    "guide": "module", "recall": "snippet", "git_diff": "char", "git_show": "char",
    "git_log": "commit",
}
BULK = frozenset({"text", "raw", "stdout", "stderr", "matches", "files", "entries",
                  "items", "modules", "hunk", "results", "outlines", "maps"})


def _shown_count(shown):
    if isinstance(shown, (list, tuple)) and len(shown) == 2:
        try:
            return max(0, int(shown[1]) - int(shown[0]) + 1)
        except (TypeError, ValueError):
            return 0
    try:
        return int(shown)
    except (TypeError, ValueError):
        return None


def page_of(kind, value):
    if not isinstance(value, dict):
        return None
    fields = ("shown", "total", "total_lines", "total_files", "total_entries",
              "offset", "next", "has_more", "truncated", "file_count", "count")
    if not any(key in value for key in fields):
        return None
    shown = _shown_count(value.get("shown"))
    if shown is None:
        if isinstance(value.get("entries"), list):
            shown = len(value["entries"])
        elif isinstance(value.get("files"), list):
            shown = len(value["files"])
        elif isinstance(value.get("items"), list):
            shown = len(value["items"])
        elif isinstance(value.get("modules"), list):
            shown = len(value["modules"])
        elif str(value.get("kind") or "") == "files" and value.get("count") is not None:
            shown = int(value.get("count") or 0)
        else:
            shown = 0
    total = value.get("total")
    if total is None:
        preferred = ("total_entries" if kind in ("outline", "outlines")
                     else "total_files" if kind in ("map", "maps")
                     else "total_lines")
        for key in (preferred, "total_entries", "total_files", "total_lines", "file_count"):
            if value.get(key) is not None:
                total = value[key]
                break
    if total is None and value.get("count") is not None and not isinstance(value.get("count"), (list, dict)):
        total = value["count"]
    try:
        total = int(total) if total is not None else None
    except (TypeError, ValueError):
        total = None
    kind_name = str(value.get("kind") or kind)
    offset = value.get("offset")
    nxt = value.get("next")
    unit = value.get("unit")
    if isinstance(nxt, dict):
        if offset is None and kind_name not in ("file", "files"):
            offset = nxt.get("offset")
        unit = nxt.get("unit", unit)
        nxt = nxt.get("offset")
    elif nxt is not None:
        try:
            nxt = int(nxt)
        except (TypeError, ValueError):
            nxt = None
    if offset is None and isinstance(value.get("shown"), (list, tuple)) and len(value["shown"]) == 2:
        offset = value["shown"][0]
    if offset is not None:
        try:
            offset = int(offset)
        except (TypeError, ValueError):
            offset = None
    if kind_name in ("map", "maps") and offset is None:
        offset = 0
    # A repeated read is a pointer, not a delivery: `shown` must not claim a page of text.
    pointer = value.get("delivered") is False
    if pointer:
        shown = 0
    has_more = bool(value.get("has_more") or nxt is not None)
    # `complete` is "this request was served"; `has_more` is "the file/result continues".
    complete = not bool(value.get("truncated"))
    if has_more and kind_name not in ("file", "files"):
        complete = False
    # `next` is a replayable argument object, not a bare number whose unit the caller has to
    # remember: read is line-based, every other pager is offset-based.
    if nxt is None:
        next_arg = None
    elif kind_name in ("file", "files"):
        next_arg = {"start": nxt}
    else:
        next_arg = {"offset": nxt}
    page = {"shown": shown, "total": total, "offset": offset, "next": next_arg,
            "unit": unit or UNITS.get(kind) or UNITS.get(kind_name),
            "has_more": has_more, "complete": complete}
    if pointer:
        page["pointer"] = True
    if isinstance(value.get("hits"), int):
        page["hits"] = value["hits"]
    return page


def normalize(tool, value, handle=None):
    if not isinstance(value, dict):
        return {"ok": True, "op": str(tool), "kind": str(tool), "data": {},
                "page": None, "handle": handle, "notes": [], "error": None}
    out = {"ok": bool(value.get("ok", True)), "op": str(tool),
           "kind": value.get("kind") or str(tool),
           "error": value.get("error") if isinstance(value.get("error"), dict) else None,
           "handle": handle, "notes": [], "data": {},
           "page": page_of(str(value.get("kind") or tool), value)}
    note = value.get("note")
    if isinstance(note, list):
        out["notes"] = [str(item) for item in note if item]
    elif note:
        out["notes"] = [str(note)]
    # Invert the filter: only bulk payloads and page fields are consumed elsewhere. A new
    # verb field reaches the card without editing this allow-list.
    consumed = {"ok", "kind", "text", "raw", "stdout", "stderr", "note", "error",
                "shown", "total", "total_lines", "total_files", "total_entries", "offset",
                "next", "has_more", "truncated", "handle", "file_count"}
    for key, item in value.items():
        if key in BULK or key in consumed or item is None:
            continue
        out["data"][key] = item
    return out


def parse(result):
    """Cell.result (a JSON summary string) -> the envelope it carries, or None."""
    if isinstance(result, dict):
        return result
    if not isinstance(result, str):
        return None
    try:
        value = json.loads(result)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None
