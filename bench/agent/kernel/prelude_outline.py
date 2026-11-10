"""prelude_outline: outline(), the structure verb — PRELUDE fragment 3/5c.

WHAT IT IS
  Raw child source `_OUTLINE`: `outline()` with its file-vs-paths branching, paging and
  rendering. prelude_index.py concatenates it after `_OUTLINE_PARSE`, whose parsers it calls.

ENTRY POINTS
  _OUTLINE   source text; not imported by the kernel. Inside: outline

INVARIANTS
  fragment, not an API: _outline_doc/_outline_html come from the preceding fragment, and the
  error helpers from prelude_setup. Explicit max_entries = served window (complete:true,
  has_more:true); the default page size sets truncated.
  missing path -> ok:false/not_found; in a path list -> partial_failure, good files delivered.

WHERE TO CHANGE
  outline paging and rendering ... outline
  one language's grammar ......... prelude_outline_parse.py
"""
_OUTLINE = r"""def outline(path, max_entries=None, keys=False):
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
        aggregate = {"ok": all(bool(item.get("ok")) for item in results), "kind": "outlines",
                     "files": results, "file_count": len(results), "shown": len(results),
                     "total": len(results), "offset": 0, "unit": "file",
                     "truncated": any(bool(item.get("truncated")) for item in results),
                     "has_more": any(bool(item.get("has_more")) for item in results),
                     "text": "\n\n".join(chunks)}
        if not aggregate["ok"]:
            aggregate.update(_partial_failure(results))
        return aggregate
    target, rel = _resolve(path)
    if target is None:
        return _Result(_outside("wrong_call", rel, "outline", path=rel))
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
