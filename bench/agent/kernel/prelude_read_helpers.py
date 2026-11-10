"""prelude_read_helpers: range/coercion helpers shared by the read verbs — PRELUDE fragment 4/5a.

WHAT IT IS
  Raw child source `_READ_HELPERS`: section bounds, range counts, the section-result shape, plus
  `_coerce_paths` and `_int_arg`, the argument coercion every read/search verb uses.
  prelude_read.py concatenates it before the read, search and recall fragments.

ENTRY POINTS
  _READ_HELPERS   source text; inside: _section_bounds, _range_count,
                  _section_search_result, _coerce_paths, _int_arg

INVARIANTS
  fragment, not an API: names resolve against earlier PRELUDE fragments (_os, _re, _fail).
  order and bytes are the contract; a helper used by a later fragment stays defined here.

WHERE TO CHANGE
  section ranges and their result shape ... _section_bounds, _range_count, _section_search_result
  argument coercion ....................... _coerce_paths, _int_arg
"""
_READ_HELPERS = r"""def _section_bounds(path, section):
    # Resolve a section name through outline() to (first_line, last_line).
    try:
        result = outline(path, max_entries=2000, keys=False)
    except Exception:
        return None
    want = str(section).strip().lower()
    entries = result.get("entries") or []
    exact = [item for item in entries if str(item.get("name") or "").strip().lower() == want]
    partial = [item for item in entries if want in str(item.get("name") or "").lower()]
    for item in exact or partial:
        return int(item["line"]), int(item.get("end") or item["line"])
    return None


def _range_count(numbers):
    total = 0
    previous = None
    for number in sorted(set(int(item) for item in numbers)):
        if previous is None or number > previous + 1:
            total += 1
        previous = number
    return total


def _section_search_result(path, numbers, hits_total, occurrences, offset=0, extra_note=""):
    # A broad search is a discovery request, not a request for 88 lines. Assign each hit to
    # the innermost outline section that owns it and return one row per section; the model
    # can then search(section=...) or read() the winning range. Explicit sections=false keeps
    # the raw hit table for callers that really want it.
    try:
        result = outline(path, max_entries=5000, keys=False)
    except Exception:
        return None
    entries = [item for item in (result.get("entries") or [])
               if int(item.get("line") or 0) > 0]
    if not entries:
        return None
    counts = [0] * len(entries)
    preamble = 0
    for number in sorted(set(int(item) for item in numbers)):
        owner = None
        for index, item in enumerate(entries):
            start = int(item.get("line") or 0)
            end = int(item.get("end") or start)
            if start <= number <= end:
                owner = index
        if owner is None:
            preamble += 1
        else:
            counts[owner] += 1
    rows = []
    if preamble:
        rows.append({"line": 1, "end": int(entries[0].get("line") or 1) - 1,
                     "kind": "preamble", "name": "(before first section)",
                     "count": preamble})
    for item, count in zip(entries, counts):
        if count:
            rows.append({"line": int(item.get("line") or 0),
                         "end": int(item.get("end") or item.get("line") or 0),
                         "kind": item.get("kind"), "name": item.get("name"),
                         "count": count})
    if not rows:
        return None
    text = "\n".join("%d-%d: %s %s — %d hit(s)" % (
        row["line"], row["end"], row["kind"], row["name"], row["count"]) for row in rows)
    return _Result({
        "ok": True, "kind": "search_sections", "path": path,
        "sections": rows, "hits": hits_total, "occurrences": occurrences,
        "hit_ranges": _range_count(numbers), "shown": len(rows), "total": len(rows),
        "unit": "section",
        "offset": int(offset), "next": None, "truncated": False, "complete": True,
        "by_file": {path: {"count": hits_total, "lines": sorted(set(int(item) for item in numbers))}},
        "note": ("aggregated %d hit(s) by section; pass sections=false for matching lines, "
                 "or section='Heading' to search one section" % hits_total)
                + (("; " + extra_note) if extra_note else ""),
        "text": text,
    })


def _coerce_paths(path):
    # A model sometimes serializes a path list as '["a", "b"]' instead of passing an array.
    # Accept that shape rather than silently searching for a file with the brackets in its
    # name; a literal path that looks like a list is vanishingly unlikely.
    if isinstance(path, str) and path.strip().startswith("[") and path.strip().endswith("]"):
        try:
            value = _ast.literal_eval(path)
        except (ValueError, SyntaxError):
            return path
        if isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
            return list(value)
    if isinstance(path, str) and (" " in path.strip() or "," in path):
        raw = path.strip()
        target, _rel = _resolve(raw)
        if target is None or not _os.path.exists(target):
            parts = [part for part in raw.replace(",", " ").split() if part]
            if len(parts) > 1 and all(_os.path.exists(_resolve(part)[0] or "") for part in parts):
                return parts
    return path


def _scratch_read(path):
    # A read outside the workspace is allowed only under scratch roots. The typed writers stay
    # workspace-only; a model that wrote /tmp/x with python can still read it back in one call.
    if not isinstance(path, str) or not _os.path.isabs(path):
        return None
    norm = _os.path.normpath(path)
    roots = {_os.path.normpath(p) for p in (
        "/tmp", "/var/tmp", "/dev/shm", _os.environ.get("TMPDIR") or "/tmp")}
    if any(norm == root or norm.startswith(root + _os.sep) for root in roots):
        return norm
    return None


def _int_arg(value, name, minimum):
    # Validate a numeric argument at the boundary. A model that passes start="five" must get
    # bad_arguments with the signature (the driver adds it), never a traceback from int().
    if value is None:
        return None, None
    try:
        if isinstance(value, bool):
            raise TypeError
        number = int(value)
    except (TypeError, ValueError):
        return None, _fail("bad_args", "bad_arguments",
                           "%s must be an integer, got %r" % (name, value),
                           "read(path, start=1, end=10); help('read') shows the signature",
                           argument=name)
    if number < minimum:
        return None, _fail("bad_args", "bad_arguments",
                           "%s must be >= %d, got %d" % (name, minimum, number),
                           "read(path, start=1, end=10); help('read') shows the signature",
                           argument=name)
    return number, None


"""
