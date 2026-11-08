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
  revert_check(paths, cmd, timeout=120)  does this test fail without the fix? (HEAD swap, then restore)

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
  - `revert_check()` is the ONE verb whose writes bypass `_commit`: it puts HEAD content in
    place, runs a command, and puts the working copy back in a `finally`, so the net effect is
    zero and a journal row would make changes()/diff() report a change that is not there. It
    refuses a dirty tree outside its own paths, never stashes and never checks out, and reports
    `restored: false` with a loud message naming the path when the restore fails.

WHERE TO CHANGE
  A mutation verb's semantics .......... the verb itself here
  The revert-check swap/restore ........ revert_check, _revert_run, _revert_git, _revert_dirty
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


def checks(name=None):
    # Parent-side tool: the loop's Verifier owns the plan, the cache and the rows, so a python
    # cell cannot run it. This stub keeps the registry truthful and help() honest; the typed
    # `checks` call is intercepted before the kernel.
    return _fail("checks", "parent_only", "checks() runs in the agent loop, not a python cell",
                 "call the typed checks tool, or checks(name=...) as a tool")


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
        return _outside("wrong_call", rel, "edit", path=rel)
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
            return _outside("patch_failed", rel, "apply", path=rel)
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


def _revert_bytes(path):
    with _open(path, "rb") as fh:
        return fh.read()


def _revert_digest(data):
    # One digest for the restore proof: the same function reads both sides, so a mismatch is
    # never an encoding artefact.
    return _hashlib.sha256(data).hexdigest()[:16]


def _revert_git(argv):
    # Read-only git, bytes out: `show HEAD:<path>` must not pass through a text codec, because a
    # restore that decodes and re-encodes is not a restore. None when git could not run at all.
    try:
        return _sp.run(["git", "-c", "core.quotepath=false"] + argv, cwd=_ROOT,
                       capture_output=True, timeout=30)
    except (OSError, _sp.TimeoutExpired):
        return None


def _revert_dirty():
    # Every TRACKED path git reports as different from HEAD; None when git itself failed. Untracked
    # files are left out on purpose: a test run leaves build artifacts (`__pycache__/`, `.tmp`)
    # that the HEAD swap cannot reach, so refusing on them would make the verb refuse after every
    # run. `paths` itself is checked for trackedness separately. A rename `R  a -> b` names both
    # sides, so the refusal names the path the caller did not pass.
    proc = _revert_git(["status", "--porcelain=v1", "--untracked-files=no"])
    if proc is None or proc.returncode != 0:
        return None
    out = []
    for raw in proc.stdout.decode("utf-8", "replace").splitlines():
        if len(raw) < 4:
            continue
        rest = raw[3:]
        for part in (rest.split(" -> ") if " -> " in rest else [rest]):
            name = part.strip().strip('"')
            if name and name not in out:
                out.append(name)
    return out


def _revert_run(command, argv, shell, timeout):
    # (exit_code, stdout, stderr, problem). A non-zero exit is the answer, not an error, so
    # `problem` stays None for it; only a timeout or a refused/unstartable spawn sets one.
    try:
        proc = _sp.run(command if shell else list(argv), shell=shell, cwd=_ROOT,
                       capture_output=True, text=True, errors="replace", timeout=float(timeout))
    except _sp.TimeoutExpired:
        return None, "", "", "timeout"
    except Exception as exc:
        return None, "", "%s: %s" % (type(exc).__name__, exc), "spawn_failed"
    return proc.returncode, proc.stdout or "", proc.stderr or "", None


def _revert_output(exit_now, out_now, err_now, exit_original, out_old, err_old):
    # Both runs, tailed: the failing assertion is the answer, and a full test dump would spend the
    # turn's context on one check.
    blocks = []
    for label, code, out, err in (("now", exit_now, out_now, err_now),
                                  ("HEAD", exit_original, out_old, err_old)):
        body = (str(out or "") + str(err or "")).strip()
        if len(body) > 2000:
            body = "...[+%d chars]\n" % (len(body) - 2000) + body[-2000:]
        blocks.append("--- %s (exit %s)%s" % (label, code, ("\n" + body) if body else ""))
    return "\n".join(blocks)


