"""prelude_snapshot: the start-of-session tree state and its delta — PRELUDE fragment 2/5b.

WHAT IT IS
  Raw child source `_SNAPSHOT`: `_BASE`/`_BASE_READY`/`_LAST_STATE`, `_snapshot()`, `_tree_state`,
  `_row`, `_changed` and `_delta`. prelude_journal.py concatenates it after `_commit`, which
  records into the same state.

ENTRY POINTS
  _SNAPSHOT   source text; inside: _BASE, _BASE_READY, _LAST_STATE, _snapshot, _tree_state,
              _row, _changed, _delta

INVARIANTS
  fragment, not an API: `_snapshot()` records the session-start tree that changes()/diff()/undo()
  compare against, and `_snapshot()` itself must stay the last statement of the assembled PRELUDE
  (it is appended by prelude_verbs.py, not here). _delta is the structured per-cell contract.

WHERE TO CHANGE
  tree state and its delta ... _snapshot, _tree_state, _row, _changed, _delta
"""
_SNAPSHOT = r"""_BASE = {}
_BASE_READY = False
_LAST_STATE = None


def _snapshot():
    global _BASE_READY, _LAST_STATE
    if _BASE_READY:
        return
    for full in _walk(_ROOT):
        rel = _os.path.relpath(full, _ROOT)
        try:
            if _os.path.getsize(full) > 2_000_000:
                continue
            body = _read_text(full)
        except (OSError, UnicodeError):
            continue
        _BASE[rel] = {"hash": _digest(body), "text": body}
    _BASE_READY = True
    _LAST_STATE = {rel: row["text"] for rel, row in _BASE.items()}


def _tree_state():
    _snapshot()
    now = {}
    for full in _walk(_ROOT):
        rel = _os.path.relpath(full, _ROOT)
        try:
            if _os.path.getsize(full) > 2_000_000:
                continue
            body = _read_text(full)
        except (OSError, UnicodeError):
            continue
        now[rel] = {"hash": _digest(body), "text": body}
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
    _LAST_STATE = now
    return deltas


"""
