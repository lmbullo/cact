"""prelude_diff: session_diff() and diff_tool() — PRELUDE fragment 2/5c.

WHAT IT IS
  Raw child source `_DIFF`: `session_diff()` (paged journal diff) and `diff_tool()`, the typed
  wrapper the driver exposes. prelude_journal.py concatenates them after the snapshot state.

ENTRY POINTS
  _DIFF   source text; inside: session_diff, diff_tool

INVARIANTS
  fragment, not an API: both read the journal rows and the snapshot state from the earlier
  fragments; the page envelope (offset/next/has_more) is unchanged.

WHERE TO CHANGE
  journal diff and its paging ... session_diff, diff_tool
"""
_DIFF = r"""def session_diff(path=None, max_chars=8000):
    # Explicit name for the session journal; `git diff` is the working-tree tool.
    return diff(path=path, max_chars=max_chars)


def diff_tool(path=None, max_chars=None):
    # The typed session_diff: the same change set as diff(), as the one envelope shape.
    # `ignored` counts journaled writes to paths the tree walk skips (bench/.tmp/), so an
    # empty diff next to seven writes cannot read as "nothing changed".
    explicit = max_chars is not None
    limit = 8000 if max_chars is None else max(0, int(max_chars))
    rel_filter = None
    if path is not None:
        _, rel_filter = _resolve(path)
    now = _tree_state()
    texts = []
    files = []
    for rel, before, after in _changed(now):
        if rel_filter is not None and rel != rel_filter:
            continue
        state, adds, dels, text = _row(rel, before, after)
        texts.append(text)
        files.append({"path": rel, "state": state, "adds": adds, "dels": dels})
    journaled = set()
    for entry in _JOURNAL:
        journaled.add(str(entry.get("path")))
    ignored = sorted(p for p in journaled
                     if p not in now and p not in _BASE
                     and (rel_filter is None or p == rel_filter))
    joined = "\n".join(texts)
    total = len(joined)
    text = joined if limit <= 0 else joined[:limit]
    # A passed max_chars is an explicit window: the page is served, `has_more` says the rest
    # is there. Only the default cap makes the response `truncated`.
    truncated = len(text) < total and not explicit
    if truncated:
        text = text + "\n...[diff truncated; page with offset=%d]" % len(text)
    notes = []
    if ignored:
        notes.append("%d journaled write(s) go to a path the tree walk skips (bench/.tmp/ or "
                     "gitignored); they are not in this diff: %s"
                     % (len(ignored), ", ".join(ignored[:6])))
    result = {"ok": True, "kind": "diff", "path": rel_filter, "files": files,
              "chars": len(text), "total_chars": total, "shown": len(text),
              "total": total, "offset": 0, "unit": "char",
              "next": {"offset": len(text), "unit": "char"} if truncated else None,
              "truncated": truncated, "has_more": truncated,
              "ignored": len(ignored), "ignored_paths": ignored, "text": text}
    if notes:
        result["note"] = "; ".join(notes)
    return result


"""
