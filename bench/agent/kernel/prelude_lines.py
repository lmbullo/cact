"""prelude_lines: edit_many() and splice(), the line-addressed edits — PRELUDE fragment 5/5d.

WHAT IT IS
  Raw child source `_LINES`: `edit_many()` (several files in one cell) and `splice()` (replace
  1-based inclusive lines). Both journal as one cell. prelude_verbs.py concatenates them after
  edit().

ENTRY POINTS
  _LINES   source text; inside: edit_many, splice

INVARIANTS
  fragment, not an API: edit_many keeps its atomic validation of every edit before writing, and
  splice keeps its 1-based inclusive line contract; both commit through `_commit`.

WHERE TO CHANGE
  multi-file edits ... edit_many
  line replacement ... splice
"""
_LINES = r"""def edit_many(edits=None, path=None):
    # One typed edit: edits=[{path, old, new}, ...]. Validate every edit against the
    # in-memory bodies first, then commit; a bad old string changes nothing. The single
    # path/old/new shape stays available as the python `edit()` helper, not as this tool.
    if edits is None:
        return _fail("edit_failed", "bad_arguments", "edits is required",
                     "edits=[{path, old, new}, ...]")
    if not isinstance(edits, (list, tuple)) or not edits:
        return _fail("edit_failed", "bad_arguments",
                     "edits must be a non-empty list of {path, old, new}",
                     "edits=[{path, old, new}, ...]")
    # Validate the whole batch's shape first. A missing path is the common model error
    # ("continue editing the file I just read"), and the old single message blamed `old`,
    # so a batch could be rejected repeatedly without naming the actual defect. Report every
    # malformed item at once; atomicity still guarantees nothing is written.
    problems = []
    for index, item in enumerate(edits):
        if not isinstance(item, dict):
            problems.append({"index": index, "reason": "each edit must be an object",
                             "keys": []})
            continue
        item_path = item.get("path") or path
        if not isinstance(item_path, str) or not item_path:
            problems.append({"index": index,
                             "reason": "missing path: every edits[] item needs its own path",
                             "keys": sorted(item)})
            continue
        old = item.get("old")
        if not isinstance(old, str) or not old:
            problems.append({"index": index, "path": item_path,
                             "reason": "old must be a non-empty string",
                             "keys": sorted(item)})
        elif not isinstance(item.get("new"), str):
            problems.append({"index": index, "path": item_path,
                             "reason": "new must be a string (it may be empty)",
                             "keys": sorted(item)})
    if problems:
        first = problems[0]
        return _fail("edit_failed", "bad_arguments", first["reason"],
                     "edits=[{path, old, new}, ...]; every item needs its own path and a non-empty old",
                     index=first["index"], problems=problems)

    originals = {}
    bodies = {}
    order = []
    matches = []
    writes = 0
    for index, item in enumerate(edits):
        item_path = item.get("path") or path
        old = item.get("old")
        new = item.get("new")
        target, rel = _resolve(item_path)
        if target is None:
            return _outside("edit_failed", rel, "edit", index=index, path=rel)
        if rel not in originals:
            if not _os.path.isfile(target):
                return _fail("edit_failed", "not_found", "no file at %s" % rel,
                             "map(path) lists files; write(path, content) creates one",
                             index=index, path=rel)
            originals[rel] = _read_text(target)
            bodies[rel] = originals[rel]
            order.append((target, rel))
        body = bodies[rel]
        updated, info = _replace_text(body, old, new, bool(item.get("all")))
        if updated is None:
            closest = info.get("closest") or []
            extra = {key: value for key, value in info.items()
                     if key not in ("reason", "closest", "use")}
            return _fail("edit_failed", info["reason"],
                         "old text was not unique in %s" % rel if info["reason"] == "ambiguous"
                         else "old text does not match %s" % rel,
                         info.get("use") or "copy a closest line exactly, or splice(path, start, end, text)",
                         index=index, path=rel, closest=closest,
                         context=_edit_context(rel, body, closest), **extra)
        bodies[rel] = updated
        matches.append((rel, info.get("match", "exact"), info.get("lines")))
        writes += 1
    changed = []
    for target, rel in order:
        if bodies[rel] != originals[rel]:
            _commit(target, rel, bodies[rel], "edit")
            changed.append(rel)
    fuzzy = [{"path": rel, "lines": lines} for rel, match, lines in matches
             if match == "indentation"]
    return {"ok": True, "kind": "edits", "paths": changed, "edits": len(edits),
            "replaced": writes, "matches": [match for _, match, _ in matches],
            "matched_lines": fuzzy,
            "note": "matched leading-indentation differences" if fuzzy else None,
            "text": "edited %d file(s) in %d replacement(s): %s" % (
                len(changed), writes, ", ".join(changed)) if changed else "no change"}


def splice(path, start, end=None, text=""):
    # Replace 1-based inclusive lines start..end with text. `end` is REQUIRED: the old default
    # (end=start) silently replaced a line for a caller who meant to insert. Insertion is
    # end=start-1; text="" deletes the range. read() already returns numbered lines.
    target, rel = _resolve(path)
    if target is None:
        return _outside("wrong_call", rel, "splice", path=rel)
    if not _os.path.isfile(target):
        return _fail("not_found", "not_found", "no file at %s" % rel,
                     "map(path) lists files; write(path, content) creates one", path=rel)
    if end is None:
        return _fail("bad_args", "bad_arguments",
                     "splice needs end: it replaces start..end inclusive, and omitting end is "
                     "not an insert",
                     "to insert before line start pass end=start-1; to replace one line pass end=start",
                     path=rel, start=start)
    a, problem = _int_arg(start, "start", 1)
    if problem is not None:
        return problem
    b, problem = _int_arg(end, "end", 0)
    if problem is not None:
        return problem
    body = _read_text(target)
    lines = body.splitlines()
    total = len(lines)
    replacement = str(text).splitlines()
    if b < a:
        if a > total + 1:
            return _fail("splice_failed", "range_past_end",
                         "insert position %d is past the end (%d lines)" % (a, total),
                         "read(path) shows total_lines; insert at %d" % (total + 1),
                         path=rel, total_lines=total, next=total + 1)
        new_lines = lines[:a - 1] + replacement + lines[a - 1:]
    else:
        if a > total or b > total:
            return _fail("splice_failed", "range_past_end",
                         "range %d-%d is past the end (%d lines)" % (a, b, total),
                         "read(path) shows total_lines and the numbered lines",
                         path=rel, total_lines=total, next=a)
        new_lines = lines[:a - 1] + replacement + lines[b:]
    trailing = "\n" if body.endswith("\n") or not lines else ""
    updated = "\n".join(new_lines) + trailing
    entry = _commit(target, rel, updated, "splice")
    lo = max(1, a - 3)
    hi = min(len(new_lines), a + len(replacement) + 3)
    hunk = "\n".join(f"{n:>6}| {new_lines[n - 1]}" for n in range(lo, hi + 1)) if hi >= lo else ""
    return {"ok": True, "kind": "splice", "path": rel,
            "replaced": [a, b] if b >= a else [a, a - 1],
            "lines": entry["after_lines"], "adds": entry["adds"], "dels": entry["dels"],
            "hunk": hunk, "hunk_lines": [lo, hi] if hi >= lo else []}

"""
