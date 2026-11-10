"""run_prelude_git: git_tool() and its page — RUN_PRELUDE fragment 6/8.

WHAT IT IS
  Raw child source `_GIT`: `git_tool` (status/diff/show/log/files/add/commit/push/remote/branch) and
  `_git_page`, the offset/max_chars pager every text answer shares. It shells out through exec_argv, so
  the mutation and spawn gates apply unchanged.

ENTRY POINTS
  _GIT   source text; inside: _git_page, git_tool

INVARIANTS
  one `git` argument vector per action, no shell string; a git_action result carries ok/kind/action/paths/
  sha/text/stdout/stderr/exit_code, and paging returns next.offset plus has_more.

WHERE TO CHANGE
  the actions, paths and message handling ... git_tool   paging window ... _git_page
"""
_GIT = r"""def _git_page(text, offset=0, max_chars=None):
    text = str(text or "")
    start = max(0, int(offset or 0))
    explicit = max_chars is not None
    limit = 12000 if max_chars is None else max(0, int(max_chars))
    shown = text[start:] if limit <= 0 else text[start:start + limit]
    end = start + len(shown)
    # offset is the START of this window, next is where to resume; the old shape returned the
    # resume cursor as `offset`, so a first page looked like a continuation.
    return shown, len(text), start, (end if end < len(text) else None), (end < len(text) and not explicit)


def git_tool(action, paths=None, message=None, limit=20, offset=0, max_chars=None,
             stat=False, cached=False, rev="HEAD", remote="origin", branch=None,
             cwd=None):
    # Typed git: no pager, no shell, no giant undifferentiated dump. diff/show page with
    # offset/max_chars; status/log/files return parsed fields plus text.
    action = str(action or "").strip().lower()
    paths = [str(item) for item in paths] if isinstance(paths, (list, tuple)) else (
        [str(paths)] if paths else [])
    base = ["git"]
    if action == "status":
        base += ["status", "--porcelain=v1", "-b"]
    elif action == "diff":
        base += ["diff", "--no-color"]
        if cached:
            base.append("--cached")
        if stat:
            base.append("--stat")
    elif action == "show":
        base += ["show", "--no-color"]
        if stat:
            base.append("--stat")
        if rev:
            base.append(str(rev))
    elif action == "log":
        base += ["log", "--oneline", "-n", str(max(1, int(limit or 20)))]
    elif action == "files":
        base += ["ls-files"]
    elif action == "add":
        base += ["add"] + (["--"] + paths if paths else ["-A"])
    elif action == "commit":
        if not message or not str(message).strip():
            return _Result({"ok": False, "kind": "git_error", "action": action,
                            "error": {"code": "missing_message", "message": "commit needs message"}})
        if paths:
            added = exec_argv(["git", "add", "--"] + paths, cwd=cwd)
            if added.get("exit_code") not in (0, None):
                return _Result({"ok": False, "kind": "git_error", "action": action,
                                "stdout": added.get("stdout") or "",
                                "stderr": added.get("stderr") or ""})
        base += ["commit", "-m", str(message)]
    elif action == "push":
        base += ["push", str(remote or "origin")]
        if branch:
            base.append(str(branch))
    elif action == "remote":
        base += ["remote", "-v"]
    elif action == "branch":
        base += ["branch", "--show-current"]
    else:
        return _Result({"ok": False, "kind": "git_error", "action": action,
                        "error": {"code": "unknown_action",
                                  "message": "action must be status/diff/show/log/files/add/commit/push/remote/branch"}})
    if paths and action in ("diff", "show"):
        base += ["--"] + paths
    result = exec_argv(base, cwd=cwd)
    out = str(result.get("stdout") or "")
    err = str(result.get("stderr") or "")
    code = result.get("exit_code")
    ok = code in (0, None)
    if action in ("diff", "show"):
        text, total, start, nxt, truncated = _git_page(out, offset, max_chars)
        return _Result({"ok": ok, "kind": "git_diff" if action == "diff" else "git_show",
                        "action": action, "paths": paths, "text": text,
                        "total_chars": total, "total": total, "shown": len(text),
                        "offset": start, "unit": "char",
                        "next": {"offset": nxt, "unit": "char"} if nxt is not None else None,
                        "has_more": nxt is not None, "truncated": truncated,
                        "exit_code": code, "stderr": err})
    if action == "status":
        no_repo = code != 0 and ("not a git repository" in err.lower()
                                 or "not a git repo" in err.lower())
        changed = []
        branch_name = ""
        for line in out.splitlines():
            if line.startswith("##"):
                branch_name = line[2:].strip().split("...")[0].strip()
                continue
            if len(line) > 3:
                changed.append({"status": line[:2].strip(), "path": line[3:].strip()})
        result = _Result({"ok": ok or no_repo, "kind": "git_status", "action": action,
                        "git": code == 0, "no_repo": no_repo,
                        "branch": branch_name, "changed": changed, "clean": not changed,
                        "text": out or (err if no_repo else ""), "exit_code": code,
                        "stderr": err})
        note = _status_note()
        if note:
            result["note"] = note
        return result
    if action == "log":
        commits = []
        for line in out.splitlines():
            parts = line.split(" ", 1)
            if len(parts) == 2:
                commits.append({"sha": parts[0], "subject": parts[1]})
        return _Result({"ok": ok, "kind": "git_log", "action": action,
                        "commits": commits, "text": out, "exit_code": code, "stderr": err})
    if action == "files":
        files = [line for line in out.splitlines() if line.strip()]
        return _Result({"ok": ok, "kind": "git_files", "action": action,
                        "files": files, "text": out, "exit_code": code, "stderr": err})
    if action == "branch":
        return _Result({"ok": ok, "kind": "git_branch", "action": action,
                        "branch": out.strip(), "text": out, "exit_code": code, "stderr": err})
    sha = ""
    if action == "commit" and ok:
        probe = exec_argv(["git", "rev-parse", "--short", "HEAD"], cwd=cwd)
        sha = str(probe.get("stdout") or "").strip()
    return _Result({"ok": ok, "kind": "git_action", "action": action,
                    "paths": paths, "sha": sha, "text": out, "stdout": out,
                    "stderr": err, "exit_code": code})


"""
