"""run_prelude_gate: the mutation and spawn gate — RUN_PRELUDE fragment 3/8.

WHAT IT IS
  Raw child source `_GATE`: the classifier that refuses a command which changes files through a process.
  `_segments` splits a command line, `_abs_outside`/`_mutator_targets_outside`/`_writes_stdout` decide
  a segment's targets and redirects, `_mutating_command` answers the refusal; `_spawn_argv`, `_spawn_command`
  and `_audit_spawn` gate direct spawn (subprocess.Popen, os.system, os.exec*, os.posix_spawn, os.spawn),
  installed here as the child's sys.addaudithook at its original point in the program.

ENTRY POINTS
  _GATE   source text; inside: _segments, _TMP_ROOTS, _abs_outside, _mutator_targets_outside, _writes_stdout,
          _mutating_command, _norm_command, _spawn_argv, _spawn_command, _gate_check, _audit_spawn

INVARIANTS
  allowed: git add/commit/push/fetch/init/clone and an absolute target under /tmp, /var/tmp, /dev/shm, $TMPDIR.
  a mutating spawn raises _SpawnBlocked -> the driver answers wrong_call; a correctness boundary, not a sandbox.
  the hook install stays in this fragment: it must run after _audit_spawn is bound and before _run_shell.

WHERE TO CHANGE
  what counts as a mutation ... _RUN_SHELL_MUTATORS, _HINT (run_prelude_setup.py), _mutating_command
  the audit hook ... _audit_spawn, _spawn_argv, _gate_check
"""
_GATE = r"""def _segments(command):
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


"""
