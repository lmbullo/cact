"""run_prelude_exec: exec_argv(), the one typed process tool — RUN_PRELUDE fragment 5/8.

WHAT IT IS
  Raw child source `_EXEC`: `exec_argv` runs one program with an explicit argv, or a list of commands
  (commands=[...]) in order, stopping at the first non-zero exit; cwd replaces `cd`, env and stdin are
  explicit, stdout/stderr stay separate, and the orphan journal, the mutation gate and check_cache apply.

ENTRY POINTS
  _EXEC   source text; inside: exec_argv

INVARIANTS
  never parses a shell string: a shell with -c/-lc in argv -> shell_via_exec -> exec(shell=<string>).
  a mutation bypass -> kind:"wrong_call" + a use hint; a plan command carries one note, never a refusal.

WHERE TO CHANGE
  argv handling, env, stdin, paging notes ... exec_argv
"""
_EXEC = r"""def exec_argv(argv=None, commands=None, shell=None, timeout=60, cwd=None, env=None, stdin=None):
    # The typed process tool: a program and its argv, never a shell string. A list of
    # commands runs in order and stops at the first non-zero exit. cwd replaces `cd`,
    # stdout/stderr stay separate, and the same orphan journal and mutation gate apply.
    global _GATE_IN_RUN
    if shell is not None:
        if argv is not None or commands is not None:
            return _Result({"ok": False, "kind": "bad_args", "error": {
                "code": "both_shell_and_argv",
                "message": "pass shell=<string> or argv/commands, not both",
                "use": "exec(shell='git status && git diff --stat')"}})
        if not isinstance(shell, str) or not shell.strip():
            return _Result({"ok": False, "kind": "bad_args", "error": {
                "code": "bad_shell", "message": "shell must be a non-empty string",
                "use": "exec(shell='git status && git diff --stat')"}})
        return _run_shell(shell, timeout=timeout, cwd=cwd)
    if commands is not None:
        if argv is not None:
            return _Result({"ok": False, "kind": "bad_args", "error": {
                "code": "both_argv_and_commands",
                "message": "pass argv=[...] or commands=[[...], ...], not both",
                "use": "exec(argv=['git','status']) or exec(commands=[['git','status'], ['git','diff']])"}})
        if not isinstance(commands, (list, tuple)) or not commands:
            return _Result({"ok": False, "kind": "bad_args", "error": {
                "code": "bad_commands", "message": "commands must be a non-empty list of argv lists",
                "use": "exec(commands=[['git','status'], ['git','diff']])"}})
        for index, item in enumerate(commands):
            if isinstance(item, str):
                return _Result({"ok": False, "kind": "bad_args", "index": index, "error": {
                    "code": "bad_commands",
                    "message": "each command must be an argv list, not a shell string",
                    "use": "exec(commands=[['git','status'], ['git','diff']]) or exec(shell='git status && git diff')"}})
            if (not isinstance(item, (list, tuple)) or not item
                    or any(isinstance(part, (list, tuple, dict, set)) for part in item)):
                return _Result({"ok": False, "kind": "bad_args", "index": index, "error": {
                    "code": "bad_commands",
                    "message": "each command must be a non-empty argv list of scalars",
                    "use": "exec(commands=[['git','status'], ['git','diff']])"}})
        items = []
        for item in commands:
            result = exec_argv(item, timeout=timeout, cwd=cwd, env=env, stdin=stdin)
            items.append(result)
            if not result.get("ok") or result.get("exit_code") not in (0, None):
                break
        ok = all(bool(item.get("ok")) and item.get("exit_code") in (0, None) for item in items)
        stdout = "\n".join(str(item.get("stdout") or "") for item in items).strip()
        stderr = "\n".join(str(item.get("stderr") or "") for item in items).strip()
        results = [{"argv": item.get("argv"), "exit_code": item.get("exit_code"),
                    "ok": item.get("ok")} for item in items]
        result = _Result({"ok": ok, "kind": "commands", "count": len(items),
                        "shown": len(items), "total": len(items), "unit": "command",
                        "offset": 0, "truncated": False,
                        "results": results, "stdout": stdout, "stderr": stderr,
                        "exit_code": next((item.get("exit_code") for item in items
                                           if item.get("exit_code") not in (0, None)), 0)})
        note = next((item.get("note") for item in items if item.get("note")), None)
        if note:
            result["note"] = note
        return result
    if isinstance(argv, str):
        # A model that passes the whole command as argv gets it run once, with a note. The old
        # shell_string refusal was correct but cost a retry on the most common call shape.
        result = _run_shell(argv, timeout=timeout, cwd=cwd)
        note = ("argv was a shell string; it ran through exec(shell=...) so shell syntax "
                "applied. Pass argv=['prog','arg'] for a literal program.")
        result["note"] = note
        result["text"] = str(result.get("stdout") or "")
        return result
    if (not isinstance(argv, (list, tuple)) or not argv
            or any(isinstance(item, (list, tuple, dict, set)) for item in argv)):
        return _Result({"ok": False, "kind": "bad_args", "error": {
            "code": "bad_argv", "message": "argv must be a non-empty list of scalars",
            "use": "exec(argv=['python3','bench/smoke.py'])"}})
    argv = [str(item) for item in argv]
    command = " ".join(argv)
    _RUN_LOG.append(command)
    cached = _CHECK_CACHE.get(_norm_command(command))
    if cached:
        if isinstance(cached, dict):
            text = str(cached.get("text") or "")
            ok = bool(cached.get("ok", True))
        else:
            text, ok = str(cached), True
        return _Result({"ok": True, "kind": "check_cached", "command": command,
                "result": text, "exit_code": 0 if ok else 1, "stdout": text, "stderr": "",
                "note": "this command is the project check for the current mutation batch; its recorded result is authoritative."})
    blocked = _gate_check(command, redirection=False)
    if blocked:
        use = _HINT.get(blocked) or _HINT.get(blocked.split()[0], "splice(), edit() or apply()")
        return _Result({"ok": False, "kind": "wrong_call", "command": command,
                "blocked": blocked,
                "error": {"code": "mutation_via_exec",
                          "message": f"`{blocked}` changes files through a program, so it would bypass the journal; use the helper.",
                          "use": use},
                "exit_code": None, "stdout": "", "stderr": ""})
    if _exec_shell_string(argv):
        return _Result({"ok": False, "kind": "wrong_call", "command": command,
                "blocked": "bash -c",
                "error": {"code": "shell_via_exec",
                          "message": "exec(argv) is for programs; a shell interpreter with -c/-lc is a shell string. Use exec(shell=<command>) explicitly.",
                          "use": "exec(shell='git status && git diff --stat') or exec(commands=[[...],[...]])"},
                "exit_code": None, "stdout": "", "stderr": ""})
    if env is not None and not isinstance(env, dict):
        return _Result({"ok": False, "kind": "bad_args", "error": {
            "code": "bad_env", "message": "env must be an object of string values",
            "use": "exec(argv=['git','status'], env={'GIT_PAGER': 'cat'})"}})
    child_env = None
    if env:
        child_env = dict(_ros.environ)
        child_env.update({str(key): str(value) for key, value in env.items()})
    extend = globals().get("_CELL_EXTEND")
    if extend is not None:
        extend(float(timeout) + 20)
    try:
        _GATE_IN_RUN += 1
        try:
            proc = _rsp.Popen(
                argv, shell=False, cwd=cwd or _RUN_ROOT,
                stdout=_rsp.PIPE, stderr=_rsp.PIPE, text=True,
                encoding="utf-8", errors="replace",
                stdin=_rsp.PIPE if stdin is not None else _rsp.DEVNULL,
                env=child_env, start_new_session=True,
            )
            _journal_orphan(proc.pid)
        finally:
            _GATE_IN_RUN -= 1
    except _SpawnBlocked as exc:
        use = _HINT.get(str(exc.blocked)) or _HINT.get(str(exc.blocked).split()[0], "splice(), edit() or apply()")
        return _Result({"ok": False, "kind": "wrong_call", "command": command,
                "blocked": exc.blocked,
                "error": {"code": "mutation_via_exec", "message": str(exc), "use": use},
                "exit_code": None, "stdout": "", "stderr": ""})
    except OSError as exc:
        return _Result({"ok": False, "kind": "spawn_error", "command": command,
                "error": {"code": "spawn_failed", "message": str(exc)},
                "exit_code": None, "stdout": "", "stderr": ""})
    try:
        out, err = proc.communicate(input=(str(stdin) if stdin is not None else None),
                                    timeout=float(timeout))
    except _rsp.TimeoutExpired:
        try:
            _ros.killpg(_ros.getpgid(proc.pid), _rsig.SIGKILL)
        except OSError:
            proc.kill()
        try:
            out, err = proc.communicate(timeout=10)
        except Exception:
            out, err = "", ""
        return _Result({"ok": True, "kind": "timeout", "argv": argv, "command": command,
                "timeout_s": float(timeout), "exit_code": None, "stdout": out or "", "stderr": err or "",
                "note": "the program hit its own timeout and its process group was killed; the kernel is intact"})
    result = _Result({"ok": True, "kind": "command", "argv": argv, "command": command,
            "cwd": cwd or _RUN_ROOT, "exit_code": proc.returncode,
            "stdout": out or "", "stderr": err or ""})
    dupe = _typed_dupe(argv, cwd)
    if dupe and dupe[0] not in _DUPE_NOTED:
        _DUPE_NOTED.add(dupe[0])
        result["note"] = ("`%s` repeats a typed verb; %s answers it in one call."
                          % (dupe[0], dupe[1]))
    check_note = _check_note(command)
    if check_note and not result.get("note"):
        result["note"] = check_note
    return result


"""
