"""prelude_edit: edit() and its matching helpers — PRELUDE fragment 5/5c.

WHAT IT IS
  Raw child source `_EDIT`: `_closest_lines`, `_replace_text`, `_edit_context` and `edit()`.
  edit() reports ambiguity instead of guessing and refuses a missing old block. prelude_verbs.py
  concatenates it before the line-addressed edit verbs.

ENTRY POINTS
  _EDIT   source text; inside: _closest_lines, _replace_text, _edit_context, edit

INVARIANTS
  fragment, not an API: `_commit` is the only write; closest lines and hints keep their shape.

WHERE TO CHANGE
  matching and ambiguity ... _closest_lines, _replace_text, _edit_context
"""
_EDIT = r"""def _closest_lines(body, old):
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


"""