def revert_check(paths, cmd, timeout=120):
    # "Does this test fail without the fix?" Swap `paths` back to their HEAD content, run `cmd`,
    # and ALWAYS put the working copy back. A test that also passes on the original code proves
    # nothing about the change, so it comes back broken_test: true.
    if isinstance(paths, str):
        paths = _coerce_paths(paths)
        if isinstance(paths, str):
            paths = [paths]
    if not isinstance(paths, (list, tuple)) or not paths:
        return _fail("revert_check", "bad_arguments", "paths must be a non-empty list of paths",
                     "revert_check(paths=['calc.py'], cmd=['python3', '-m', 'pytest', '-q'])")
    shell = isinstance(cmd, str)
    if shell:
        argv, command = None, cmd.strip()
    elif (isinstance(cmd, (list, tuple)) and cmd
            and not any(isinstance(part, (list, tuple, dict, set)) for part in cmd)):
        argv, command = [str(part) for part in cmd], " ".join(str(part) for part in cmd)
    else:
        return _fail("revert_check", "bad_arguments", "cmd must be an argv list or a shell string",
                     "revert_check(paths=['calc.py'], cmd=['python3', '-m', 'pytest', '-q'])")
    if not command:
        return _fail("revert_check", "bad_arguments", "cmd is empty",
                     "revert_check(paths=['calc.py'], cmd=['python3', '-m', 'pytest', '-q'])")
    try:
        timeout = float(timeout)
    except (TypeError, ValueError):
        return _fail("revert_check", "bad_arguments", "timeout must be a number of seconds",
                     "revert_check(paths=[...], cmd=[...], timeout=120)")
    extend = globals().get("_CELL_EXTEND")
    if callable(extend):
        # Two runs of `timeout` plus two swaps. Without this the kernel kills the cell mid-test
        # and the restore, which lives in this process, never runs.
        extend(2.0 * timeout + 30)
    # --- the one path set, resolved and constrained -------------------------------------
    targets = []
    for item in paths:
        target, rel = _resolve(str(item))
        if target is None:
            return _outside("revert_check", str(item), "revert_check")
        if rel not in [one[1] for one in targets]:
            targets.append((target, rel))
    mine = [rel for _, rel in targets]
    for target, rel in targets:
        if not _os.path.isfile(target):
            return _fail("revert_check", "not_found",
                         "%s is not a file in the working tree" % rel,
                         "revert_check swaps a file's content; it cannot create one", path=rel)
        proc = _revert_git(["ls-files", "--error-unmatch", "--", rel])
        if proc is None:
            return _fail("revert_check", "no_git", "git is not available here",
                         "run revert_check in a git checkout", path=rel)
        if proc.returncode != 0:
            return _fail("revert_check", "untracked",
                         "%s is untracked, so HEAD has no version to swap in" % rel,
                         "commit it first (git add, then git commit); revert_check restores "
                         "from HEAD", path=rel)
    dirty = _revert_dirty()
    if dirty is None:
        return _fail("revert_check", "no_git", "git status failed in %s" % _ROOT,
                     "run revert_check in a git checkout")
    others = [item for item in dirty if item not in mine]
    if others:
        return _fail("revert_check", "dirty_tree",
                     "the working tree is dirty outside paths: %s" % ", ".join(others[:6]),
                     "commit or discard those paths first; revert_check touches only `paths` "
                     "and never stashes", dirty=others[:20])
    # --- the original content, and what the tree holds now -------------------------------
    originals, current, changed = {}, {}, []
    for target, rel in targets:
        proc = _revert_git(["show", "HEAD:%s" % rel])
        if proc is None or proc.returncode != 0:
            return _fail("revert_check", "not_in_head", "HEAD has no version of %s" % rel,
                         "commit the file once before swapping it", path=rel)
        originals[rel] = proc.stdout
        try:
            current[rel] = _revert_bytes(target)
        except OSError as exc:
            return _fail("revert_check", "unreadable", str(exc),
                         "read(path) names the failure", path=rel)
        if originals[rel] != current[rel]:
            changed.append(rel)
    exit_now, out_now, err_now, problem = _revert_run(command, argv, shell, timeout)
    if problem == "timeout":
        return _fail("revert_check", "timeout",
                     "the command did not finish in %ss" % timeout,
                     "raise timeout= or narrow the command; nothing was swapped",
                     command=command)
    if problem:
        return _fail("revert_check", "spawn_failed",
                     "the command did not start: %s" % str(err_now).strip()[:200],
                     "exec(argv=[...]) answers the same question without a swap; check the "
                     "program name", command=command)
    # --- swap, run the original, and put the working copy back no matter what ------------
    exit_original, out_old, err_old, problem_old = None, "", "", None
    failures, swapped = [], True
    try:
        for target, rel in targets:
            with _open(target, "wb") as fh:
                fh.write(originals[rel])
        exit_original, out_old, err_old, problem_old = _revert_run(command, argv, shell, timeout)
    except OSError as exc:
        swapped = False
        failures.append("swap: %s" % exc)
    finally:
        for target, rel in targets:
            try:
                with _open(target, "wb") as fh:
                    fh.write(current[rel])
            except OSError as exc:
                failures.append("%s: %s" % (rel, exc))
    digests, expected = {}, {}
    for target, rel in targets:
        expected[rel] = _revert_digest(current[rel])
        try:
            digests[rel] = _revert_digest(_revert_bytes(target))
        except OSError:
            digests[rel] = None
    restored = all(digests[rel] == expected[rel] for rel in expected)
    broken = exit_original == 0
    stdout = _revert_output(exit_now, out_now, err_now, exit_original, out_old, err_old)
    text = ("revert_check: broken_test=%s exit_now=%s exit_with_original=%s restored=%s"
            % (broken, exit_now, exit_original, restored))
    if not swapped:
        # The HEAD content never landed, so `exit_with_original` measures nothing: that is a
        # failed measurement, not a test that passes. The digest still proves the copy is intact.
        return _fail("revert_check", "swap_failed",
                     "could not put the HEAD content in place: %s" % "; ".join(failures),
                     "make `paths` writable first; nothing was measured and the working copy "
                     "is unchanged",
                     broken_test=False, exit_with_original=None, exit_now=exit_now,
                     restored=restored, digest=digests, expected_digest=expected, paths=mine,
                     command=command, stdout=stdout, text=text)
    if not restored:
        lost = [rel for rel in expected if digests[rel] != expected[rel]]
        return _fail("revert_check", "restore_failed",
                     "RESTORE FAILED for %s: it still holds the HEAD content, not your working "
                     "copy%s" % (", ".join(lost) or ", ".join(mine),
                                 (" (%s)" % "; ".join(failures)) if failures else ""),
                     "a human must see a failed restore: report it and stop. undo() restores the "
                     "session-start copy of the path; check diff() before you continue",
                     broken_test=broken, exit_with_original=exit_original, exit_now=exit_now,
                     restored=False, digest=digests, expected_digest=expected, paths=mine,
                     command=command, stdout=stdout, text=text)
    note = []
    if not changed:
        note.append("every path already matches HEAD, so the swap changed nothing")
    if broken:
        note.append("the test passes on the original code too, so it does not verify the fix")
    if exit_now not in (0, None) and not broken:
        note.append("the test does not pass on the working tree either (exit_now=%s)" % exit_now)
    if problem_old == "timeout":
        note.append("the command hit the %ss timeout against the original" % timeout)
    elif problem_old:
        note.append("the command did not start against the original: %s"
                    % str(err_old).strip()[:200])
    result = {"ok": True, "kind": "revert_check", "broken_test": broken,
              "exit_with_original": exit_original, "exit_now": exit_now,
              "restored": True, "digest": digests, "expected_digest": expected,
              "paths": mine, "changed": changed, "command": command,
              "timeout_s": timeout, "stdout": stdout, "text": text}
    if not failures and note:
        result["note"] = "; ".join(note)
    return result


_snapshot()
"""
