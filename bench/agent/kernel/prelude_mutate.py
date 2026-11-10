"""prelude_mutate: write(), delete() and mkdir() — PRELUDE fragment 5/5b.

WHAT IT IS
  Raw child source `_MUTATE`: the three basic file verbs. write() replaces and refuses to drop a
  tail without force=True; delete() removes one file; mkdir() creates parents. Every one commits
  through `_commit`. prelude_verbs.py concatenates it after the verb page.

ENTRY POINTS
  _MUTATE   source text; inside: write, delete, mkdir

INVARIANTS
  fragment, not an API: each verb returns the same envelope and journals through `_commit`; the
  literal-newline note and wrong_call refusals are the previous bytes.

WHERE TO CHANGE
  write/force tail rule ... write
  delete and mkdir ........ delete, mkdir
"""
_MUTATE = r"""def write(path, content, force=False):
    target, rel = _resolve(path)
    if target is None:
        return _outside("wrong_call", rel, "write", path=rel)
    if not isinstance(content, str):
        return _fail("bad_args", "bad_arguments", "content must be a string",
                     "write(path, content, force=False)", path=rel)
    existed = _os.path.isfile(target)
    if existed and not force:
        current = _read_text(target)
        if len(content) < len(current):
            # Any shorter body drops a tail; a prefix of the old file is only the clearest
            # case. Refusing only the prefix let `write(path, "zzz")` delete six lines.
            prefix = current.startswith(content)
            return _fail("refused_truncation", "shorter_write",
                         ("the new content is %d chars, shorter than the current %d, so this "
                          "write would delete the file's tail" % (len(content), len(current)))
                         + (" (it is a prefix of the current file)" if prefix else ""),
                         "pass force=True to replace the file, or edit()/splice() the region",
                         path=rel, existing_lines=len(current.splitlines()),
                         new_lines=len(content.splitlines()), prefix=prefix)
    note = _literal_newline_note(rel, content)
    entry = _commit(target, rel, content, "write")
    result = {"ok": True, "kind": "write", "path": rel, "created": entry["created"],
              "lines": entry["after_lines"], "line_delta": None if entry["created"] else entry["after_lines"] - entry["before_lines"],
              "adds": entry["adds"], "dels": entry["dels"]}
    if note:
        result["note"] = note
    return result


def delete(path):
    # One file, through the tree state diff/undo already tracks. `rm`/`mv` through exec are
    # refused because they would bypass the journal; this is the typed way to remove one.
    target, rel = _resolve(path)
    if target is None:
        return _outside("wrong_call", rel, "delete", path=rel)
    if not _os.path.exists(target):
        return {"ok": True, "kind": "no_change", "path": rel, "reason": "missing",
                "text": "no file at %s" % rel}
    if _os.path.isdir(target):
        return _fail("wrong_call", "is_directory", "delete() takes one file, not a directory",
                     "map(path) lists a directory; delete each file you mean", path=rel)
    try:
        before = _read_text(target)
        entry = _commit(target, rel, None, "delete")
    except OSError as exc:
        return _fail("delete_failed", "delete_failed", str(exc),
                     "is a process holding it? map(path) lists what is there", path=rel)
    if entry is None:
        return {"ok": True, "kind": "no_change", "path": rel, "reason": "missing"}
    return {"ok": True, "kind": "delete", "path": rel, "deleted": True,
            "lines": len(before.splitlines()),
            "text": "deleted %s (%d lines)" % (rel, len(before.splitlines()))}


def mkdir(path):
    # Empty directories are not journal rows (there is no file content to undo), but the
    # typed verb keeps `mkdir` out of the shell gate and gives a clear result.
    target, rel = _resolve(path)
    if target is None:
        return _outside("wrong_call", rel, "mkdir", path=rel)
    if _os.path.exists(target):
        if _os.path.isdir(target):
            return {"ok": True, "kind": "no_change", "path": rel, "reason": "exists",
                    "text": "directory already exists: %s" % rel}
        return _fail("wrong_call", "not_a_directory", "%s exists and is not a directory" % rel,
                     "delete(path) removes the file, or pick another name", path=rel)
    try:
        _os.makedirs(target, exist_ok=True)
    except OSError as exc:
        return _fail("mkdir_failed", "mkdir_failed", str(exc),
                     "mkdir(path) creates parents; check the parent is a directory", path=rel)
    return {"ok": True, "kind": "mkdir", "path": rel, "created": True,
            "text": "created %s/" % rel}


"""
