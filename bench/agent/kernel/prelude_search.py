"""prelude_search: grep() and search(), the text search verbs — PRELUDE fragment 4/5d.

WHAT IT IS
  Raw child source `_SEARCH`: `grep()` (regex/literal search with include filters, context and
  paging) and `search()`, its alias with the same defaults. prelude_read.py concatenates it
  after the result envelopes it returns.

ENTRY POINTS
  _SEARCH   source text; inside: grep, search

INVARIANTS
  fragment, not an API: it walks through `_walk`, filters through the setup helpers, and returns
  the `_grep_result` envelope unchanged. Counts, ordering and paging are the previous bytes.

WHERE TO CHANGE
  search grammar, include filters, counts ... grep / search
"""
_SEARCH = r"""def grep(pattern, path=".", max_results=None, regex=True, include=None, context=0,
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
        return _outside("wrong_call", rel, "search", path=rel)
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
    # An explicit max_results is a real page request: aggregation would silently ignore it.
    # The default (no max_results) still aggregates a broad single-file result above 20 hits.
    if (explicit_file and skip == 0 and not explicit_limit
            and (sections is True or (sections is None and matched_lines > 20))):
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


"""
