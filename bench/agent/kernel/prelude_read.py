"""prelude_read: delivery — read(), grep(), search(), recall(), skill().

WHAT IT IS
  Fragment 4/5 of `PRELUDE` (see prelude.py). The largest surface an agent uses:
  `read()` (ranges, batching, section=, the repeated-read pointer), `grep()`/`search()` (regex
  by default, merged context, locators above a char budget, count mode, section aggregation),
  and `recall()` (the session-side search over `_OBS`), plus `skill()` (list or load one
  project procedure from `.agent/skills/`). Plus the shared result builders
  `_count_result`, `_grep_result`, `_section_bounds`, `_range_count`,
  `_section_search_result`, `_coerce_paths`.

ENTRY POINTS
  read(path=None, start=None, end=None, raw=False, repeat=False, ranges=None, section=None)
  grep(pattern, path=".", max_results=None, regex=True, include=None, context=0, ...)
  search(...)          the typed verb (same engine as grep)
  recall(pattern, limit=10, context=100) / recall_tool(...)   the typed session search
  skill(name=None, root=".")   list .agent/skills/, or load one procedure
  Inside: _int_arg, _READS_CELL/_READS_SESSION updates, _count_result, _grep_result

INVARIANTS
  - `read()` is a ledger, not a firehose: no range returns the whole file only while it fits
    the line/char budget, else `next`/`has_more`. A contained range already delivered this
    session (same digest) returns `already_read` plus the `_OBS[n]` pointer; `repeat=True`
    re-delivers. `_commit` drops that file's entry.
  - `start`/`end` are the only window arguments (the signature matches the provider schema):
    a non-integer, `start < 1`, or `end < start` is `bad_arguments` at the boundary, never a
    traceback from int(). `end` omitted is the default page (truncated when it cuts).
  - A missing path is `ok:false`/`not_found`, never `ok:true`/`miss`: success means success.
  - Every delivered range is recorded in `_READS_CELL`, which is what `Cell.reads` and the
    card's coverage line report.
  - Search text is exact (no clipping); context is merged so a line appears once; above
    `max_chars` a broad result degrades to locators instead of a silent digest. The two search
    counts are labelled: `hits` (matching lines) and `hit_ranges` (contiguous ranges).
    `max_results=0` is every hit in one answer (mirroring `map(max_files=0)`), so a multi-path
    alternation grep is one call; the default page stays 50 and `offset`/`next` still page.
  - `recall()` reads `_OBS`, which survives a restart; it must not depend on live variables.

WHERE TO CHANGE
  The read budget or pointer ....... _READ_MAX_LINES, _READ_MAX_CHARS, read
  The search result shape .......... _grep_result, _count_result, _section_search_result
  The section aggregation .......... _section_bounds, _section_search_result
  Batching ......................... _coerce_paths, read
  Skill discovery/loading .......... skill
"""
from __future__ import annotations

