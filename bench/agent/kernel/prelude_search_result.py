"""prelude_search_result: grep/search result envelopes — PRELUDE fragment 4/5c.

WHAT IT IS
  Raw child source `_SEARCH_RESULT`: `_count_result` and `_grep_result`, the two envelopes the
  search verbs return (count rows and hit tables with paging, context and truncation).
  prelude_read.py concatenates it before `_SEARCH`.

ENTRY POINTS
  _SEARCH_RESULT   source text; inside: _count_result, _grep_result

INVARIANTS
  fragment, not an API: the search fragment calls both. offset/next/has_more/truncated and the
  `matches` / `search_counts` / `search_sections` kinds are the wire contract.

WHERE TO CHANGE
  count rows and hit-table shapes ... _count_result, _grep_result
"""
_SEARCH_RESULT = r"""def _count_result(counts, occurrences=0, note=None):
    total = sum(int(value) for value in counts.values())
    text = "\n".join("%s:%d" % (path, count) for path, count in sorted(counts.items()))
    if note:
        text = (text + "\n" if text else "") + "note: " + note
    # `shown`/`total` count the rows this response delivers (one per file with matches);
    # `hits` is the matching-line count. Reporting 234 lines as `shown` while delivering 25
    # rows made the page look half-served.
    return _Result({"ok": True, "kind": "search_counts", "counts": counts,
                    "by_file": {path: {"count": count, "lines": []}
                                for path, count in counts.items()},
                    "occurrences": occurrences, "hits": total,
                    "shown": len(counts), "total": len(counts), "unit": "file",
                    "offset": 0, "next": None, "truncated": False, "complete": True,
                    "text": text, "note": note})


def _grep_result(hits, occurrences, truncated, max_chars=12000, offset=0, total=None,
                 all_numbers=None):
    # Render each file once. Adjacent/overlapping hits share their context lines, so the
    # model sees every line once; a line that is both a hit and another hit's context keeps
    # the `:` marker. The structured `matches` stays complete for python callers.
    grouped = {}
    order = []
    for hit in hits:
        path = hit.get("path")
        if path not in grouped:
            grouped[path] = {}
            order.append(path)
        rows = grouped[path]
        for item in hit.get("before") or []:
            rows.setdefault(int(item["line"]), [item["text"], False])
        rows[int(hit["line"])] = [hit["text"], True]
        for item in hit.get("after") or []:
            rows.setdefault(int(item["line"]), [item["text"], False])
    lines = []
    for path in order:
        for number in sorted(grouped[path]):
            text, is_hit = grouped[path][number]
            lines.append("%s:%s%s %s" % (path, number, ":" if is_hit else "-", text))
    full = "\n".join(lines)
    total = len(hits) if total is None else int(total)
    by_file = {}
    for hit in hits:
        path = hit.get("path")
        entry = by_file.setdefault(path, {"count": 0, "lines": []})
        entry["count"] += 1
        entry["lines"].append(int(hit["line"]))
    all_numbers = sorted({int(hit["line"]) for hit in hits}) if all_numbers is None else all_numbers
    range_count = _range_count(all_numbers)
    # Two counts, two names: `hits` is matching lines, `hit_ranges` is how many contiguous
    # ranges they fall in. One unlabeled `ranges` next to `total` made a model guess.
    common = {"ok": True, "kind": "matches", "matches": hits,
              "truncated": truncated, "shown": len(hits), "total": total, "hits": total,
              "hit_ranges": range_count, "unit": "hit",
              "offset": int(offset), "by_file": by_file,
              "next": {"offset": int(offset) + len(hits), "unit": "hit"} if int(offset) + len(hits) < total else None}
    if int(offset) >= total and not hits:
        common["note"] = "end of results (offset %d >= total %d)" % (int(offset), total)
    if max_chars and len(full) > int(max_chars):
        # A broad context search can exceed the card budget and lose its middle. Deliver
        # locators instead of a silently digested blob. Hit lines stay exact; only context
        # is absent here (the caller can re-run with context=0 or max_chars=0 for it).
        locators = "\n".join(
            "%s:%s: %s" % (hit["path"], hit["line"], hit.get("text") or "")
            for hit in hits)
        elided = any(hit.get("before") or hit.get("after") for hit in hits)
        common.update({"text_elided": elided, "text_chars": len(full)})
        if elided and int(max_chars) != 12000:
            common["note"] = ("broad result: max_chars is a locator threshold, not a cap; "
                              "max_chars=0 disables locator mode")
        common["text"] = locators
        return _Result(common)
    common["text"] = full
    return _Result(common)


"""
