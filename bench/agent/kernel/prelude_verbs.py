"""prelude_verbs: the remaining typed verbs and the start-up snapshot.

WHAT IT IS
  Fragment 5/5 of `PRELUDE` (see prelude.py): `verbs()` (the help catalog), the mutation verbs
  `write`, `delete`, `mkdir`, `edit`, `edit_many`, `splice`, `apply` (unified diff parsing:
  `_parse_patch`, `_patch_path`, `_hunk_old`, `_hunk_new`) and `commit({path: text})`. It ends
  with the `_snapshot()` call that records the session-start tree.

ENTRY POINTS
  write(path, content, force=False)      replace a file (refuses any shorter body without force)
  delete(path), mkdir(path)              the journalled removals/creations
  edit(path, old, new, all=False)        one replacement; edit_many(edits=[...]) the atomic batch
  splice(path, start, end, text)         1-based inclusive line replacement (end is required)
  apply(patch)                           a unified diff, several files, one cell
  commit({path: text}, force=False)      several writes in one cell

INVARIANTS
  - Every one of them commits through `_commit`; `edit` reports ambiguity instead of guessing
    and validates the whole batch before writing; `apply` fails on a context mismatch rather
    than fuzzy-matching.
  - `write()` refuses ANY shorter body without `force=True` (a prefix of the old file is just
    the clearest case), and notes a body whose only line breaks are a literal backslash-n.
  - `splice()` requires `end`: the old default (end=start) silently replaced a line for a
    caller who meant to insert. Insertion is `end=start-1`.
  - A failed `edit` carries the enclosing definition's lines from `outline()`, so `closest:`
    is never the only answer.
  - `_snapshot()` must stay the LAST statement of the assembled PRELUDE: it records `_BASE`,
    which is what changes()/diff()/undo() compare against.

WHERE TO CHANGE
  A mutation verb's semantics .......... the verb itself here
  The unified-diff parser .............. _parse_patch, _hunk_old, _hunk_new
  The verb catalog `verbs()` ........... verbs.VERBS (the registry) — this only renders it
"""
from __future__ import annotations