# --- part 4/5: read(), grep()/search(), recall() -----------------------------------------
_READ = r"""def _section_bounds(path, section):
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
    return path


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


def read(path=None, start=None, end=None, raw=False, repeat=False, ranges=None, section=None):
    path = _coerce_paths(path)
    if section:
        if not isinstance(path, str):
            return _fail("bad_args", "bad_arguments", "section= applies to one file path",
                         "read(path, section='Heading')")
        target, rel = _resolve(path)
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
        return _Result({"ok": all(bool(item.get("ok")) for item in results),
                        "kind": "files", "files": results, "count": len(results),
                        "shown": len(results), "total": len(results), "offset": 0,
                        "unit": "range",
                        "has_more": any(bool(item.get("has_more")) for item in results),
                        "truncated": any(bool(item.get("truncated")) for item in results),
                        "next": None, "text": "\n\n".join(chunks)})
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
        return _Result({"ok": all(bool(item.get("ok")) for item in results), "kind": "files",
                        "files": results, "count": len(results),
                        "shown": len(results), "total": len(results), "offset": 0,
                        "unit": "file",
                        "has_more": any(bool(item.get("has_more")) for item in results),
                        "truncated": any(bool(item.get("truncated")) for item in results),
                        "next": None, "text": "\n\n".join(chunks)})
    target, rel = _resolve(path)
    if target is None:
        return _fail("wrong_call", "outside_root", "%s is outside the workspace" % rel,
                     _OUTSIDE_USE, path=rel)
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
        result.update({"already_read": True, "delivered": False,
                       "handle": handle, "text": "# " + note})
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


def _count_result(counts, occurrences=0, note=None):
    total = sum(int(value) for value in counts.values())
    text = "\n".join("%s:%d" % (path, count) for path, count in sorted(counts.items()))
    if note:
        text = (text + "\n" if text else "") + "note: " + note
    # `shown`/`total` count the rows this response delivers (one per file with matches);
    # `hits` is the matching-line count. Reporting 234 lines as `shown` while delivering 25
    # rows made the page look half-served.
    return _Result({"ok": True, "kind": "search_counts", "counts": counts,
                    "by_file": {path: {"count": count, "lines": []}
                                for path, count in counts.items()},
                    "occurrences": occurrences, "hits": total,
                    "shown": len(counts), "total": len(counts), "unit": "file",
                    "offset": 0, "next": None, "truncated": False, "complete": True,
                    "text": text, "note": note})


def _grep_result(hits, occurrences, truncated, max_chars=12000, offset=0, total=None,
                 all_numbers=None):
    # Render each file once. Adjacent/overlapping hits share their context lines, so the
    # model sees every line once; a line that is both a hit and another hit's context keeps
    # the `:` marker. The structured `matches` stays complete for python callers.
    grouped = {}
    order = []
    for hit in hits:
        path = hit.get("path")
        if path not in grouped:
            grouped[path] = {}
            order.append(path)
        rows = grouped[path]
        for item in hit.get("before") or []:
            rows.setdefault(int(item["line"]), [item["text"], False])
        rows[int(hit["line"])] = [hit["text"], True]
        for item in hit.get("after") or []:
            rows.setdefault(int(item["line"]), [item["text"], False])
    lines = []
    for path in order:
        for number in sorted(grouped[path]):
            text, is_hit = grouped[path][number]
            lines.append("%s:%s%s %s" % (path, number, ":" if is_hit else "-", text))
    full = "\n".join(lines)
    total = len(hits) if total is None else int(total)
    by_file = {}
    for hit in hits:
        path = hit.get("path")
        entry = by_file.setdefault(path, {"count": 0, "lines": []})
        entry["count"] += 1
        entry["lines"].append(int(hit["line"]))
    all_numbers = sorted({int(hit["line"]) for hit in hits}) if all_numbers is None else all_numbers
    range_count = _range_count(all_numbers)
    # Two counts, two names: `hits` is matching lines, `hit_ranges` is how many contiguous
    # ranges they fall in. One unlabeled `ranges` next to `total` made a model guess.
    common = {"ok": True, "kind": "matches", "matches": hits,
              "truncated": truncated, "shown": len(hits), "total": total, "hits": total,
              "hit_ranges": range_count, "unit": "hit",
              "offset": int(offset), "by_file": by_file,
              "next": {"offset": int(offset) + len(hits), "unit": "hit"} if int(offset) + len(hits) < total else None}
    if int(offset) >= total and not hits:
        common["note"] = "end of results (offset %d >= total %d)" % (int(offset), total)
    if max_chars and len(full) > int(max_chars):
        # A broad context search can exceed the card budget and lose its middle. Deliver
        # locators instead of a silently digested blob. Hit lines stay exact; only context
        # is absent here (the caller can re-run with context=0 or max_chars=0 for it).
        locators = "\n".join(
            "%s:%s: %s" % (hit["path"], hit["line"], hit.get("text") or "")
            for hit in hits)
        elided = any(hit.get("before") or hit.get("after") for hit in hits)
        common.update({"text_elided": elided, "text_chars": len(full)})
        if elided and int(max_chars) != 12000:
            common["note"] = ("broad result: max_chars is a locator threshold, not a cap; "
                              "max_chars=0 disables locator mode")
        common["text"] = locators
        return _Result(common)
    common["text"] = full
    return _Result(common)


def grep(pattern, path=".", max_results=None, regex=True, include=None, context=0,
         ignore_case=False, max_chars=12000, offset=0, count=False, within=None,
         section=None, sections=None):
    # Search: one path or a list, an optional include glob, and optional context. The typed
    # `search` and the python `grep` share this one implementation and the same regex default.
    # `max_results` is the page size; passing it makes the page an explicit window (complete),
    # omitting it means the default page and a cut is `truncated`. `max_results=0` means every
    # hit in one answer, mirroring `map(max_files=0)`: a whole multi-file grep is one call.
    explicit_limit = max_results is not None
    page = 50 if max_results is None else int(max_results)
    if page <= 0:
        page = None
    path = _coerce_paths(path)
    if isinstance(path, (list, tuple, set)):
        if count:
            counts = {}
            occurred = 0
            for item in (sorted(path) if isinstance(path, set) else path):
                result = grep(pattern, item, max_results=max_results, regex=regex,
                              include=include, context=context, ignore_case=ignore_case,
                              max_chars=max_chars, offset=0, count=True, within=within,
                              section=section, sections=False)
                counts.update(result.get("counts") or {})
                occurred += int(result.get("occurrences") or 0)
            return _count_result(counts, occurred)
        hits, occurred, truncated = [], 0, False
        for item in (sorted(path) if isinstance(path, set) else path):
            result = grep(pattern, item, max_results=max_results, regex=regex,
                          include=include, context=context, ignore_case=ignore_case,
                          max_chars=max_chars, offset=0, within=within, section=section,
                          sections=False)
            hits.extend(result.get("matches") or [])
            occurred += int(result.get("occurrences") or 0)
            truncated = truncated or bool(result.get("truncated"))
            if page is not None and len(hits) >= page:
                return _grep_result(hits[:page], occurred, not explicit_limit, max_chars,
                                    offset=offset, total=int(result.get("total") or len(hits)))
        return _grep_result(hits, occurred, truncated, max_chars, offset=offset,
                            total=len(hits))
    target, rel = _resolve(path)
    if target is None:
        return _fail("wrong_call", "outside_root", "%s is outside the workspace" % rel,
                     _OUTSIDE_USE, path=rel)
    if not _os.path.exists(target):
        return _fail("not_found", "not_found", "no path at %s" % rel,
                     "map(path) lists files; search(pattern, path='.') walks the tree",
                     path=rel)
    matcher = None
    flags = _re.IGNORECASE if ignore_case else 0
    if regex:
        try:
            matcher = _re.compile(pattern, flags)
        except _re.error as exc:
            return {"ok": False, "kind": "bad_args", "pattern": pattern,
                    "error": {"code": "bad_regex", "message": str(exc),
                              "use": "search(pattern, regex=False) for a literal substring"}}
    needle = pattern.lower() if ignore_case else pattern
    includes = _include_patterns(include)
    if section and isinstance(path, str):
        target, rel = _resolve(path)
        if target is None or not _os.path.isfile(target):
            return {"ok": False, "kind": "bad_args", "error": {
                "code": "section_needs_file",
                "message": "section= applies to one existing file",
                "use": "search(pattern, path='file', section='Heading')"}}
        bounds = _section_bounds(target, section)
        if bounds is None:
            return {"ok": False, "kind": "bad_args", "error": {
                "code": "section_not_found",
                "message": "no outline entry matches %r" % section,
                "use": "outline(%r) to see section names" % path}}
        within = list(bounds)
    window = None
    if within is not None:
        try:
            if isinstance(within, dict):
                window = (int(within.get("start") or 1), int(within.get("end") or 0))
            elif isinstance(within, (list, tuple)) and len(within) == 2:
                window = (int(within[0]), int(within[1]))
        except (TypeError, ValueError):
            window = None
        if window is not None and window[1] < window[0]:
            window = None
    explicit_file = _os.path.isfile(target)
    include_ignored = bool(includes and explicit_file)
    files = [target] if explicit_file else list(_walk(target))
    hits = []
    counts = {}
    all_numbers = []
    occurrences = 0
    matched_lines = 0
    truncated = False
    skip = max(0, int(offset or 0))
    for full in files:
        full_rel = _os.path.relpath(full, _ROOT)
        if includes and not explicit_file:
            if not any(_include_match(full_rel, pat) for pat in includes):
                continue
        try:
            body = _read_text(full)
        except OSError:
            continue
        lines = body.splitlines()
        for number, line in enumerate(lines, 1):
            hay = line.lower() if ignore_case else line
            if not (matcher.search(line) if matcher else needle in hay):
                continue
            if window is not None and not (window[0] <= number <= window[1]):
                continue
            if matcher:
                occurrences += len(matcher.findall(line))
            else:
                occurrences += (line.lower() if ignore_case else line).count(needle)
            hit = {"path": full_rel, "line": number, "text": line.strip()}
            if context:
                width = int(context)
                hit["before"] = [{"line": n, "text": lines[n - 1].strip()}
                                 for n in range(max(1, number - width), number)]
                hit["after"] = [{"line": n, "text": lines[n - 1].strip()}
                                for n in range(number + 1, min(len(lines), number + width) + 1)]
            counts[full_rel] = counts.get(full_rel, 0) + 1
            matched_lines += 1
            all_numbers.append(number)
            if count:
                continue
            if matched_lines <= skip:
                continue
            if page is None or len(hits) < page:
                hits.append(hit)
            else:
                truncated = not explicit_limit
    if count:
        note = None if counts else "no matches for %r in %s" % (pattern, rel)
        return _count_result(counts, occurrences, note)
    if explicit_file and skip == 0 and (sections is True or
                                       (sections is None and matched_lines > 20)):
        aggregated = _section_search_result(
            rel, all_numbers, matched_lines, occurrences, offset=skip,
            extra_note=("include is only used for directory paths; the explicit file was searched"
                        if include_ignored else ""))
        if aggregated is not None:
            return aggregated
    result = _grep_result(hits, occurrences, truncated, max_chars,
                          offset=skip, total=matched_lines, all_numbers=all_numbers)
    notes = []
    if matched_lines == 0:
        scope = ""
        if section:
            scope = " in section %r" % section
        elif window is not None:
            scope = " within [%d,%d]" % window
        notes.append("no matches for %r in %s%s" % (pattern, rel, scope))
    if include_ignored:
        notes.append("include is only used for directory paths; the explicit file was searched")
    if includes and not explicit_file:
        unmatched = [pat for pat in includes
                     if not any(_include_match(_os.path.relpath(full, _ROOT), pat) for full in files)]
        if unmatched:
            result["include_unmatched"] = unmatched
            notes.append("include matched no file: " + ", ".join(unmatched))
    if notes:
        result["note"] = "; ".join(notes)
        result["text"] = str(result.get("text") or "") + "\nnote: " + result["note"]
    return result


def search(pattern, path=".", max_results=None, regex=True, include=None, context=0,
           ignore_case=False, max_chars=12000, offset=0, count=False, within=None,
           section=None, sections=None):
    # The typed `search` tool is the public name; grep() stays the python helper. Search is
    # regex by default, matching the grep mental model: an alternation like `a|b` is one
    # call, not a literal miss. Pass regex=False for a literal substring.
    return grep(pattern, path=path, max_results=max_results, regex=regex,
                include=include, context=context, ignore_case=ignore_case,
                max_chars=max_chars, offset=offset, count=count, within=within,
                section=section, sections=sections)


def recall(pattern, limit=10, context=100):
    # Search the full output of earlier cells, including turns a fold removed. Every cell
    # keeps its raw stdout/stderr/result in _OBS; this returns matching snippets plus the
    # handle to page the full text. grep is for the tree, recall is for the session.
    try:
        rx = _re.compile(pattern)
    except _re.error:
        rx = _re.compile(_re.escape(pattern))
    out = []
    for i, entry in enumerate(globals().get("_OBS") or []):
        for field in ("stdout", "stderr", "result"):
            text = entry.get(field) or ""
            for match in rx.finditer(text):
                start = max(0, match.start() - int(context))
                end = min(len(text), match.end() + int(context))
                while start > 0 and text[start - 1].isalnum():
                    start -= 1
                while end < len(text) and text[end].isalnum():
                    end += 1
                snippet = text[start:end]
                out.append({"obs": i, "field": field, "source": "session",
                            "snippet": snippet, "text": snippet,
                            "handle": "_OBS[%d]['%s']" % (i, field)})
                if len(out) >= int(limit):
                    return out
    return out


def recall_tool(pattern, limit=10, context=100):
    # Typed session search: the same _OBS snippets as recall(), with a text rendering so the
    # model does not need a python cell to recover a digested card.
    items = recall(pattern, limit=limit, context=context)
    scanned = len(globals().get("_OBS") or [])
    text = "\n".join("[%s] %s: %s" % (item["handle"], item["field"], item["text"])
                     for item in items)
    note = None if items else ("no matches for %r in %d earlier cell(s)" % (pattern, scanned))
    if note:
        text = "note: " + note
    return {"ok": True, "kind": "recall", "items": items,
            "shown": len(items), "total": len(items), "next": None,
            "scanned": scanned, "count": len(items), "note": note, "text": text}


def skill(name=None, root="."):
    # On-demand procedures: .agent/skills/<name>.md, discovered at call time so a run can
    # add one without editing the tool schema. Listing is cheap; loading is one file.
    base = _os.path.join(root, ".agent", "skills")
    try:
        names = sorted(n[:-3] for n in _os.listdir(base) if n.endswith(".md"))
    except OSError:
        names = []
    if name is None:
        text = "\n".join("- " + n for n in names) if names else "no skills in .agent/skills/"
        return {"ok": True, "kind": "skills", "items": names, "shown": len(names),
                "total": len(names), "note": None if names else "no skills", "text": text}
    path = _os.path.join(base, str(name) + ".md")
    try:
        with _open(path, encoding="utf-8", errors="replace") as fh:
            body = fh.read()
    except OSError:
        return {"ok": False, "kind": "skills", "error": {"code": "unknown_skill",
                "message": "no skill %r" % (name,), "available": names,
                "use": "skill() lists; skill(name) loads one"}}
    return {"ok": True, "kind": "skill", "name": str(name), "text": body,
            "chars": len(body), "path": path}


"""
