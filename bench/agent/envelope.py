"""The typed observation envelope: one shape for every verb result.

WHAT IT IS
  A typed verb answers with data, not with a terminal transcript. `normalize(tool, value)`
  turns whatever a PRELUDE helper returned into the one dict a loop, a trace and another
  agent can read without guessing:

    ok       bool
    op       the verb name
    kind     the helper's own kind (file/matches/map/git_diff/...)
    data     small, tool-specific fields (path, count, occurrences, ...)
    page     {shown, total, offset, next, unit, has_more, truncated, complete} or None
    handle   the `_OBS[n]` address of the full delivered text, or None
    notes    human-readable notes as a list (never the only copy of a fact)
    error    {code, message, use} when ok is false

  `page` is the only paging contract. `shown` is a count (or the inclusive [a, b] range the
  helper already reports), `offset` is the cursor of the FIRST unit this response delivered,
  `next` is the cursor to resume at, `unit` names what the cursor counts
  (line/char/hit/file/entry/module), `has_more` says the file/result continues, and
  `truncated`/`complete` are one pair: a built-in default page limit cut output the caller did
  not ask to have cut (`truncated: true`, `complete: false`), or the request was served
  (`truncated: false`, `complete: true`) even when `has_more` is true. A helper may keep extra
  top-level keys for python callers; the envelope does not repeat them, so a model never sees
  the same fact twice.

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
  - An empty payload (`{}`) from a named tool is `ok:false`/`internal_error`, never a
    success: a missing result means the verb crashed or never answered.
  - `complete == not truncated`, everywhere. `has_more` is separate and always means the
    underlying file/result continues; it never implies a failed request. A helper reports
    `truncated` only when a built-in default (page size, max_chars) cut the output; an
    explicit window (read `end`, `max_files`, `max_entries`, `max_results`, `max_chars`) is
    served, so it is complete with `has_more` true.
  - `offset` is never the next cursor: it is the start of what this response delivered
    (0-based index for map/guide/outline/search, the first line for read, the first char for
    git). `next` is the only resume cursor.

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
    "guide": "module", "guides": "module", "recall": "snippet", "git_diff": "char", "git_show": "char",
    "git_log": "commit",
}
BULK = frozenset({"text", "raw", "stdout", "stderr", "matches", "files", "entries",
                  "items", "modules", "hunk", "outlines", "maps"})


def _shown_count(shown):
    """A `shown` value (count or (first, last)) -> the number of units delivered, or None."""
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
    """The one paging contract for a verb result: {shown, total, offset, next, unit, has_more,
    truncated, complete, ...}, or None when the result has no paging fields at all.

    `next` comes back as a replayable argument object: {"start": N} for read, {"offset": N} for
    every other pager. `complete` means this request was served; `has_more` means the result
    continues."""
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
    # A repeated read is a pointer, not a delivery: `shown` must not claim a page of text.
    pointer = value.get("delivered") is False
    if pointer:
        shown = 0
    has_more = bool(value.get("has_more") or nxt is not None)
    # One pair, everywhere: `truncated` says a default limit cut output the caller asked for;
    # `complete` is exactly its inverse. `has_more` never changes either one.
    truncated = bool(value.get("truncated"))
    complete = not truncated
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
            "has_more": has_more, "truncated": truncated, "complete": complete}
    if pointer:
        page["pointer"] = True
    if isinstance(value.get("hits"), int):
        page["hits"] = value["hits"]
    return page


def normalize(tool, value, handle=None):
    """Any verb result -> the canonical envelope: ok/op/kind/data/page/handle/notes/error."""
    if not isinstance(value, dict):
        return {"ok": True, "op": str(tool), "kind": str(tool), "data": {},
                "page": None, "handle": handle, "notes": [], "error": None}
    if not value:
        # An empty payload for a named tool is a missing result, never a success: the driver
        # may have crashed before it could answer, and `{}` used to normalize to ok:true.
        return {"ok": False, "op": str(tool), "kind": str(tool) + "_failed",
                "error": {"code": "internal_error",
                          "message": "no result from %s" % tool,
                          "use": "the verb raised - this is a tool bug, not your arguments"},
                "handle": handle, "notes": [], "data": {}, "page": None}
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
