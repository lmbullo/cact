"""prelude_outline_parse: outline()'s language parsers — PRELUDE fragment 3/5b.

WHAT IT IS
  Raw child source `_OUTLINE_PARSE`: `_outline_doc` (Markdown headings and Python/TS
  definitions) and `_outline_html` (HTML sections and headings), the parsers `outline()` calls.
  prelude_index.py concatenates it before `_OUTLINE`.

ENTRY POINTS
  _OUTLINE_PARSE   source text; inside: _outline_doc, _outline_html

INVARIANTS
  fragment, not an API: outline() calls both, so the pair is concatenated in one PRELUDE.
  each parser returns entries with 1-based inclusive ranges; a Markdown heading owns its subtree.

WHERE TO CHANGE
  one language's outline grammar ... _outline_doc / _outline_html
"""
_OUTLINE_PARSE = r"""def _outline_doc(node):
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


"""
