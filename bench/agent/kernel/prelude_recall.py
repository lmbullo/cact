"""prelude_recall: recall(), recall_tool() and page() — PRELUDE fragment 4/5e.

WHAT IT IS
  Raw child source `_RECALL`: recall over the persisted `_OBS` log, the typed `recall_tool`
  wrapper and `page()`, which reads a slice of one `_OBS` entry. prelude_read.py concatenates
  it after the search verbs and before skill().

ENTRY POINTS
  _RECALL   source text; inside: _recall_hits, recall, recall_tool, page

INVARIANTS
  fragment, not an API: `_OBS` is the child namespace's observation log (also persisted to
  KERNEL_OBS_LOG), and a missing handle stays a structured refusal.

WHERE TO CHANGE
  recall matching and limits ... _recall_hits, recall, recall_tool
  handle paging ............... page
"""
_RECALL = r"""def _recall_hits(pattern, context=100):
    # Every matching snippet in _OBS before paging: the caller needs the true total.
    try:
        rx = _re.compile(pattern)
    except _re.error:
        rx = _re.compile(_re.escape(pattern))
    out = []
    for i, entry in enumerate(globals().get("_OBS") or []):
        for field in ("stdout", "stderr", "result"):
            text = entry.get(field) or ""
            for match in rx.finditer(text):
                start = max(0, match.start() - int(context))
                end = min(len(text), match.end() + int(context))
                while start > 0 and text[start - 1].isalnum():
                    start -= 1
                while end < len(text) and text[end].isalnum():
                    end += 1
                snippet = text[start:end]
                out.append({"obs": i, "field": field, "source": "session",
                            "snippet": snippet, "text": snippet,
                            "handle": "_OBS[%d]['%s']" % (i, field)})
    return out


def recall(pattern, limit=10, context=100, offset=0):
    # Search the full output of earlier cells, including turns a fold removed. `limit` is the
    # page size, never the total: the caller pages with `offset` and reads `next`.
    items = _recall_hits(pattern, context)
    start = max(0, int(offset or 0))
    size = None if limit is None else max(0, int(limit))
    return items[start:] if size is None else items[start:start + size]


def recall_tool(pattern, limit=10, context=100, offset=0):
    # Typed session search: the same _OBS snippets as recall(), with a text rendering so the
    # model does not need a python cell to recover a digested card. `total` is every match;
    # `shown` is this page and `next.offset` is the resume cursor.
    items = _recall_hits(pattern, context)
    total = len(items)
    start = max(0, int(offset or 0))
    size = None if limit is None else max(0, int(limit))
    page = items[start:] if size is None else items[start:start + size]
    end = start + len(page)
    scanned = len(globals().get("_OBS") or [])
    text = "\n".join("[%s] %s: %s" % (item["handle"], item["field"], item["text"])
                     for item in page)
    if not page and total and start >= total:
        note = "end of results (offset %d >= total %d)" % (start, total)
    elif not page:
        note = "no matches for %r in %d earlier cell(s)" % (pattern, scanned)
    else:
        note = None
    if note:
        text = "note: " + note
    return {"ok": True, "kind": "recall", "items": page,
            "shown": len(page), "total": total, "offset": start,
            "next": {"offset": end, "unit": "snippet"} if end < total else None,
            "scanned": scanned, "count": total, "note": note, "text": text}


def page(handle, offset=0, limit=8000, field="stdout"):
    # Read a slice of one _OBS entry by the exact handle a card named. This is the recovery
    # path behind every card clip and every digest: no workspace I/O, no shell, and an
    # unknown handle is the same structured refusal as any other verb.
    shown = str(handle or "").strip()
    match = _re.fullmatch(r"_OBS\[(-?\d+)\](?:\[['\"](stdout|stderr|result)['\"]\])?", shown)
    if not match:
        return _fail("page", "bad_handle", "%r is not an _OBS[n] handle" % (handle,),
                     "pass the exact handle a card named, like _OBS[12]")
    index = int(match.group(1))
    if match.group(2):
        field = match.group(2)
    if field not in ("stdout", "stderr", "result"):
        return _fail("page", "bad_field", "field must be stdout, stderr or result",
                     "page(handle, field='result') reads the structured envelope")
    obs = globals().get("_OBS") or []
    if index < 0:
        index += len(obs)
    if index < 0 or index >= len(obs):
        return _fail("page", "unknown_handle",
                     "no %s in this session (%d entries)" % (shown, len(obs)),
                     "page() reads only the handles this session's cards named")
    value = (obs[index] or {}).get(field)
    text = "" if value is None else value if isinstance(value, str) else repr(value)
    total = len(text)
    start = max(0, int(offset or 0))
    size = 8000 if limit is None else max(0, int(limit))
    chunk = text[start:start + size]
    end = start + len(chunk)
    has_more = end < total
    result = {"ok": True, "kind": "obs", "handle": shown, "field": field,
              "offset": start, "shown": len(chunk), "total": total, "unit": "char",
              "next": {"offset": end, "unit": "char"} if has_more else None,
              "truncated": False, "has_more": has_more, "text": chunk}
    if total and start >= total:
        result["note"] = "offset %d is past the end (%d chars)" % (start, total)
    return result


"""
