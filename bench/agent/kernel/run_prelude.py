"""run_prelude: RUN_PRELUDE, the one process tool plus the mutation and spawn gates.

WHAT IT IS
  The source string injected into a child that may spawn programs: `exec_argv()` (the typed
  `exec` tool: argv, commands=[[...]], or an explicit shell string), `git_tool()` (the typed
  `git` tool), `run()` where python-only modes are allowed, and the classifier that refuses a
  mutation that would bypass the journal.

ENTRY POINTS
  RUN_PRELUDE   the child source (KERNEL_RUN_PRELUDE); present only when allow_run is on
  In the namespace: exec_argv(), git_tool(), run() (only when KERNEL_SHELL=1), plus the
  private gate: `_mutating_command`, `_HINT`, `_audit_spawn`, `_SpawnBlocked`.

INVARIANTS
  - `exec` never parses a shell string for a program: a shell interpreter with `-c`/`-lc` in
    argv is refused as `shell_via_exec` and points at `exec(shell=<string>)`. Passing a bare
    string as `argv` is the one exception: it is run once through `_run_shell` and the answer
    carries a note, because "fail then retry with shell=" cost two turns for one command.
  - Programs/verbs that change tracked files (`tee`, `cp`, `mv`, `rm`, `sed -i`,
    `git checkout/reset/apply/...`, `>` redirection; a quoted `>` is an argument) are refused
    as `kind: "wrong_call"` with a `use` hint, because they bypass the journal and would make
    diff()/undo() lie. `git add/commit/push/fetch/init/clone` are allowed, as is a mutation
    whose target is an absolute path under a scratch root (/tmp, /var/tmp, /dev/shm, $TMPDIR).
  - Direct spawning (subprocess.Popen, os.system, os.exec*, os.posix_spawn, os.spawn) goes
    through the same classifier via `sys.addaudithook`; a mutating spawn raises
    `_SpawnBlocked`, which the driver turns into a `wrong_call`. Allowed spawns are listed in
    `Cell.runs`. This is a correctness boundary, not a sandbox: a program can still write
    files.
  - Each program starts in a NEW session and its group leader is journalled to
    KERNEL_ORPHAN_JOURNAL, so killing the kernel group cannot leak it (see process.py).
  - `check_cache` answers a repeated command with `kind: "check_cached"` instead of spawning
    it twice.

WHERE TO CHANGE
  What exec/git refuse ......................... `_RUN_SHELL_MUTATORS`, `_RUN_GIT_MUTATORS`,
                                                 `_mutating_command`, `_HINT`, then RUN_NOTE
                                                 in repl.py and AGENTS.md §4 invariant 2
  The spawn gate ............................... `_audit_spawn`, `_gate_check`, `_SpawnBlocked`
  exec's argv/commands/shell shape ............. `exec_argv`
  The typed git actions ........................ `git_tool`
"""

