"""prelude_undo: changes(), diff() and undo() — PRELUDE fragment 2/5e.

WHAT IT IS
  Raw child source `_UNDO`: the session change list, the text diff and undo (restoring the
  pre-cell bytes of the last journal rows). prelude_journal.py concatenates it last, so it sees
  the journal and snapshot state.

ENTRY POINTS
  _UNDO   source text; inside: changes, diff, undo

INVARIANTS
  fragment, not an API: undo() restores through `_commit`'s journal data, so its refusals and
  restored/up-to-date shapes stay exactly as before.

WHERE TO CHANGE
  change list, diff text, undo restore ... changes, diff, undo
"""
_UNDO = r"""def changes():
    now = _tree_state()
    files = {}
    for rel, before, after in _changed(now):
        state, adds, dels, _ = _row(rel, before, after)
        files[rel] = {"state": state, "adds": adds, "dels": dels}
    # A typed mutation beyond the snapshot budget is still in the journal; list it from there
    # rather than hiding work the model just did.
    journaled = {}
    for entry in _JOURNAL:
        rel = entry.get("path")
        if rel is not None and rel not in _BASE:
            journaled[rel] = entry
    for rel, entry in journaled.items():
        if rel in files:
            continue
        files[rel] = {"state": "deleted" if entry.get("deleted") else "modified",
                      "adds": int(entry.get("adds") or 0),
                      "dels": int(entry.get("dels") or 0)}
    result = {"ok": True, "kind": "changes", "files": files, "count": len(files),
              "snapshot_partial": _SNAP_PARTIAL}
    if _SNAP_PARTIAL:
        result["note"] = ("the session snapshot stopped at its memory budget; only journaled "
                          "paths outside the watched prefix are listed (raise "
                          "KERNEL_SNAPSHOT_BYTES to widen it)")
    return result


def diff(path=None, max_chars=8000):
    rel_filter = None
    if path is not None:
        _, rel_filter = _resolve(path)
    now = _tree_state()
    texts = []
    files = []
    seen = set()
    for rel, before, after in _changed(now):
        if rel_filter is not None and rel != rel_filter:
            continue
        state, adds, dels, text = _row(rel, before, after)
        texts.append(text)
        files.append({"path": rel, "state": state, "adds": adds, "dels": dels})
        seen.add(rel)
    # A typed mutation beyond the snapshot budget is still in the journal; list it from there
    # rather than hiding work the model just did.
    journaled = {}
    for entry in _JOURNAL:
        rel = entry.get("path")
        if rel is not None and rel not in _BASE:
            journaled[rel] = entry
    for rel, entry in journaled.items():
        if rel in seen or (rel_filter is not None and rel != rel_filter):
            continue
        files.append({"path": rel, "state": "deleted" if entry.get("deleted") else "modified",
                      "adds": int(entry.get("adds") or 0),
                      "dels": int(entry.get("dels") or 0)})
        if entry.get("diff"):
            texts.append(str(entry["diff"]))
    joined = "\n".join(texts)
    if len(joined) > max_chars:
        joined = joined[:max_chars] + "\n...[diff truncated]"
    # A string, not a dict: the model reads and slices the diff directly, and `changes()`
    # already carries the per-file metadata.
    return joined


def undo(path=None, n=1):
    global _MUT
    now = _tree_state()
    targets = []
    for rel, before, after in _changed(now):
        if path is not None:
            _, want = _resolve(path)
            if rel != want:
                continue
        targets.append((rel, before))
    # Typed edits past the snapshot budget are recoverable from the journal: the first row
    # for the path carries its session-start text in _SNAP. Without this, undo() would claim
    # the tree already matched while the model's own write was still there.
    covered = {rel for rel, _ in targets}
    first, last = {}, {}
    for entry in _JOURNAL:
        rel = entry.get("path")
        if rel is None or rel in _BASE or rel in covered:
            continue
        first.setdefault(rel, entry)
        last[rel] = entry
    for rel, entry in last.items():
        if path is not None:
            _, want = _resolve(path)
            if rel != want:
                continue
        try:
            current = _read_text(_os.path.join(_ROOT, rel))
        except (OSError, UnicodeError):
            current = None
        after = None if current is None else _digest(current)
        if after != entry.get("after_hash"):
            continue
        start = first[rel]
        if start.get("created"):
            before = None
        elif start.get("seq") in _SNAP:
            before = _SNAP[start["seq"]]
        else:
            continue
        targets.append((rel, before))
        covered.add(rel)
    if not targets:
        return {"ok": True, "kind": "undo", "restored": [],
                "snapshot_partial": _SNAP_PARTIAL,
                "note": "workspace already matches its session-start state"}
    restored = []
    for rel, before in reversed(targets):
        target, _ = _resolve(rel)
        try:
            if before is None:
                _os.remove(target)
            else:
                _os.makedirs(_os.path.dirname(target) or _ROOT, exist_ok=True)
                with _open(target, "w", encoding="utf-8") as fh:
                    fh.write(before)
        except OSError as exc:
            return {"ok": False, "kind": "undo", "path": rel,
                    "error": {"code": "restore_failed", "message": str(exc)}}
        _MUT = True
        restored.append(rel)
    return {"ok": True, "kind": "undo", "restored": restored,
            "snapshot_partial": _SNAP_PARTIAL}


"""
