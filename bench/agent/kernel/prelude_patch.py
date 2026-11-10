"""prelude_patch: the unified-diff parser and apply() — PRELUDE fragment 5/5e.

WHAT IT IS
  Raw child source `_PATCH`: `_parse_patch`, `_patch_path`, `_hunk_old`, `_hunk_new` and `apply()`.
  apply writes several files in one cell so one diff()/undo() covers the change set.
  prelude_verbs.py concatenates it after the line-addressed edits.

ENTRY POINTS
  _PATCH   source text; inside: _parse_patch, _patch_path, _hunk_old, _hunk_new, apply

INVARIANTS
  fragment, not an API: a malformed patch is a refusal, never a partial write; every changed file
  goes through `_commit` so the journal stays one row per file.

WHERE TO CHANGE
  patch grammar ... _parse_patch, _hunk_old, _hunk_new
  apply/write ..... apply
"""
_PATCH = r"""def _parse_patch(patch):
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


"""
