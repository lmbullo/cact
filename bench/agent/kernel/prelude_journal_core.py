"""prelude_journal_core: _commit and the write-through open() — PRELUDE fragment 2/5a.

WHAT IT IS
  Raw child source `_JOURNAL_CORE`: the refusal hints, `_Writer`, the journalled `open()`,
  `_resolve`, `_read_text`, `_digest` and `_commit`, the one journal entry point behind every
  mutation. prelude_journal.py concatenates it before the snapshot and diff fragments.

ENTRY POINTS
  _JOURNAL_CORE   source text; inside: _HINT, _Writer, open, _resolve, _read_text, _digest,
                  _commit

INVARIANTS
  fragment, not an API: every mutation commits through _commit, so its row/snapshot/CELL changed
  ordering and the journal file shape are unchanged. open() is write-through, never a second
  write path.

WHERE TO CHANGE
  journal row, snapshot diff, _CELL_CHANGED ... _commit
  journalled file modes and hints ........... _Writer, open, _HINT
"""
_JOURNAL_CORE = r"""_HINT = {
    "cat": "read(path)", "head": "read(path, start=1, end=N)",
    "tail": "read(path, start=total-N)", "less": "read(path)",
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


"""
