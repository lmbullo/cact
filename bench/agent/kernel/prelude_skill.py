"""prelude_skill: skill(), the project-procedure verb — PRELUDE fragment 4/5f.

WHAT IT IS
  Raw child source `_SKILL`: `skill()` lists `.agent/skills/<name>.md` procedures or returns
  one by name, read-only. prelude_read.py concatenates it last, so it sees the read helpers.

ENTRY POINTS
  _SKILL   source text; inside: skill

INVARIANTS
  fragment, not an API: not_found is an answer about the skill set, never an exception; the
  verb stays read-only and never maps the whole tree.

WHERE TO CHANGE
  skill lookup and rendering ... skill
"""
_SKILL = r"""def skill(name=None, root="."):
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
