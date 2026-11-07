"""prelude_verbs: the remaining typed verbs and the start-up snapshot.

WHAT IT IS
  Fragment 5/5 of `PRELUDE` (see prelude.py): `verbs()` (the help catalog), the mutation verbs
  `write`, `delete`, `mkdir`, `edit`, `edit_many`, `splice`, `apply` (unified diff parsing:
  `_parse_patch`, `_patch_path`, `_hunk_old`, `_hunk_new`) and `commit({path: text})`. It ends
  with the `_snapshot()` call that records the session-start tree.

ENTRY POINTS
  write(path, content, force=False)      replace a file (refuses to drop a tail without force)
  delete(path), mkdir(path)              the journalled removals/creations
  edit(path, old, new, all=False)        one replacement; edit_many(edits=[...]) the atomic batch
  splice(path, start, end, text)         1-based inclusive line replacement
  apply(patch)                           a unified diff, several files, one cell
  commit({path: text}, force=False)      several writes in one cell

INVARIANTS
  - Every one of them commits through `_commit`; `edit` reports ambiguity instead of guessing
    and validates the whole batch before writing; `apply` fails on a context mismatch rather
    than fuzzy-matching.
  - `write()` refuses to truncate a longer file without `force=True`.
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
    return {"ok": True, "kind": "verbs", "verbs": out,
            "text": "\n".join("%s%s  %s" % (item["name"], item["signature"], item["summary"])
                             for item in out)}


help = verbs


def write(path, content, force=False):
    target, rel = _resolve(path)
    if target is None:
        return {"ok": False, "error": {"code": "outside_root", "path": rel}}
    existed = _os.path.isfile(target)
    if existed and not force:
        current = _read_text(target)
        if len(content) < len(current) and current.startswith(content):
            return {"ok": False, "kind": "refused_truncation", "path": rel,
                    "existing_lines": len(current.splitlines()), "new_lines": len(content.splitlines()),
                    "note": "the new content is a prefix of the current file, so this write would delete its tail. "
                            "If that is intended, pass force=True; otherwise edit() the region."}
    entry = _commit(target, rel, content, "write")
    return {"ok": True, "kind": "write", "path": rel, "created": entry["created"],
            "lines": entry["after_lines"], "line_delta": None if entry["created"] else entry["after_lines"] - entry["before_lines"],
            "adds": entry["adds"], "dels": entry["dels"]}


def delete(path):
    # One file, through the tree state diff/undo already tracks. `rm`/`mv` through exec are
    # refused because they would bypass the journal; this is the typed way to remove one.
    target, rel = _resolve(path)
    if target is None:
        return {"ok": False, "kind": "wrong_call", "path": rel,
                "error": {"code": "outside_root", "path": rel}}
    if not _os.path.exists(target):
        return {"ok": True, "kind": "no_change", "path": rel, "reason": "missing",
                "text": "no file at %s" % rel}
    if _os.path.isdir(target):
        return {"ok": False, "kind": "wrong_call", "path": rel,
                "error": {"code": "is_directory",
                          "message": "delete() takes one file, not a directory",
                          "use": "map(path) lists a directory; delete each file you mean"}}
    try:
        before = _read_text(target)
        entry = _commit(target, rel, None, "delete")
    except OSError as exc:
        return {"ok": False, "kind": "delete_failed", "path": rel,
                "error": {"code": "delete_failed", "message": str(exc)}}
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
        return {"ok": False, "kind": "wrong_call", "path": rel,
                "error": {"code": "outside_root", "path": rel}}
    if _os.path.exists(target):
        if _os.path.isdir(target):
            return {"ok": True, "kind": "no_change", "path": rel, "reason": "exists",
                    "text": "directory already exists: %s" % rel}
        return {"ok": False, "kind": "wrong_call", "path": rel,
                "error": {"code": "not_a_directory", "path": rel}}
    try:
        _os.makedirs(target, exist_ok=True)
    except OSError as exc:
        return {"ok": False, "kind": "mkdir_failed", "path": rel,
                "error": {"code": "mkdir_failed", "message": str(exc)}}
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


def edit(path, old, new, all=False, replace_all=None):
    target, rel = _resolve(path)
    if target is None:
        return {"ok": False, "error": {"code": "outside_root", "path": rel}}
    if not _os.path.isfile(target):
        return {"ok": True, "kind": "no_change", "path": rel, "reason": "missing"}
    if replace_all is not None:
        all = bool(replace_all)
    body = _read_text(target)
    updated, info = _replace_text(body, old, new, all)
    if updated is None:
        if info["reason"] == "not_found":
            return {"ok": True, "kind": "no_change", "path": rel,
                    "reason": "not_found", "closest": info.get("closest") or [],
                    "use": "copy a closest line exactly, or splice(path, line, line, new)"}
        return {"ok": True, "kind": info["reason"], "path": rel,
                **{key: value for key, value in info.items() if key != "reason"}}
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
        return {"ok": False, "kind": "edit_failed",
                "reason": "pass edits=[{path, old, new}, ...]"}
    if not isinstance(edits, (list, tuple)) or not edits:
        return {"ok": False, "kind": "edit_failed",
                "reason": "edits must be a non-empty list of {path, old, new}"}
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
        return {"ok": False, "kind": "edit_failed", "index": first["index"],
                "reason": first["reason"], "problems": problems,
                "use": "edits=[{path, old, new}, ...]; every item needs its own path and a non-empty old"}

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
            return {"ok": False, "kind": "edit_failed", "index": index,
                    "error": {"code": "outside_root", "path": rel}}
        if rel not in originals:
            if not _os.path.isfile(target):
                return {"ok": False, "kind": "edit_failed", "index": index,
                        "path": rel, "reason": "missing"}
            originals[rel] = _read_text(target)
            bodies[rel] = originals[rel]
            order.append((target, rel))
        body = bodies[rel]
        updated, info = _replace_text(body, old, new, bool(item.get("all")))
        if updated is None:
            result = {"ok": False, "kind": "edit_failed", "index": index, "path": rel,
                      "reason": info["reason"]}
            result.update({key: value for key, value in info.items() if key != "reason"})
            if info["reason"] == "not_found":
                result["use"] = "copy a closest line exactly, or splice(path, line, line, new)"
            return result
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
    # Replace 1-based inclusive lines start..end with text. end < start inserts before start;
    # text="" deletes the range. read() already returns numbered lines, so the model never
    # re-emits unchanged lines for a surgical change. _commit keeps diff()/undo() honest.
    target, rel = _resolve(path)
    if target is None:
        return {"ok": False, "error": {"code": "outside_root", "path": rel}}
    if not _os.path.isfile(target):
        return {"ok": True, "kind": "no_change", "path": rel, "reason": "missing"}
    body = _read_text(target)
    lines = body.splitlines()
    total = len(lines)
    try:
        a = int(start)
        b = a if end is None else int(end)
    except (TypeError, ValueError):
        return {"ok": False, "kind": "splice", "path": rel,
                "reason": "start and end must be integers (1-based line numbers)"}
    if a < 1:
        return {"ok": False, "kind": "splice", "path": rel,
                "reason": "start is 1-based", "total_lines": total}
    replacement = str(text).splitlines()
    if b < a:
        if a > total + 1:
            return {"ok": False, "kind": "splice", "path": rel,
                    "reason": "insert position is past the end", "total_lines": total,
                    "next": total + 1}
        new_lines = lines[:a - 1] + replacement + lines[a - 1:]
    else:
        if a > total or b > total:
            return {"ok": False, "kind": "splice", "path": rel,
                    "reason": "range is past the end; read(path) shows total_lines",
                    "total_lines": total, "next": a}
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
        return {"ok": False, "kind": "patch_failed", "reason": "no hunks found in the patch text"}
    results = []
    for item in parsed:
        rel = _patch_path(item["dst"] or item["src"])
        if rel is None:
            return {"ok": False, "kind": "patch_failed", "reason": "missing +++ path"}
        target, rel = _resolve(rel)
        if target is None:
            return {"ok": False, "error": {"code": "outside_root", "path": rel}}
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
                return {"ok": False, "kind": "patch_failed", "path": rel,
                        "reason": "context did not match", "hunk": old[:4]}
            lines[position:position + len(old)] = new
        trailing = "\n" if body.endswith("\n") or not exists else ""
        entry = _commit(target, rel, "\n".join(lines) + trailing, "apply")
        results.append({"path": rel, "adds": entry["adds"], "dels": entry["dels"]})
    return {"ok": True, "kind": "apply", "files": results}


def commit(files, force=False):
    if not isinstance(files, dict) or not files:
        return {"ok": False, "kind": "commit", "reason": "pass a dict of {path: text}"}
    done = []
    for path, content in files.items():
        if not isinstance(content, str):
            return {"ok": False, "kind": "commit", "path": str(path), "reason": "content must be text"}
        result = write(path, content, force=force)
        if not result.get("ok"):
            return {"ok": False, "kind": "commit", "path": str(path), "detail": result}
        done.append({"path": result["path"], "adds": result["adds"], "dels": result["dels"]})
    return {"ok": True, "kind": "commit", "files": done}


_snapshot()
"""
