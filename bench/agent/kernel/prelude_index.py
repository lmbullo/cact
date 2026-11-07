"""prelude_index: listing files and structure — map() and outline().

WHAT IT IS
  Fragment 3/5 of `PRELUDE` (see prelude.py). `_walk` (gitignore-aware traversal), `map()`
  (files with line counts, paging, include filters), and `outline()` with its parsers: Markdown
  heading subtrees, Python defs/classes/methods, TS/JS definitions, and HTML sections/headings
  including ones inside JS template literals.

ENTRY POINTS
  map(path=".", max_files=None, include=None, offset=0)      files, paged (max_files=0: all rows)
  outline(path, max_entries=None, keys=False)                 structure with subtree ranges
  Inside: _walk, _outline_doc, _outline_html

INVARIANTS
  - `_walk` applies SKIP_DIRS, the root `.gitignore` and the include patterns: map, search and
    read all list through it, so generated trees never pollute a repo map.
  - `map` pages with `shown`/`next`/`has_more`; `max_files=0` means every row plus the
    `dirs_matched` summary, so a 700-file tree is one call instead of seven. `outline`
    reports 1-based inclusive ranges, and a Markdown heading owns its whole subtree.
  - Both report `offset` as the start of what they delivered (0 on the first page) and
    `truncated` only when the default page size cut the list; an explicit `max_files`/
    `max_entries` is a served window (`complete:true`, `has_more:true`).
  - A missing path is `ok:false`/`not_found`.
  - Both are read-only and never open a file through the journalled `open()`.

WHERE TO CHANGE
  Which files are listed .................. _walk, map
  The outline grammar for one language ..... _outline_doc / _outline_html
  Paging limits ........................... map, outline
"""
from __future__ import annotations

