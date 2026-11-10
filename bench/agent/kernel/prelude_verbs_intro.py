"""prelude_verbs_intro: verbs()/help/checks and the write-path constants — PRELUDE fragment 5/5a.

WHAT IT IS
  Raw child source `_VERBS_INTRO`: the verbs()/help page, checks(), the editable-extension set
  and the literal-newline hint. prelude_verbs.py concatenates it before the mutation verbs.

ENTRY POINTS
  _VERBS_INTRO   source text; inside: verbs, help, checks, _ESCAPE_OK_EXT, _literal_newline_note

INVARIANTS
  fragment, not an API: help()/verbs() read `_VERB_META` and `_VERB_MECHANICS` from the setup
  fragment, and checks() reads the plan from the child env. Text and ordering are unchanged.

WHERE TO CHANGE
  the verbs page ......... verbs / help
  the checks() shorthand . checks
"""
_VERBS_INTRO = r"""def verbs(topic=None):
    # The live catalog: the same registry the parent uses for tool specs. Summaries come
    # from KERNEL_VERBS; signatures are introspected here, so they cannot drift from the
    # functions a typed call actually dispatches to.
    import inspect as _inspect
    meta = globals().get("_VERB_META") or {}
    out = []
    for name in sorted(meta):
        if topic and str(topic) not in name:
            continue
        fn = globals().get((meta[name].get("function") or name))
        if not callable(fn):
            continue
        try:
            signature = str(_inspect.signature(fn))
        except (TypeError, ValueError):
            signature = "(...)"
        out.append({"name": name, "signature": signature,
                    "summary": meta[name].get("summary") or "",
                    "mutates": bool(meta[name].get("mutates"))})
    if topic and not out:
        # A silent empty list read as success and cost a retry. An unknown topic is a refusal
        # with the nearest names, so the next call is right.
        names = sorted(name for name in meta if callable(globals().get((meta[name].get("function") or name))))
        near = _difflib.get_close_matches(str(topic), names, n=3, cutoff=0.3) or []
        return _fail("unknown_verb", "unknown_verb", "no verb matches %r" % (topic,),
                     "help() lists every verb; help(name) matches a name",
                     available=near or names)
    return {"ok": True, "kind": "verbs", "verbs": out,
            "text": "\n".join("%s%s  %s" % (item["name"], item["signature"], item["summary"])
                             for item in out)}


help = verbs


def checks(name=None):
    # Parent-side tool: the loop's Verifier owns the plan, the cache and the rows, so a python
    # cell cannot run it. This stub keeps the registry truthful and help() honest; the typed
    # `checks` call is intercepted before the kernel.
    return _fail("checks", "parent_only", "checks() runs in the agent loop, not a python cell",
                 "call the typed checks tool, or checks(name=...) as a tool")


# A literal backslash-n in source code is real content; in prose it is a model that
# double-escaped. Only the second case gets a note, so a code write stays quiet.
_ESCAPE_OK_EXT = {".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".json", ".c", ".h",
                  ".cc", ".go", ".rs", ".sh", ".yml", ".yaml", ".toml", ".css", ".java"}


def _literal_newline_note(path, content):
    if "\n" in content or "\r" in content:
        return None
    if "\\n" not in content:
        return None
    if _os.path.splitext(str(path))[1].lower() in _ESCAPE_OK_EXT:
        return None
    return ("the content has a literal backslash-n and no real newline, so it was written as "
            "%d line(s) with %d visible escape(s); pass real newlines if you meant line breaks, "
            "and read(path) to check what landed" % (len(content.splitlines()),
                                                     content.count("\\n")))


"""
