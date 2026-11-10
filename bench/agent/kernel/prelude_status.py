"""prelude_status: status(), the one read-only repository state verb — PRELUDE fragment 2/5d.

WHAT IT IS
  Raw child source `_STATUS`: `_not_a_repo` and `status()`, which reports branch, journal paths
  and ahead/behind without mutating or journaling. prelude_journal.py concatenates it after the
  diff verbs.

ENTRY POINTS
  _STATUS   source text; inside: _not_a_repo, status

INVARIANTS
  fragment, not an API: a non-git directory is a valid state (`git: false`, `no_repo: true`), not
  a failed call; `status` never journals.

WHERE TO CHANGE
  branch / journal / ahead-behind shape ... status
"""
_STATUS = r"""def _not_a_repo(stderr):
    # git exits 128 for a directory outside a work tree. That is a readable state, not a tool
    # failure: the journal half of status() is still valid and the caller should not spend a
    # repair turn on it.
    text = str(stderr or "").lower()
    return "not a git repository" in text or "not a git repo" in text


def status(cwd=None):
    # One call for tree state: branch, capped short status, ahead/behind, and the session
    # journal's paths with add/del counts (never the diff text). Read-only: no journal row and
    # no mutation, so it is safe between edits. No repository is a state, not an error.
    base = cwd or _ROOT
    try:
        proc = _sp.run(["git", "status", "--porcelain=v1", "-b"], cwd=base,
                       capture_output=True, text=True, errors="replace", timeout=10)
        out, err, code = proc.stdout or "", proc.stderr or "", proc.returncode
    except (OSError, _sp.TimeoutExpired) as exc:
        out, err, code = "", "%s: %s" % (type(exc).__name__, exc), None
    no_repo = code != 0 and _not_a_repo(err)
    branch, ahead, behind = "", None, None
    changed = []
    for line in out.splitlines():
        if line.startswith("## "):
            head = line[3:].strip()
            meta = _re.search(r"\[(.+)\]$", head)
            if meta:
                found = _re.search(r"ahead (\d+)", meta.group(1))
                if found:
                    ahead = int(found.group(1))
                found = _re.search(r"behind (\d+)", meta.group(1))
                if found:
                    behind = int(found.group(1))
                head = head[:meta.start()].strip()
            branch = head.split("...")[0].strip()
            continue
        if len(line) > 3:
            changed.append({"status": line[:2].strip(), "path": line[3:].strip()})
    journal = diff_tool(max_chars=0)
    files = [{"path": item.get("path"), "state": item.get("state"),
              "adds": int(item.get("adds") or 0), "dels": int(item.get("dels") or 0)}
             for item in journal.get("files") or []]
    adds = sum(item["adds"] for item in files)
    dels = sum(item["dels"] for item in files)
    lines = ["branch: %s%s" % (branch or ("(no git repository)" if no_repo else "(unknown)"),
                               "" if ahead is None and behind is None
                               else " (ahead %s, behind %s)" % (ahead, behind)),
             "git: %s" % ("not a repository" if no_repo else "%d changed" % len(changed))]
    for item in changed[:15]:
        lines.append("  %-2s %s" % (item["status"], item["path"]))
    if len(changed) > 15:
        lines.append("  +%d more" % (len(changed) - 15))
    lines.append("journal: %d file(s) +%d -%d" % (len(files), adds, dels))
    for item in files[:10]:
        lines.append("  %s +%d -%d" % (item["path"], item["adds"], item["dels"]))
    if len(files) > 10:
        lines.append("  +%d more" % (len(files) - 10))
    if err.strip() and not no_repo:
        lines.append("git error: " + err.strip().splitlines()[0][:200])
    text = "\n".join(lines)
    return {"ok": code in (0, None) or no_repo, "kind": "status", "git": code == 0,
            "no_repo": no_repo, "branch": branch,
            "ahead": ahead, "behind": behind, "changed": changed[:50],
            "changed_total": len(changed), "clean": not changed and not files,
            "files": files, "file_count": len(files), "adds": adds, "dels": dels,
            "ignored": int(journal.get("ignored") or 0), "text": text}


"""