# --- part 3/5: traversal, map(), outline() ------------------------------------------------
_INDEX = r"""def _walk(root):
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
        return {"ok": all(bool(item.get("ok")) for item in results), "kind": "maps",
                "files": results, "count": len(results), "file_count": len(results),
                "shown": len(results), "total": len(results), "offset": 0, "unit": "file",
                "truncated": any(bool(item.get("truncated")) for item in results),
                "has_more": any(bool(item.get("has_more")) for item in results),
                "text": "\n\n".join(chunks)}
    explicit = max_files is not None
    size = 120 if max_files is None else int(max_files)
    target, rel = _resolve(path)
    if target is None:
        return _fail("wrong_call", "outside_root", "%s is outside the workspace" % rel,
                     _OUTSIDE_USE, path=rel)
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


def _outline_doc(node):
    try:
        text = _ast.get_docstring(node) or ""
    except Exception:
        return ""
    return text.strip().splitlines()[0].strip()[:120] if text.strip() else ""


def _outline_html(lines):
    # HTML landmarks inside a plain page or a JS/TS template literal. A reader can navigate
    # a component by sections, ids and headings before reading it; the JS AST cannot see a
    # template string's markup.
    entries = []
    for number, line in enumerate(lines, 1):
        for match in _re.finditer(
                r"<\s*(section|header|footer|main|nav|article|aside|h[1-6])\b([^>]*)>",
                line, _re.IGNORECASE):
            tag = match.group(1).lower()
            attrs = match.group(2) or ""
            ident = _re.search(r"\bid\s*=\s*[\"']([^\"']+)", attrs, _re.IGNORECASE)
            classes = _re.search(r"\bclass(?:Name)?\s*=\s*[\"']([^\"']+)", attrs, _re.IGNORECASE)
            if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
                tail = line[match.end():]
                title = _re.sub(r"<[^>]*>", "", tail).split("</", 1)[0].strip()
                title = _re.sub(r"\s+", " ", title)[:120]
                name = ("h%s: %s" % (tag[1], title)) if title else tag
                kind = "heading"
            else:
                name = tag
                if ident:
                    name += "#" + ident.group(1)
                if classes:
                    name += "." + classes.group(1).strip().replace(" ", ".")[:80]
                kind = tag
            entries.append({"line": number, "kind": kind, "name": name[:160]})
    return entries


def outline(path, max_entries=None, keys=False):
    # The structural view: headings and definitions with line numbers, so a long file can
    # be navigated before (or instead of) reading it whole. This is not a shell equivalent;
    # it is the map read() and search() do not provide.
    path = _coerce_paths(path)
    explicit = max_entries is not None
    limit = 200 if max_entries is None else max(1, int(max_entries))
    if isinstance(path, (list, tuple, set)):
        results = [outline(item, max_entries=limit, keys=keys) for item in path]
        chunks = []
        for item in results:
            rel = str(item.get("path") or "")
            chunks.append("# " + rel + "\n" + str(item.get("text") or ""))
        return {"ok": all(bool(item.get("ok")) for item in results), "kind": "outlines",
                "files": results, "file_count": len(results), "shown": len(results),
                "total": len(results), "offset": 0, "unit": "file",
                "truncated": any(bool(item.get("truncated")) for item in results),
                "has_more": any(bool(item.get("has_more")) for item in results),
                "text": "\n\n".join(chunks)}
    target, rel = _resolve(path)
    if target is None:
        return _Result(_fail("wrong_call", "outside_root", "%s is outside the workspace" % rel,
                             _OUTSIDE_USE, path=rel))
    if not _os.path.exists(target):
        return _Result(_fail("not_found", "not_found", "no file at %s" % rel,
                             "map(path) lists files; read(path) needs an existing file",
                             path=rel))
    if _os.path.isdir(target):
        return _Result(_fail("wrong_call", "is_directory",
                             "outline() takes a file; map() lists a directory",
                             "map(path) for files, outline(file) for structure", path=rel))
    try:
        body = _read_text(target)
    except OSError as exc:
        return _Result({"ok": False, "kind": "read_error", "path": rel,
                        "error": {"code": "read_failed", "message": str(exc)}})
    lines = body.splitlines()
    ext = _os.path.splitext(target)[1].lower()
    entries = []

    def _key_entries(value):
        if not isinstance(value, _ast.Dict):
            return []
        out = []
        for key, item in zip(value.keys, value.values):
            # Only a key whose value is a real multi-line block is structure; a flat dict
            # such as UNITS would explode into alphabetized noise.
            if not (isinstance(key, _ast.Constant) and isinstance(key.value, str)):
                continue
            start = getattr(item, "lineno", key.lineno)
            end = getattr(item, "end_lineno", start)
            if end > start:
                out.append({"line": key.lineno, "kind": "key", "name": key.value})
        return out

    if ext == ".py":
        try:
            tree = _ast.parse(body)
        except SyntaxError as exc:
            return _Result({"ok": True, "kind": "outline", "path": rel, "entries": [],
                            "total_lines": len(lines),
                            "text": "# %s: cannot parse Python (%s); use read() or search()" % (rel, exc.msg)})
        for node in tree.body:
            if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)):
                kind = "class" if isinstance(node, _ast.ClassDef) else "function"
                entries.append({"line": node.lineno, "kind": kind, "name": node.name,
                                "doc": _outline_doc(node)})
                if isinstance(node, _ast.ClassDef):
                    for sub in node.body:
                        if isinstance(sub, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                            entries.append({"line": sub.lineno, "kind": "method",
                                            "name": node.name + "." + sub.name,
                                            "doc": _outline_doc(sub)})
            elif isinstance(node, _ast.Assign):
                names = [target.id for target in node.targets
                         if isinstance(target, _ast.Name)]
                if names:
                    label = ", ".join(names)
                    key_names = []
                    if isinstance(node.value, _ast.Dict):
                        key_names = [key.value for key in node.value.keys
                                     if isinstance(key, _ast.Constant) and isinstance(key.value, str)]
                    elif isinstance(node.value, (_ast.Tuple, _ast.List)):
                        key_names = [item.value for item in node.value.elts
                                     if isinstance(item, _ast.Constant) and isinstance(item.value, str)]
                    if key_names:
                        shown = ", ".join(key_names[:12]) + (" ..." if len(key_names) > 12 else "")
                        label += " (%s, %d keys): %s" % (
                            "dict" if isinstance(node.value, _ast.Dict) else "sequence",
                            len(key_names), shown)
                    entries.append({"line": node.lineno, "kind": "assign", "name": label})
                    if keys:
                        entries.extend(_key_entries(node.value))
            elif isinstance(node, _ast.AnnAssign) and isinstance(node.target, _ast.Name):
                name = node.target.id
                key_names = []
                if isinstance(node.value, _ast.Dict):
                    key_names = [key.value for key in node.value.keys
                                 if isinstance(key, _ast.Constant) and isinstance(key.value, str)]
                if key_names:
                    shown = ", ".join(key_names[:12]) + (" ..." if len(key_names) > 12 else "")
                    name += " (dict, %d keys): %s" % (len(key_names), shown)
                entries.append({"line": node.lineno, "kind": "assign", "name": name})
                if keys:
                    entries.extend(_key_entries(node.value))
    elif ext in (".md", ".markdown"):
        for number, line in enumerate(lines, 1):
            match = _re.match(r"^(#{1,6})\s+(.*\S)\s*$", line)
            if match:
                entries.append({"line": number, "kind": "h" + str(len(match.group(1))),
                                "name": match.group(2)[:160]})
    elif ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
        for number, line in enumerate(lines, 1):
            match = _re.match(
                r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?"
                r"(function|class|interface|type|enum|const|let|var)\s+([A-Za-z_$][\w$]*)",
                line)
            if match:
                entries.append({"line": number, "kind": match.group(1),
                                "name": match.group(2)})
                continue
            match = _re.match(r"^\s*(?:test|it|describe)\(\s*['\"](.+?)['\"]", line)
            if match:
                entries.append({"line": number, "kind": "test", "name": match.group(1)[:120]})
        entries.extend(_outline_html(lines))
    elif ext in (".html", ".htm", ".vue", ".svelte"):
        entries.extend(_outline_html(lines))
    elif ext in (".css", ".scss", ".less"):
        # Section comments and at-rules are the useful map of a stylesheet; a full selector
        # list is too long to be an outline. Fall back to top-level selectors when there are
        # no comments.
        selectors = []
        for number, line in enumerate(lines, 1):
            text = line.strip()
            if text.startswith("/*"):
                body = text[2:].strip()
                body = body[:-2].strip() if body.endswith("*/") else body
                if body and not body.startswith("=") and not body.startswith("-"):
                    entries.append({"line": number, "kind": "section", "name": body[:140]})
            elif _re.match(r"@(media|supports|layer|container|keyframes)\b", text):
                entries.append({"line": number, "kind": "at-rule", "name": text.split("{", 1)[0].strip()[:140]})
            elif (line and not line[0].isspace() and text.endswith("{")
                  and not text.startswith(("/*", "*", "@", "}", "{"))):
                selectors.append({"line": number, "kind": "rule", "name": text[:-1].strip()[:140]})
        if not entries:
            entries.extend(selectors)
    else:
        for number, line in enumerate(lines, 1):
            match = _re.match(r"^\s*(?:def|class|function|export)\s+([A-Za-z_$][\w$.]*)", line)
            if match:
                entries.append({"line": number, "kind": "def", "name": match.group(1)})
            elif _re.match(r"^#{1,6}\s+", line):
                # In a non-Markdown file a `#` line is a comment, not a heading; label it
                # honestly so a .gitignore/shell banner is not confused with structure.
                entries.append({"line": number, "kind": "comment", "name": line.strip()[:160]})

    entries.sort(key=lambda item: item["line"])
    for index, item in enumerate(entries):
        # A Markdown parent owns its subtree: h2 "Commands" ends at the next heading of the
        # same or shallower level, not at its first h3 child. For other structures the next
        # entry is the boundary.
        match = _re.match(r"^h([1-6])$", str(item.get("kind") or ""))
        if match:
            level = int(match.group(1))
            following = next(
                (other for other in entries[index + 1:]
                 if (m := _re.match(r"^h([1-6])$", str(other.get("kind") or "")))
                 and int(m.group(1)) <= level),
                None,
            )
            end = (following["line"] - 1) if following else len(lines)
        else:
            end = (entries[index + 1]["line"] - 1
                   if index + 1 < len(entries) else len(lines))
        item["end"] = max(item["line"], end)
    total_entries = len(entries)
    truncated = total_entries > limit and not explicit
    entries = entries[:limit]
    if not entries:
        text = "# %s: no structural lines; use read() or search()" % rel
    else:
        header = ("# %s (%d of %d)\n" % (rel, len(entries), total_entries)) if truncated else ""
        text = header + "\n".join(
            "%d-%d: %s %s%s" % (item["line"], item["end"], item["kind"], item["name"],
                                    " (whole file)" if item.get("kind") == "h1"
                                    and item["line"] == 1 and item["end"] == len(lines) else "")
            if item["end"] > item["line"]
            else "%d: %s %s" % (item["line"], item["kind"], item["name"])
            for item in entries)
    return _Result({"ok": True, "kind": "outline", "path": rel, "entries": entries,
                    "total_lines": len(lines), "total_entries": total_entries,
                    "shown": len(entries), "total": total_entries, "offset": 0,
                    "unit": "entry", "truncated": truncated,
                    "has_more": len(entries) < total_entries,
                    "next": {"offset": len(entries), "unit": "entry"} if len(entries) < total_entries else None,
                    "text": text})


"""
