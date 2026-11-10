"""prelude_read_file: read(), the budgeted file read verb — PRELUDE fragment 4/5b.

WHAT IT IS
  Raw child source `_READ_FILE`: `read()` with its range, ranges, section and repeat modes,
  the read ledger check that turns a repeated contained range into a pointer, and the
  has_more/next paging shape. prelude_read.py concatenates it after `_READ_HELPERS`.

ENTRY POINTS
  _READ_FILE   source text; inside: read

INVARIANTS
  fragment, not an API: read() calls the helpers in the preceding fragment and the journal's
  `_read_text`/`_resolve`. Budget, paging and not_found/wrong_call shapes are unchanged.

WHERE TO CHANGE
  read delivery budget, paging, repeat ledger ... read
"""
_READ_FILE = r"""def read(path=None, start=None, end=None, raw=False, repeat=False, ranges=None, section=None):
    path = _coerce_paths(path)
    if section:
        if not isinstance(path, str):
            return _fail("bad_args", "bad_arguments", "section= applies to one file path",
                         "read(path, section='Heading')")
        target, rel = _resolve(path)
        if target is None:
            target = _scratch_read(path)
            rel = target or rel
        if target is None or not _os.path.isfile(target):
            return _fail("not_found", "not_found", "no file at %s" % rel,
                         "map(path) lists files; outline(path) shows sections", path=rel)
        bounds = _section_bounds(target, section)
        if bounds is None:
            return _fail("bad_args", "bad_arguments",
                         "no outline entry matches %r" % (section,),
                         "outline(%r) lists the section names" % (path,), path=rel)
        start, end = bounds
    # A batched ranged read: ranges=[{path, start, end}, ...] is one call for several
    # windows, unlike path=[...] which applies the same window to each file.
    if ranges is not None:
        if not isinstance(ranges, (list, tuple)) or not ranges:
            return _fail("bad_args", "bad_arguments", "ranges must be a non-empty list",
                         "read(ranges=[{path, start, end}, ...])")
        results = []
        for item in ranges:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                results.append(_fail("bad_args", "bad_arguments",
                                     "each range needs a path",
                                     "read(ranges=[{path, start, end}, ...])"))
                continue
            results.append(read(item["path"], start=item.get("start"),
                                end=item.get("end"), raw=raw, repeat=repeat))
        results = [_Result(item) if isinstance(item, dict) else item for item in results]
        chunks = []
        for item in results:
            rel = str(item.get("path") or "")
            if item.get("kind") == "file":
                page = item.get("next")
                status = ("continues at %s" % (
                    page.get("offset") if isinstance(page, dict) else page)
                    if page is not None else "complete")
                chunks.append("# %s (%s)\n%s" % (rel, status, item.get("text") or ""))
            else:
                chunks.append("# " + rel + "\n" + str(dict(item)))
        aggregate = {"ok": all(bool(item.get("ok")) for item in results),
                     "kind": "files", "files": results, "count": len(results),
                     "shown": len(results), "total": len(results), "offset": 0,
                     "unit": "range",
                     "has_more": any(bool(item.get("has_more")) for item in results),
                     "truncated": any(bool(item.get("truncated")) for item in results),
                     "next": None, "text": "\n\n".join(chunks)}
        if not aggregate["ok"]:
            aggregate.update(_partial_failure(results))
        return _Result(aggregate)
    if path is None:
        return _fail("bad_args", "bad_arguments", "read needs path or ranges",
                     "read(path, start=..., end=...) or read(ranges=[...])")
    first_line, problem = _int_arg(start, "start", 1)
    if problem is not None:
        return problem
    last_line, problem = _int_arg(end, "end", 1)
    if problem is not None:
        return problem
    if first_line is None:
        first_line = 1
    explicit = last_line is not None
    if explicit and last_line < first_line:
        return _fail("bad_args", "bad_arguments",
                     "end (%d) is before start (%d)" % (last_line, first_line),
                     "read(path, start=1, end=10); end is the inclusive last line")
    # A list is one call for several files: the model's natural batch shape. Each sub-read
    # records its own _READS_CELL ranges, so coverage stays exact.
    if isinstance(path, (list, tuple, set)):
        paths = sorted(path) if isinstance(path, set) else list(path)
        results = [read(item, start=first_line, end=last_line, raw=raw, repeat=repeat)
                   for item in paths]
        chunks = []
        for item in results:
            rel = str(item.get("path") or "")
            if item.get("kind") == "file":
                chunks.append("# " + rel + "\n" + (item.get("text") or ""))
            else:
                chunks.append("# " + rel + "\n" + str(dict(item)))
        aggregate = {"ok": all(bool(item.get("ok")) for item in results), "kind": "files",
                     "files": results, "count": len(results),
                     "shown": len(results), "total": len(results), "offset": 0,
                     "unit": "file",
                     "has_more": any(bool(item.get("has_more")) for item in results),
                     "truncated": any(bool(item.get("truncated")) for item in results),
                     "next": None, "text": "\n\n".join(chunks)}
        if not aggregate["ok"]:
            aggregate.update(_partial_failure(results))
        return _Result(aggregate)
    target, rel = _resolve(path)
    if target is None:
        target = _scratch_read(path)
        rel = target or rel
    if target is None:
        return _outside("wrong_call", rel, "read", path=rel)
    if not _os.path.exists(target):
        near = []
        try:
            near = _difflib.get_close_matches(
                _os.path.basename(rel), _os.listdir(_os.path.dirname(target) or _ROOT), n=3, cutoff=0.3)
        except OSError:
            pass
        return _fail("not_found", "not_found", "no file at %s" % rel,
                     "map(path) lists files; search(pattern) finds text",
                     path=rel, did_you_mean=near)
    if _os.path.isdir(target):
        return _fail("wrong_call", "is_directory", "read() takes a file, not a directory",
                     "map(%r) lists the directory; read each file you mean" % rel, path=rel)
    body = _read_text(target)
    lines = body.splitlines()
    total = len(lines)
    if total and first_line > total:
        # Past EOF is an empty delivery, not a silent clamp back into the file.
        return _Result({"ok": True, "kind": "file", "path": rel, "total_lines": total,
                        "shown": [first_line, first_line - 1], "offset": first_line,
                        "next": None, "truncated": False, "has_more": False,
                        "note": "start %d is past EOF (total_lines=%d)" % (first_line, total),
                        "text": ""})
    if explicit:
        stop = min(total, last_line)
        truncated = False
    else:
        stop = total
        budget = 0
        for n in range(first_line - 1, total):
            budget += len(lines[n]) + 1
            if n - (first_line - 1) + 1 > _READ_MAX_LINES or budget > _READ_MAX_CHARS:
                stop = max(first_line, n)
                break
        # `truncated` means this call cut output the caller asked to see: the default page.
        truncated = stop < total
    start = first_line
    has_more = stop < total
    result = {"ok": True, "kind": "file", "path": rel, "total_lines": total,
              "shown": [start, stop], "offset": start,
              "next": {"offset": stop + 1, "unit": "line"} if has_more else None,
              "truncated": truncated, "has_more": has_more}
    digest = _digest(body)
    prior = _READS_SESSION.get(rel)
    hit = None
    if (not raw and not repeat and prior and prior.get("digest") == digest):
        hit = next((item for item in prior.get("ranges") or []
                    if item["start"] <= start and stop <= item["stop"]), None)
    if hit is not None:
        handle = "_OBS[%d]['stdout']" % int(hit["obs"])
        note = ("already delivered in this session; use read(%r, start=%d, end=%d, repeat=True) "
                "to receive it again" % (rel, start, stop))
        # A pointer delivers no text: the original window's truncation must not be reported
        # on this response. `has_more` still says the file continues past the range.
        result.update({"already_read": True, "delivered": False,
                       "handle": handle, "text": "# " + note,
                       "truncated": False})
        return _Result(result)
    entry = _READS_CELL.setdefault(rel, {"ranges": [], "total": total})
    entry["ranges"].append([start, stop])
    entry["total"] = total
    if prior is None or prior.get("digest") != digest:
        prior = {"ranges": [], "total": total, "digest": digest}
    prior["ranges"].append({"start": start, "stop": stop,
                            "obs": len(_OBS) if "_OBS" in globals() else 0})
    prior["total"] = total
    _READS_SESSION[rel] = prior
    chunk = "\n".join(lines[start - 1:stop])
    if raw:
        result["text"] = chunk
        result["raw"] = chunk
    else:
        result["text"] = "\n".join(f"{n:>6}| {lines[n - 1]}" for n in range(start, stop + 1))
    return _Result(result)


"""
