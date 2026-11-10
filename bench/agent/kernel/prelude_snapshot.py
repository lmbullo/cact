"""prelude_snapshot: the start-of-session tree state and its delta — PRELUDE fragment 2/5b.

WHAT IT IS
  Raw child source `_SNAPSHOT`: `_BASE`/`_BASE_READY`/`_LAST_STATE`, `_snapshot()`, `_tree_state`,
  `_row`, `_changed` and `_delta`. prelude_journal.py concatenates it after `_commit`, which
  records into the same state.

ENTRY POINTS
  _SNAPSHOT   source text; inside: _BASE, _BASE_READY, _LAST_STATE, _SNAP_PARTIAL, _snap_files,
              _snapshot, _tree_state, _row, _changed, _delta

INVARIANTS
  fragment, not an API: `_snapshot()` records the session-start tree that changes()/diff()/undo()
  compare against, and `_snapshot()` itself must stay the last statement of the assembled PRELUDE
  (it is appended by prelude_verbs.py, not here). _delta is the structured per-cell contract.
  the walk is bounded: KERNEL_SNAPSHOT_BYTES (default 64 MB) and KERNEL_SNAPSHOT_FILES (default
  40000) cap it, so a home directory cannot pin gigabytes; past the cap the views cover the
  watched prefix and report `snapshot_partial` instead of reading the rest.

WHERE TO CHANGE
  tree state and its delta ... _snapshot, _tree_state, _row, _changed, _delta
  the memory/read budget ..... _SNAP_MAX_BYTES, _SNAP_MAX_FILES, _snap_files
"""
_SNAPSHOT = r"""_BASE = {}
_BASE_READY = False
_LAST_STATE = None
_SNAP_MAX_BYTES = max(0, int(_os.environ.get("KERNEL_SNAPSHOT_BYTES") or 64_000_000))
_SNAP_MAX_FILES = max(0, int(_os.environ.get("KERNEL_SNAPSHOT_FILES") or 40000))
_SNAP_PARTIAL = False


def _snap_files():
    # The one bounded traversal the session snapshot and its per-cell delta share. Cap by file
    # count and by cumulative bytes read, then stop: the rest of a huge tree is not tracked.
    global _SNAP_PARTIAL
    total = 0
    count = 0
    for full in _walk(_ROOT):
        try:
            size = _os.path.getsize(full)
        except OSError:
            continue
        if size > 2_000_000:
            continue
        if count >= _SNAP_MAX_FILES or total + size > _SNAP_MAX_BYTES:
            _SNAP_PARTIAL = True
            return
        total += size
        count += 1
        yield full


def _snapshot():
    global _BASE_READY, _LAST_STATE
    if _BASE_READY:
        return
    for full in _snap_files():
        rel = _os.path.relpath(full, _ROOT)
        try:
            body = _read_text(full)
        except (OSError, UnicodeError):
            continue
        _BASE[rel] = {"hash": _digest(body), "text": body}
    _BASE_READY = True
    _LAST_STATE = {rel: row["text"] for rel, row in _BASE.items()}


def _tree_state():
    _snapshot()
    now = {}
    for full in _snap_files():
        rel = _os.path.relpath(full, _ROOT)
        try:
            if _os.path.getsize(full) > 2_000_000:
                continue
            body = _read_text(full)
        except (OSError, UnicodeError):
            continue
        now[rel] = {"hash": _digest(body), "text": body}
    if _SNAP_PARTIAL:
        # A file that shrank past the moving byte boundary is still present; keep its base row
        # so `changes()` does not report a phantom deletion. Files beyond the prefix are not
        # tracked at all, which is what `snapshot_partial` tells the caller.
        for rel in _BASE:
            if rel in now:
                continue
            try:
                if _os.path.getsize(_os.path.join(_ROOT, rel)) > 2_000_000:
                    continue
            except OSError:
                continue
            now[rel] = _BASE[rel]
    return now


def _row(rel, before, after):
    if before is None:
        diff_text = "".join(_difflib.unified_diff(
            [], after.splitlines(True), fromfile="/dev/null", tofile="b/" + rel, n=3))
        state = "added"
    elif after is None:
        diff_text = "".join(_difflib.unified_diff(
            before.splitlines(True), [], fromfile="a/" + rel, tofile="/dev/null", n=3))
        state = "deleted"
    else:
        diff_text = "".join(_difflib.unified_diff(
            before.splitlines(True), after.splitlines(True), fromfile="a/" + rel, tofile="b/" + rel, n=3))
        state = "modified"
    adds = sum(1 for line in diff_text.splitlines() if line.startswith("+") and not line.startswith("+++"))
    dels = sum(1 for line in diff_text.splitlines() if line.startswith("-") and not line.startswith("---"))
    return state, adds, dels, diff_text


def _changed(now=None):
    now = now or _tree_state()
    out = []
    for rel in sorted(set(_BASE) | set(now)):
        before = _BASE.get(rel, {}).get("text")
        after = now.get(rel, {}).get("text")
        if before != after:
            out.append((rel, before, after))
    return out


def _delta():
    global _LAST_STATE
    _snapshot()
    now = {rel: row["text"] for rel, row in _tree_state().items()}
    prev = _LAST_STATE if _LAST_STATE is not None else {rel: row["text"] for rel, row in _BASE.items()}
    deltas = {}
    for rel in sorted(set(prev) | set(now)):
        before, after = prev.get(rel), now.get(rel)
        if before != after:
            deltas[rel] = _row(rel, before, after)[3]
    # A typed edit outside the snapshot prefix is not in prev/now; the journal already holds
    # the hunk _commit computed, so the state card still shows what the model wrote.
    for rel in _CELL_CHANGED:
        if rel in deltas:
            continue
        for entry in reversed(_JOURNAL):
            if entry.get("path") != rel:
                continue
            # A file created and removed in the same cell has a create row but is gone now;
            # it is not a change, and the card must not claim one.
            if entry.get("deleted") or _os.path.exists(_os.path.join(_ROOT, rel)):
                deltas[rel] = str(entry.get("diff") or "")
            break
    _LAST_STATE = now
    return deltas


"""
