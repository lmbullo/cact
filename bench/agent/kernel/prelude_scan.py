"""prelude_scan: gitignore-aware traversal and map() — PRELUDE fragment 3/5a.

WHAT IT IS
  Raw child source `_SCAN`: `_walk`, the one traversal every listing verb uses, and `map()`
  with its paging and include filters. prelude_index.py concatenates it into `_INDEX`, so the
  child still execs one byte-identical PRELUDE.

ENTRY POINTS
  _SCAN   source text; not imported by the kernel. Inside: _walk, map

INVARIANTS
  fragment, not an API: it resolves names from earlier PRELUDE fragments (_os, _ROOT, _ignored,
  _resolve, _fail, _outside, _partial_failure). Order and bytes are the contract.
  _walk applies SKIP_DIRS plus the root .gitignore; map pages with shown/next/has_more and
  reports offset/truncated as before.

WHERE TO CHANGE
  traversal and ignore rules ... _walk
  map paging and filters ...... map
"""
_SCAN = r"""def _walk(root):
    for dirpath, dirnames, filenames in _os.walk(root):
        rel_dir = _os.path.relpath(dirpath, _ROOT)
        keep = []
        for name in dirnames:
            if name in _SKIP_DIRS or name.startswith("."):
                continue
            rel = name if rel_dir == "." else rel_dir + "/" + name
            if _ignored(rel, True):
                continue
            keep.append(name)
        dirnames[:] = sorted(keep)
        for name in sorted(filenames):
            full = _os.path.join(dirpath, name)
            rel = _os.path.relpath(full, _ROOT)
            if _ignored(rel, False):
                continue
            yield full


def map(path=".", max_files=None, include=None, offset=0):
    path = _coerce_paths(path)
    if isinstance(path, (list, tuple, set)):
        results = [map(item, max_files=max_files, include=include, offset=offset)
                   for item in (sorted(path) if isinstance(path, set) else path)]
        chunks = []
        for item in results:
            chunks.append("# " + str(item.get("root") or "") + "\n" + str(item.get("text") or ""))
        aggregate = {"ok": all(bool(item.get("ok")) for item in results), "kind": "maps",
                     "files": results, "count": len(results), "file_count": len(results),
                     "shown": len(results), "total": len(results), "offset": 0, "unit": "file",
                     "truncated": any(bool(item.get("truncated")) for item in results),
                     "has_more": any(bool(item.get("has_more")) for item in results),
                     "text": "\n\n".join(chunks)}
        if not aggregate["ok"]:
            aggregate.update(_partial_failure(results))
        return aggregate
    explicit = max_files is not None
    size = 120 if max_files is None else int(max_files)
    target, rel = _resolve(path)
    if target is None:
        return _outside("wrong_call", rel, "map", path=rel)
    if not _os.path.exists(target):
        near = []
        try:
            near = _difflib.get_close_matches(
                _os.path.basename(rel) or rel,
                _os.listdir(_os.path.dirname(target) or _ROOT), n=3, cutoff=0.3)
        except OSError:
            pass
        return _fail("not_found", "not_found", "no path at %s" % rel,
                     "map('.') lists the workspace root; guide(query) says which module owns a topic",
                     path=rel, did_you_mean=near)
    includes = _include_patterns(include)

    def wanted(name):
        return not includes or any(_include_match(name, pat) for pat in includes)

    if _os.path.isfile(target):
        try:
            body = _read_text(target)
        except OSError as exc:
            return {"ok": False, "kind": "read_error", "path": rel,
                    "error": {"code": "read_failed", "message": str(exc)}}
        entry = {"path": rel, "lines": len(body.splitlines()), "bytes": len(body)}
        rows = [entry] if wanted(rel) else []
        text = "  lines  path\n" + "\n".join(
            "%6d  %s" % (item["lines"], item["path"]) for item in rows)
        result = {"ok": True, "kind": "map", "root": rel, "files": rows,
                  "count": len(rows), "total_files": len(rows), "offset": 0, "unit": "file",
                  "shown": [1, len(rows)] if rows else [], "truncated": False,
                  "next": None, "has_more": False, "text": text}
        if not rows and includes:
            result["include_unmatched"] = includes
            result["note"] = "the explicit file does not match include: " + ", ".join(includes)
            result["text"] = text + "\nnote: " + result["note"]
        return result

    base = target
    entries = []
    for full in _walk(base):
        rel = _os.path.relpath(full, _ROOT)
        if not wanted(rel):
            continue
        try:
            body = _read_text(full)
        except OSError:
            continue
        entries.append({"path": rel, "lines": len(body.splitlines()), "bytes": len(body)})
    total = len(entries)
    unmatched = [pat for pat in includes
                 if not any(_include_match(entry["path"], pat) for entry in entries)]
    dirs = {}
    for entry in entries:
        parts = entry["path"].split("/")
        for depth in range(1, len(parts)):
            key = "/".join(parts[:depth])
            dirs[key] = dirs.get(key, 0) + 1
    if total == 0:
        try:
            nonempty = next(_os.scandir(base), None) is not None
        except OSError:
            nonempty = False
        if nonempty:
            return _fail("map", "empty_index",
                         "directory has entries but the filter yielded no files",
                         ("include matched no file: " + ", ".join(includes)
                          if includes else "call map(path) without include"),
                         root=_os.path.relpath(base, _ROOT), count=0, total_files=0,
                         dirs_matched={}, include_unmatched=includes)
    start = max(0, int(offset or 0))
    if size <= 0:
        # max_files=0: every row. A tree sweep is one call, not seven pages of 120.
        page = entries[start:]
        truncated = False
    else:
        page = entries[start:start + size]
        truncated = (start + len(page)) < total and not explicit
    end = start + len(page)
    text = "  lines  path\n" + "\n".join(
        "%6d  %s" % (entry["lines"], entry["path"]) for entry in page)
    if unmatched:
        text += "\nnote: include matched no file: " + ", ".join(unmatched)
    result = {"ok": True, "kind": "map", "root": _os.path.relpath(base, _ROOT),
              "files": page, "count": total, "total_files": total, "dirs_matched": dirs,
              "include_unmatched": unmatched, "offset": start, "unit": "file",
              "shown": [start + 1, end] if page else [],
              "next": {"offset": end, "unit": "file"} if end < total else None,
              "truncated": truncated, "has_more": end < total, "text": text}
    if unmatched:
        result["note"] = "include matched no file: " + ", ".join(unmatched)
    return result


"""