# --- part 5/5: verbs, the patch parser, commit(), the start-up snapshot -------------------
_VERBS = r"""def verbs(topic=None):
    # The live catalog: the same registry the parent uses for tool specs. Summaries come
    # from KERNEL_VERBS; signatures are introspected here, so they cannot drift from the
    # functions a typed call actually dispatches to.
    import inspect as _inspect
    meta = globals().get("_VERB_META") or {}
    out = []
    for name in sorted(meta):
        if topic and str(topic) not in name:
            continue
        fn = globals().get((meta[name].get("function") or name))
        if not callable(fn):
            continue
        try:
            signature = str(_inspect.signature(fn))
        except (TypeError, ValueError):
            signature = "(...)"
        out.append({"name": name, "signature": signature,
                    "summary": meta[name].get("summary") or "",
                    "mutates": bool(meta[name].get("mutates"))})
    if topic and not out:
        # A silent empty list read as success and cost a retry. An unknown topic is a refusal
        # with the nearest names, so the next call is right.
        names = sorted(name for name in meta if callable(globals().get((meta[name].get("function") or name))))
        near = _difflib.get_close_matches(str(topic), names, n=3, cutoff=0.3) or []
        return _fail("unknown_verb", "unknown_verb", "no verb matches %r" % (topic,),
                     "help() lists every verb; help(name) matches a name",
                     available=near or names)
    return {"ok": True, "kind": "verbs", "verbs": out,
            "text": "\n".join("%s%s  %s" % (item["name"], item["signature"], item["summary"])
                             for item in out)}


help = verbs


# A literal backslash-n in source code is real content; in prose it is a model that
# double-escaped. Only the second case gets a note, so a code write stays quiet.
_ESCAPE_OK_EXT = {".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".json", ".c", ".h",
                  ".cc", ".go", ".rs", ".sh", ".yml", ".yaml", ".toml", ".css", ".java"}


def _literal_newline_note(path, content):
    if "\n" in content or "\r" in content:
        return None
    if "\\n" not in content:
        return None
    if _os.path.splitext(str(path))[1].lower() in _ESCAPE_OK_EXT:
        return None
    return ("the content has a literal backslash-n and no real newline, so it was written as "
            "%d line(s) with %d visible escape(s); pass real newlines if you meant line breaks, "
            "and read(path) to check what landed" % (len(content.splitlines()),
                                                     content.count("\\n")))


def write(path, content, force=False):
    target, rel = _resolve(path)
    if target is None:
        return _fail("wrong_call", "outside_root", "%s is outside the workspace" % rel,
                     _OUTSIDE_USE, path=rel)
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
        return _fail("wrong_call", "outside_root", "%s is outside the workspace" % rel,
                     _OUTSIDE_USE, path=rel)
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
        return _fail("wrong_call", "outside_root", "%s is outside the workspace" % rel,
                     _OUTSIDE_USE, path=rel)
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


def _closest_lines(body, old):
    probe = old.splitlines()[0].strip() if old.splitlines() else old
    return [{"line": number, "text": line.strip()[:200]}
            for number, line in enumerate(body.splitlines(), 1)
            if _difflib.SequenceMatcher(None, probe, line.strip()).ratio() > 0.6][:3]


def _replace_text(body, old, new, all=False):
    # Exact replacement is the contract. If that misses, accept a unique block whose lines
    # differ only in leading whitespace: copying an indented prose bullet into a header that
    # starts at column 1 was one of the repeated edit failures. Never fuzzy-match content;
    # a non-unique or differently-worded block still refuses and returns the closest lines.
    count = body.count(old)
    if count == 1 and not all:
        return body.replace(old, new, 1), {"match": "exact"}
    if count and all:
        return body.replace(old, new), {"match": "exact", "replaced": count}
    if count > 1:
        return None, {"reason": "ambiguous", "count": count,
                      "use": "extend old until unique, or pass all=True"}
    old_lines = old.splitlines()
    if not old_lines or all:
        return None, {"reason": "not_found", "closest": _closest_lines(body, old)}
    body_lines = body.splitlines(keepends=True)
    plain = [line.rstrip("\r\n") for line in body_lines]
    wanted = [line.lstrip() for line in old_lines]
    hits = []
    for index in range(0, len(plain) - len(wanted) + 1):
        if [line.lstrip() for line in plain[index:index + len(wanted)]] == wanted:
            hits.append(index)
    if len(hits) == 1:
        start = hits[0]
        end = start + len(wanted)
        before = "".join(body_lines[:start])
        after = "".join(body_lines[end:])
        replacement = new
        if (after or body.endswith(("\n", "\r"))) and not replacement.endswith(("\n", "\r")):
            replacement += "\n"
        return before + replacement + after, {"match": "indentation",
                                              "lines": [start + 1, end]}
    if len(hits) > 1:
        return None, {"reason": "ambiguous", "count": len(hits),
                      "lines": [index + 1 for index in hits],
                      "use": "several blocks match after ignoring leading whitespace; include more lines"}
    return None, {"reason": "not_found", "closest": _closest_lines(body, old)}


def _edit_context(rel, body, closest=None):
    # The enclosing definition's lines, from outline(): `closest: []` on a short file is
    # unactionable, and a failed edit should show the region that actually exists.
    lines = body.splitlines()
    numbers = [int(item.get("line") or 0) for item in (closest or []) if isinstance(item, dict)]
    try:
        result = outline(rel, max_entries=2000, keys=False)
    except Exception:
        result = {}
    entries = [item for item in (result.get("entries") or [])
               if int(item.get("line") or 0) > 0]
    owner = None
    for number in numbers or [1]:
        for item in entries:
            start = int(item.get("line") or 0)
            end = int(item.get("end") or start)
            if start <= number <= end:
                owner = item
    if owner is None and entries:
        owner = entries[0]
    if owner is None:
        if not lines:
            return None
        first, last = 1, min(len(lines), 20)
        return {"name": "(whole file)", "line": first, "end": last,
                "lines": [[n, lines[n - 1]] for n in range(first, last + 1)]}
    first = int(owner.get("line") or 1)
    last = min(int(owner.get("end") or first), first + 19, len(lines))
    if last < first:
        return None
    return {"name": owner.get("name"), "line": first, "end": last,
            "lines": [[n, lines[n - 1]] for n in range(first, last + 1)]}


def edit(path, old, new, all=False, replace_all=None):
    target, rel = _resolve(path)
    if target is None:
        return _fail("wrong_call", "outside_root", "%s is outside the workspace" % rel,
                     _OUTSIDE_USE, path=rel)
    if not _os.path.isfile(target):
        return _fail("not_found", "not_found", "no file at %s" % rel,
                     "map(path) lists files; write(path, content) creates one", path=rel)
    if replace_all is not None:
        all = bool(replace_all)
    body = _read_text(target)
    updated, info = _replace_text(body, old, new, all)
    if updated is None:
        closest = info.get("closest") or []
        return _fail("edit_failed", info["reason"],
                     "old text was not unique" if info["reason"] == "ambiguous"
                     else "old text does not match %s" % rel,
                     "copy a closest line exactly, or splice(path, start, end, text)",
                     path=rel, closest=closest,
                     context=_edit_context(rel, body, closest))
    entry = _commit(target, rel, updated, "edit")
    at = updated.find(new)
    first = updated[:at].count("\n") + 1 if at >= 0 else 1
    lines = updated.splitlines()
    lo = max(1, first - 3)
    hi = min(len(lines), first + new.count("\n") + 3)
    hunk = "\n".join(f"{n:>6}| {lines[n - 1]}" for n in range(lo, hi + 1))
    return {"ok": True, "kind": "edit", "path": rel,
            "replaced": info.get("replaced", 1),
            "match": info.get("match", "exact"), "matched_lines": info.get("lines"),
            "lines": entry["after_lines"], "hunk": hunk, "hunk_lines": [lo, hi],
            "adds": entry["adds"], "dels": entry["dels"]}


def edit_many(edits=None, path=None):
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
            return _fail("edit_failed", "outside_root", "%s is outside the workspace" % rel,
                         _OUTSIDE_USE, index=index, path=rel)
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
        return _fail("wrong_call", "outside_root", "%s is outside the workspace" % rel,
                     _OUTSIDE_USE, path=rel)
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

def _parse_patch(patch):
    files = []
    current = None
    hunk = None
    for raw in str(patch).splitlines():
        if raw.startswith("--- "):
            current = {"src": raw[4:].split("\t")[0].strip(), "dst": None, "hunks": []}
            files.append(current)
            hunk = None
        elif raw.startswith("+++ ") and current is not None:
            current["dst"] = raw[4:].split("\t")[0].strip()
        elif raw.startswith("@@") and current is not None:
            match = _re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", raw)
            if not match:
                return None
            hunk = {"old_start": int(match.group(1)), "lines": []}
            current["hunks"].append(hunk)
        elif hunk is not None and raw[:1] in (" ", "+", "-"):
            hunk["lines"].append(raw)
    return [item for item in files if item["hunks"]]


def _patch_path(value):
    if not value or value == "/dev/null":
        return None
    if value.startswith(("a/", "b/")):
        value = value[2:]
    return value


def _hunk_old(hunk):
    return [line[1:] for line in hunk["lines"] if line[:1] in (" ", "-")]


def _hunk_new(hunk):
    return [line[1:] for line in hunk["lines"] if line[:1] in (" ", "+")]


def apply(patch):
    parsed = _parse_patch(patch)
    if not parsed:
        return _fail("patch_failed", "bad_patch", "no hunks found in the patch text",
                     "apply() takes a real unified diff: --- a/path, +++ b/path, then @@ hunks")
    results = []
    for item in parsed:
        rel = _patch_path(item["dst"] or item["src"])
        if rel is None:
            return _fail("patch_failed", "bad_patch", "the patch has no +++ path",
                         "name the target on a +++ b/path line")
        target, rel = _resolve(rel)
        if target is None:
            return _fail("patch_failed", "outside_root", "%s is outside the workspace" % rel,
                         _OUTSIDE_USE, path=rel)
        exists = _os.path.isfile(target)
        body = _read_text(target) if exists else ""
        lines = body.splitlines()
        for hunk in item["hunks"]:
            old = _hunk_old(hunk)
            new = _hunk_new(hunk)
            base = max(0, int(hunk["old_start"]) - 1)
            position = None
            for offset in range(0, 200):
                for candidate in (base + offset, base - offset):
                    if candidate < 0 or candidate + len(old) > len(lines):
                        continue
                    if lines[candidate:candidate + len(old)] == old:
                        position = candidate
                        break
                if position is not None:
                    break
            if position is None:
                return _fail("patch_failed", "context_mismatch",
                             "the patch context did not match %s" % rel,
                             "read(path) and rebuild the hunk, or use edit(edits=[...])",
                             path=rel, hunk=old[:4])
            lines[position:position + len(old)] = new
        trailing = "\n" if body.endswith("\n") or not exists else ""
        entry = _commit(target, rel, "\n".join(lines) + trailing, "apply")
        results.append({"path": rel, "adds": entry["adds"], "dels": entry["dels"]})
    return {"ok": True, "kind": "apply", "files": results}


def commit(files, force=False):
    if not isinstance(files, dict) or not files:
        return _fail("commit", "bad_arguments", "commit takes a dict of {path: text}",
                     "commit({'a.py': text, 'b.py': text})")
    done = []
    for path, content in files.items():
        if not isinstance(content, str):
            return _fail("commit", "bad_arguments", "content must be text",
                         "commit({'a.py': text})", path=str(path))
        result = write(path, content, force=force)
        if not result.get("ok"):
            inner = result.get("error") or {}
            return _fail("commit", inner.get("code") or "commit_failed",
                         inner.get("message") or "write failed",
                         inner.get("use") or "write(path, content, force=True)",
                         path=str(path), detail=result)
        done.append({"path": result["path"], "adds": result["adds"], "dels": result["dels"]})
    return {"ok": True, "kind": "commit", "files": done}


_snapshot()
"""
