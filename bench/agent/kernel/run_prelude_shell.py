"""run_prelude_shell: _run_shell and the shell-argv escape — RUN_PRELUDE fragment 4/8.

WHAT IT IS
  Raw child source `_SHELL`: `_run_shell` runs one program (or a list, in order, stopping at the first
  non-zero exit) as a subprocess with its own session, journalling the leader and applying the gate; and
  `_exec_shell_string` runs a bare argv string once through the shell, with a note.

ENTRY POINTS
  _SHELL   source text; inside: _run_shell, _exec_shell_string

INVARIANTS
  returns the result dict (ok/kind/stdout/stderr/exit_code/text); stdout and stderr stay separate.
  _GATE_IN_RUN suppresses gate re-entry for the child of a gate check; _RUN_LOG keeps every command.

WHERE TO CHANGE
  subprocess, session, timeout, kill ... _run_shell   bare-string argv ... _exec_shell_string
"""
_SHELL = r"""def _run_shell(command, timeout=60, cwd=None):
    global _GATE_IN_RUN
    # A list is one call for several commands: the same gate and journal rules apply to each.
    # Each sub-run is a normal _run_shell(), so _RUN_LOG, check_cache and the spawn gate stay exact.
    if isinstance(command, (list, tuple)):
        items = [_run_shell(item, timeout=timeout, cwd=cwd) for item in command]
        stdout = "\n".join(str(item.get("stdout") or "") for item in items).strip()
        stderr = "\n".join(str(item.get("stderr") or "") for item in items).strip()
        result = _Result({"ok": all(bool(item.get("ok")) for item in items), "kind": "commands",
                        "count": len(items), "shown": len(items), "total": len(items),
                        "unit": "command", "offset": 0, "truncated": False,
                        "commands": items, "stdout": stdout, "stderr": stderr,
                        "exit_code": next((item.get("exit_code") for item in items
                                            if item.get("exit_code") not in (0, None)), 0)})
        note = next((item.get("note") for item in items if item.get("note")), None)
        if note:
            result["note"] = note
        return result
    _RUN_LOG.append(str(command))
    cached = _CHECK_CACHE.get(_norm_command(command))
    if cached:
        if isinstance(cached, dict):
            text = str(cached.get("text") or "")
            ok = bool(cached.get("ok", True))
        else:
            text, ok = str(cached), True
        return _Result({"ok": True, "kind": "check_cached", "command": str(command),
                "result": text, "exit_code": 0 if ok else 1, "stdout": text, "stderr": "",
                "note": "this command is the project check for the current mutation batch; its recorded result is authoritative."})
    blocked = _mutating_command(command)
    if blocked:
        use = _HINT.get(blocked) or _HINT.get(blocked.split()[0], "splice(), edit() or apply()")
        return _Result({
            "ok": False,
            "kind": "wrong_call",
            "command": str(command),
            "blocked": blocked,
            "error": {
                "code": "mutation_via_run",
                "message": f"`{blocked}` changes files through the shell, so it would bypass the "
                           "journal and diff()/undo() could not see it. Use the helper.",
                "use": use,
            },
            "exit_code": None,
            "stdout": "",
            "stderr": "",
        })
    extend = globals().get("_CELL_EXTEND")
    if extend is not None:
        extend(float(timeout) + 20)
    try:
        _GATE_IN_RUN += 1
        try:
            proc = _rsp.Popen(
                str(command), shell=True, cwd=cwd or _RUN_ROOT,
                stdout=_rsp.PIPE, stderr=_rsp.PIPE, text=True,
                encoding="utf-8", errors="replace", executable="/bin/bash",
                start_new_session=True,
            )
            _journal_orphan(proc.pid)
        finally:
            _GATE_IN_RUN -= 1
    except _SpawnBlocked as exc:
        use = _HINT.get(str(exc.blocked)) or _HINT.get(str(exc.blocked).split()[0], "splice(), edit() or apply()")
        return _Result({"ok": False, "kind": "wrong_call", "command": str(command),
                "blocked": exc.blocked,
                "error": {"code": "mutation_via_run", "message": str(exc), "use": use},
                "exit_code": None, "stdout": "", "stderr": ""})
    except OSError as exc:
        return _Result({"ok": False, "kind": "spawn_error", "command": str(command),
                "error": {"code": "spawn_failed", "message": str(exc)},
                "exit_code": None, "stdout": "", "stderr": ""})
    try:
        out, err = proc.communicate(timeout=float(timeout))
    except _rsp.TimeoutExpired:
        try:
            _ros.killpg(_ros.getpgid(proc.pid), _rsig.SIGKILL)
        except OSError:
            proc.kill()
        try:
            out, err = proc.communicate(timeout=10)
        except Exception:
            out, err = "", ""
        return _Result({"ok": True, "kind": "timeout", "command": str(command), "timeout_s": float(timeout),
                "exit_code": None, "stdout": out or "", "stderr": err or "",
                "note": "the command hit its own timeout and its process group was killed; the kernel is intact"})
    result = _Result({"ok": True, "kind": "command", "command": str(command), "exit_code": proc.returncode,
            "stdout": out or "", "stderr": err or ""})
    note = _check_note(command)
    if note:
        result["note"] = note
    return result


run = _run_shell


def _exec_shell_string(argv):
    for index, item in enumerate(argv):
        if _ros.path.basename(str(item)) not in ("bash", "sh", "zsh", "dash", "ksh"):
            continue
        tail = argv[index + 1:]
        if any(str(arg) == "-c" or (str(arg).startswith("-") and not str(arg).startswith("--")
                                    and "c" in str(arg)[1:]) for arg in tail):
            return True
    return False


"""
