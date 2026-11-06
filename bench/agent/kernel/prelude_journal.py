"""prelude_journal: the mutation journal — the one path every write goes through.

WHAT IT IS
  Fragment 2/5 of `PRELUDE` (see prelude.py). It owns `_commit` (journal row, snapshot diff,
  `_CELL_CHANGED`), the `_Writer`/`open()` wrapper that catches an unclosed write, the mutation
  hint table `_HINT`, and the readers of the journal: `_snapshot`, `_tree_state`, `_row`,
  `_changed`, `_delta`, `session_diff()`, `changes()`, `diff()`, `undo()`.

ENTRY POINTS
  Inside the cell namespace: _commit, open, _resolve, _read_text, _digest, _snapshot,
  _tree_state, _row, changes(), diff(path, max_chars), undo(path, n), session_diff(path,
  max_chars)
  `_HINT` maps a refused shell verb to the typed helper that replaces it.

INVARIANTS
  - Every mutating verb in prelude_verbs.py commits through `_commit`; nothing may write a
    tracked file another way, or changes()/diff()/undo() start lying.
  - `open()` is rebound here and routes a write through `_Writer`; headers.py pins
    `builtins.open` before this runs so the header index is never journalled.
  - `_BASE` is the session-start snapshot; `_snapshot()` (last fragment) fills it, so these
    functions must tolerate `_BASE == {}` until then.
  - `undo()` restores session-start text and drops files created this session; it never
    touches git.

WHERE TO CHANGE
  The journal row or delta ................ _commit, _row, _delta
  What `undo` restores .................... undo
  The refusal hints ....................... _HINT (and run_prelude's `_mutating_command`)
"""
from __future__ import annotations

# --- part 2/5: the journal, the write-through open(), snapshot/diff/undo ------------------
_JOURNAL = r"""_HINT = {
    "cat": "read(path)", "head": "read(path, offset=1, limit=N)",
    "tail": "read(path, offset=total-N)", "less": "read(path)",
    "more": "read(path)", "wc": "read(path)['total_lines']",
    "stat": "map(path)", "grep": "grep(pattern, path)",
    "rg": "grep(pattern, path, regex=True)", "ag": "grep(pattern, path)",
    "sed": "splice(path, start, end, text) or edit(path, old, new)",
    "awk": "read(path) with Python string slicing",
    "find": "map(path)", "ls": "map(path)", "tree": "map(path)",
    "du": "map(path)", "tee": "splice(path, start, end, text) or write(path, content)",
    "touch": "write(path, '')", "cp": "read + write",
    "mv": "write + os.remove", "rm": "os.remove",
    "truncate": "splice(path, 1, read(path)['total_lines'], '') or write(path, '')",
}


class _Writer:
    def __init__(self, target, rel, base):
        self.target = target
        self.rel = rel
        self._parts = [base]
        self._dirty = False
        self.closed = False

    def write(self, text):
        self._parts.append(str(text))
        self._dirty = True
        self._flush_to_journal()
        return len(text)

    def writelines(self, lines):
        self._parts.extend(str(line) for line in lines)
        self._dirty = True
        self._flush_to_journal()

    def _flush_to_journal(self):
        if not self._dirty:
            return
        _commit(self.target, self.rel, "".join(self._parts), "open()")
        self._dirty = False

    def flush(self):
        self._flush_to_journal()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self._flush_to_journal()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def __del__(self):
        # `open(path, "w").write(text)` has no name to close; commit when the writer is
        # collected so the journal does not silently miss a write the model just made.
        try:
            self.close()
        except Exception:
            pass


def open(file, mode="r", *args, **kwargs):
    if any(ch in mode for ch in "wax+") and "b" not in mode and "r" not in mode:
        target, rel = _resolve(file)
        if target is not None:
            base = ""
            if "a" in mode and _os.path.isfile(target):
                base = _read_text(target)
            return _Writer(target, rel, base)
    if "b" not in mode and "errors" not in kwargs:
        kwargs["errors"] = "replace"
    return _open(file, mode, *args, **kwargs)


def _resolve(path):
    raw = str(path).strip() or "."
    target = _os.path.normpath(raw if _os.path.isabs(raw) else _os.path.join(_ROOT, raw))
    if target != _ROOT and not target.startswith(_ROOT + _os.sep):
        return None, raw
    return target, _os.path.relpath(target, _ROOT)


def _read_text(path):
    with _open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _digest(text):
    return _hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:12]


def _commit(target, rel, body, source):
    global _MUT, _JSEQ, _IGNORE
    before = None
    try:
        if _os.path.isfile(target):
            before = _read_text(target)
    except OSError:
        before = None
    if body is None:
        # A typed delete is a journal row too: before text is the diff base and the tree
        # state is what changes()/undo() read. Never write a placeholder empty file.
        if before is None:
            return None
        _os.remove(target)
        body_text = ""
    else:
        _os.makedirs(_os.path.dirname(target) or _ROOT, exist_ok=True)
        with _open(target, "w", encoding="utf-8") as fh:
            fh.write(body)
        body_text = body
    _READS_SESSION.pop(rel, None)
    if rel == ".gitignore":
        _IGNORE = None
    _MUT = True
    _JSEQ += 1
    diff_text = "".join(_difflib.unified_diff(
        (before or "").splitlines(True), body_text.splitlines(True),
        fromfile=("/dev/null" if before is None else "a/" + rel),
        tofile=("/dev/null" if body is None else "b/" + rel), n=3))
    adds = sum(1 for line in diff_text.splitlines() if line.startswith("+") and not line.startswith("+++"))
    dels = sum(1 for line in diff_text.splitlines() if line.startswith("-") and not line.startswith("---"))
    entry = {"seq": _JSEQ, "path": rel, "source": source, "created": before is None,
             "before_lines": 0 if before is None else len(before.splitlines()),
             "after_lines": len(body_text.splitlines()), "adds": adds, "dels": dels,
             "before_hash": None if before is None else _digest(before),
             "after_hash": None if body is None else _digest(body_text), "diff": diff_text}
    if body is None:
        entry["deleted"] = True
    _JOURNAL.append(entry)
    _SNAP[entry["seq"]] = before
    if len(_JOURNAL) > 200:
        old = _JOURNAL.pop(0)
        _SNAP.pop(old["seq"], None)
    if rel not in _CELL_CHANGED:
        _CELL_CHANGED.append(rel)
    return entry


_BASE = {}
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


def session_diff(path=None, max_chars=8000):
    # Explicit name for the session journal; `git diff` is the working-tree tool.
    return diff(path=path, max_chars=max_chars)


def changes():
    now = _tree_state()
    files = {}
    for rel, before, after in _changed(now):
        state, adds, dels, _ = _row(rel, before, after)
        files[rel] = {"state": state, "adds": adds, "dels": dels}
    return {"ok": True, "kind": "changes", "files": files, "count": len(files)}


def diff(path=None, max_chars=8000):
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
    if not targets:
        return {"ok": True, "kind": "undo", "restored": [],
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
    return {"ok": True, "kind": "undo", "restored": restored}


"""
