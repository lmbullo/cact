"""prelude_commit: commit(), the journal checkpoint verb — PRELUDE fragment 5/5f.

WHAT IT IS
  Raw child source `_COMMIT`: `commit()` records the given paths as one journalled checkpoint
  (optionally under git) without rewriting their bytes. prelude_verbs.py concatenates it after
  apply().

ENTRY POINTS
  _COMMIT   source text; inside: commit

INVARIANTS
  fragment, not an API: the same _commit rows and the same result envelope as before; a commit
  refusal is still a structured answer, not an exception.

WHERE TO CHANGE
  commit rows and refusals ... commit
"""
_COMMIT = r"""def commit(files, force=False):
    if not isinstance(files, dict) or not files:
        return _fail("commit", "bad_arguments", "commit takes a dict of {path: text}",
                     "commit({'a.py': text, 'b.py': text})")
    done = []
    for path, content in files.items():
        if not isinstance(content, str):
            return _fail("commit", "bad_arguments", "content must be text",
                         "commit({'a.py': text})", path=str(path))
        result = write(path, content, force=force)
        if not result.get("ok"):
            inner = result.get("error") or {}
            return _fail("commit", inner.get("code") or "commit_failed",
                         inner.get("message") or "write failed",
                         inner.get("use") or "write(path, content, force=True)",
                         path=str(path), detail=result)
        done.append({"path": result["path"], "adds": result["adds"], "dels": result["dels"]})
    return {"ok": True, "kind": "commit", "files": done}


"""