RUN_PRELUDE = r"""
import json as _json, os as _ros, re as _re, signal as _rsig, subprocess as _rsp
_RUN_ROOT = _ros.getcwd()
# exec()/run() children get their own session, so killing the kernel group does not reach them.
# Journal each group leader here; the parent reaps leftovers when the kernel stops/restarts.
_ORPHAN_JOURNAL = _ros.environ.get("KERNEL_ORPHAN_JOURNAL")


def _proc_start(pid):
    try:
        with open("/proc/%s/stat" % pid) as fh:
            return fh.read().rsplit(")", 1)[1].split()[19]
    except Exception:
        return None


def _journal_orphan(pid):
    if not _ORPHAN_JOURNAL:
        return
    try:
        with open(_ORPHAN_JOURNAL, "a") as fh:
            fh.write(_json.dumps({"pid": int(pid), "start": _proc_start(pid)}) + "\n")
    except Exception:
        pass
_RUN_LOG = []
_CHECK_CACHE = {}
_GATE_IN_RUN = 0
_SPAWN_EVENTS = {"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn"}


class _SpawnBlocked(Exception):
    def __init__(self, blocked, command):
        super().__init__("`%s` changes files through a process, so it would bypass the journal; use the helper" % blocked)
        self.blocked = blocked
        self.command = command
# Shell verbs that change the tree. They are refused because they bypass the journal, so
# diff()/undo() could not see them; typed helpers are the only journalled way to mutate.
_RUN_SHELL_MUTATORS = {
    "tee", "touch", "cp", "mv", "rm", "truncate", "shred", "dd", "install",
    "ln", "mkdir", "rmdir", "chmod", "chown", "chgrp", "patch",
}
# Git index/history verbs (add, commit, push, fetch, init, clone) are allowed: they do not
# overwrite working-tree files, and an agent asked to "commit and push" must be able to do it.
# Only verbs that can replace tracked content stay gated.
_RUN_GIT_MUTATORS = {
    "apply", "checkout", "reset", "clean", "stash", "rm", "mv", "pull",
    "merge", "rebase", "cherry-pick", "revert", "am", "switch", "restore",
}
_HINT = {
    "tee": "write(path, content)", "touch": "write(path, '')",
    "cp": "read(path) then write(new_path, text)",
    "mv": "write(new_path, read(path)['text']) then delete(path)",
    "rm": "delete(path)", "truncate": "write(path, '')",
    "sed -i": "splice(path, start, end, text) or edit(path, old, new)",
    "mkdir": "mkdir(path)",
    "patch": "apply(patch) or splice(path, start, end, text)",
    "git": "git add/commit/push are allowed; edit files with splice/edit/apply/commit",
    ">": "splice(path, start, end, text) or write(path, content)", "find -delete": "map() then delete(path)",
    "xargs": "a Python loop",
}


def _segments(command):
    return [part for part in _re.split(r"\|\||&&|\||;|\n", str(command)) if part.strip()]


_TMP_ROOTS = {_ros.path.normpath(p) for p in (
    "/tmp", "/var/tmp", "/dev/shm", _ros.environ.get("TMPDIR") or "/tmp")}


def _abs_outside(token):
    # Only scratch space is exempt: /tmp, /var/tmp, /dev/shm and $TMPDIR. A path outside the
    # workspace is not enough -- `rm -rf /` is also outside it.
    text = str(token).strip().strip("'\"")
    if not text.startswith("/") or "$" in text or ".." in text:
        return False
    path = _ros.path.normpath(text)
    if path == _RUN_ROOT or path.startswith(_RUN_ROOT + _ros.sep):
        return False
    return any(path == root or path.startswith(root + _ros.sep) for root in _TMP_ROOTS)


def _mutator_targets_outside(parts):
    base = _ros.path.basename(parts[0])
    paths = [part for part in parts[1:] if not part.startswith("-")]
    if not paths:
        return False
    # cp/ln/install only read the source; the destination is the mutation.
    if base in ("cp", "ln", "install"):
        return _abs_outside(paths[-1])
    return all(_abs_outside(path) for path in paths)


def _writes_stdout(segment):
    # Quote-aware: `>` inside '...' or "..." is an argument, not a redirect. The old regex
    # refused `python3 -c "print(a > b)"` and nested command strings.
    quote = None
    i, n = 0, len(segment)
    while i < n:
        ch = segment[i]
        if quote:
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch == "\\" and i + 1 < n:
            i += 2
            continue
        if ch in ("'", '"'):
            quote = ch
            i += 1
            continue
        if ch == ">" and (i == 0 or not segment[i - 1].isdigit()):
            j = i + 1
            if j < n and segment[j] == ">":
                j += 1
            while j < n and segment[j].isspace():
                j += 1
            if j < n and segment[j] in ("'", '"'):
                close = segment[j]
                j += 1
                start = j
                while j < n and segment[j] != close:
                    j += 1
                target = segment[start:j]
            else:
                start = j
                while j < n and segment[j] not in " \t\r\n;|&":
                    j += 1
                target = segment[start:j]
            target = target.strip("'\"")
            if target and target not in ("/dev/null", "/dev/stdout", "/dev/stderr", "&1", "&2") \
                    and not _abs_outside(target):
                return True
            i = j
            continue
        i += 1
    return False


def _mutating_command(command, redirection=True):
    # Return the mutating verb in a shell command, or None when it only reads / runs.
    # `redirection` is False for a list argv: there is no shell to parse `>`, so it is a
    # literal argument (e.g. a Python -c source containing `->`), not a file write.
    text = str(command).strip()
    if not text:
        return None
    for segment in _segments(text):
        if redirection and _writes_stdout(segment):
            return ">"
        parts = segment.strip().split()
        if not parts:
            continue
        base = _ros.path.basename(parts[0])
        args = parts[1:]
        names = [_ros.path.basename(arg) for arg in args]
        if base in _RUN_SHELL_MUTATORS:
            if _mutator_targets_outside(parts):
                continue
            return base
        if base == "sed" and any(arg.startswith("-i") or arg.startswith("--in-place") for arg in args):
            return "sed -i"
        if base == "git" and names and names[0] in _RUN_GIT_MUTATORS:
            return "git " + names[0]
        if base in ("bash", "sh", "zsh") and len(parts) > 2 and parts[1] in ("-c", "-lc"):
            inner = _mutating_command(" ".join(parts[2:]), redirection)
            if inner:
                return inner
        if base == "find" and any(arg in ("-delete", "-exec", "-execdir", "-ok", "-okdir")
                                  for arg in args):
            return "find -delete"
        if base == "xargs":
            inner = _mutating_command(" ".join(args), redirection)
            if inner:
                return "xargs " + inner
    return None


def _norm_command(command):
    return " ".join(str(command).split())


def _spawn_argv(event, args):
    # The raw argv: a list means no shell parsed it, a str means a shell did (or os.system).
    if event == "os.system":
        return str(args[0]) if args else ""
    if event in ("subprocess.Popen", "os.exec", "os.posix_spawn"):
        return args[1] if len(args) > 1 else args[0]
    if event == "os.spawn":
        return args[2] if len(args) > 2 else args[0]
    return ""


def _spawn_command(argv):
    if isinstance(argv, (list, tuple)):
        return " ".join(str(item) for item in argv)
    return str(argv)


def _gate_check(command, redirection=True):
    text = str(command).strip()
    if not text:
        return None
    parts = text.split()
    base = _ros.path.basename(parts[0]) if parts else ""
    if base in ("bash", "sh", "zsh") and len(parts) > 2 and parts[1] in ("-c", "-lc"):
        # `bash -c "cmd > f"` is a shell command even when the outer argv is a list.
        inner = " ".join(parts[2:]).strip().strip('"').strip("'")
        return _mutating_command(inner, redirection=True)
    return _mutating_command(text, redirection=redirection)


def _audit_spawn(event, args):
    if event not in _SPAWN_EVENTS:
        return
    argv = _spawn_argv(event, args)
    command = _spawn_command(argv)
    blocked = _gate_check(command, redirection=not isinstance(argv, (list, tuple)))
    if blocked:
        raise _SpawnBlocked(blocked, command)
    if not _GATE_IN_RUN:
        _RUN_LOG.append(command)


import sys as _sys
_sys.addaudithook(_audit_spawn)


def _run_shell(command, timeout=60, cwd=None):
    global _GATE_IN_RUN
    # A list is one call for several commands: the same gate and journal rules apply to each.
    # Each sub-run is a normal _run_shell(), so _RUN_LOG, check_cache and the spawn gate stay exact.
    if isinstance(command, (list, tuple)):
        items = [_run_shell(item, timeout=timeout, cwd=cwd) for item in command]
        stdout = "\n".join(str(item.get("stdout") or "") for item in items).strip()
        stderr = "\n".join(str(item.get("stderr") or "") for item in items).strip()
        return _Result({"ok": all(bool(item.get("ok")) for item in items), "kind": "commands",
                        "count": len(items), "shown": len(items), "total": len(items),
                        "unit": "command", "offset": 0, "truncated": False,
                        "commands": items, "stdout": stdout, "stderr": stderr,
                        "exit_code": next((item.get("exit_code") for item in items
                                            if item.get("exit_code") not in (0, None)), 0)})
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
    return _Result({"ok": True, "kind": "command", "command": str(command), "exit_code": proc.returncode,
            "stdout": out or "", "stderr": err or ""})


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


def exec_argv(argv=None, commands=None, shell=None, timeout=60, cwd=None, env=None, stdin=None):
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
        return _Result({"ok": ok, "kind": "commands", "count": len(items),
                        "shown": len(items), "total": len(items), "unit": "command",
                        "offset": 0, "truncated": False,
                        "results": results, "stdout": stdout, "stderr": stderr,
                        "exit_code": next((item.get("exit_code") for item in items
                                           if item.get("exit_code") not in (0, None)), 0)})
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
    return _Result({"ok": True, "kind": "command", "argv": argv, "command": command,
            "cwd": cwd or _RUN_ROOT, "exit_code": proc.returncode,
            "stdout": out or "", "stderr": err or ""})


def _git_page(text, offset=0, max_chars=None):
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
        changed = []
        branch_name = ""
        for line in out.splitlines():
            if line.startswith("##"):
                branch_name = line[2:].strip().split("...")[0].strip()
                continue
            if len(line) > 3:
                changed.append({"status": line[:2].strip(), "path": line[3:].strip()})
        return _Result({"ok": ok, "kind": "git_status", "action": action,
                        "branch": branch_name, "changed": changed, "clean": not changed,
                        "text": out, "exit_code": code, "stderr": err})
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
