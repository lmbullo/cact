"""run_prelude_notes: plan, status and dupe notes — RUN_PRELUDE fragment 2/8.

WHAT IT IS
  Raw child source `_NOTES`: the advisory note a successful call may carry. `_check_note` names checks()
  when a command is the plan program with the same arguments (a file argument or `-t name` narrows it, so
  no note), `_status_note` names status() for a bare `git status`, and `_typed_dupe` notes a shell
  duplicate of a typed verb once, staying silent for the flags in _DUPE_OPAQUE.

ENTRY POINTS
  _NOTES   source text; inside: _plan_program, _spec_names, _check_note, _status_note, _typed_dupe

INVARIANTS
  a note never refuses and never changes the exit code; it is attached after the result is built.
  reads _SHELL_SPLIT, _CHECK_SPECS, _VERB_DUPE, _DUPE_OPAQUE, _DUPE_NOTED, _CHECK_NOTED from _SETUP.

WHERE TO CHANGE
  plan hint ... _plan_program, _spec_names, _check_note   status hint ... _status_note   dupe ... _typed_dupe
"""
_NOTES = r"""def _plan_program(text):
    # `cd tui && bun test` and `bun test` run with cwd=tui are the same program.
    segments = [part.strip() for part in _SHELL_SPLIT.split(_norm_command(text)) if part.strip()]
    for segment in reversed(segments):
        if not segment.startswith("cd "):
            return segment
    return segments[-1] if segments else ""


def _spec_names(specs, text):
    program = _plan_program(text)
    if not program:
        return []
    names = []
    for item in specs:
        if not isinstance(item, dict):
            continue
        cmd = _norm_command(item.get("cmd") or "")
        if cmd and _plan_program(cmd) == program:
            names.append(str(item.get("name") or cmd[:40]))
    return names


def _check_note(command):
    # A plan command already has a cheaper shape: checks() runs it and reuses the cache.
    # One note per cell, and silent when the command only narrows a plan command.
    if "_check" in _CHECK_NOTED or not _CHECK_SPECS:
        return None
    text = _norm_command(command)
    names = _spec_names(_CHECK_SPECS, text)
    if not names:
        return None
    _CHECK_NOTED.add("_check")
    if len(names) == 1:
        return ("`%s` is a plan check; checks('%s') runs it with the cache in one call."
                % (text, names[0]))
    return ("`%s` is a plan check; checks() runs the plan with the cache in one call."
            % text)


def _status_note():
    # git(action='status') already ran; the note names the one call that adds the journal.
    if "_status" in _STATUS_NOTED:
        return None
    _STATUS_NOTED.add("_status")
    return ("git status is a typed action; status() returns the branch, dirty files and "
            "the session journal in one call.")


def _typed_dupe(argv, cwd=None):
    # (program, typed call) when this argv repeats a typed verb that can express it, else None.
    # A typed verb is workspace-only, so a path outside _RUN_ROOT gets no note: telling the
    # model `cat /tmp/x` is `read(path)` when read('/tmp/x') answers outside_root is a lie.
    if not argv:
        return None
    program = _ros.path.basename(str(argv[0])).lower()
    use = _VERB_DUPE.get(program)
    if not use:
        return None
    args = [str(item) for item in argv[1:]]
    short = "".join(item[1:] for item in args
                    if item.startswith("-") and not item.startswith("--") and len(item) > 1)
    if any(char in _DUPE_OPAQUE.get(program, "") for char in short):
        return None
    if program == "sed" and not any(item.startswith("-i") or item.startswith("--in-place")
                                    for item in args):
        return None
    base = cwd or _RUN_ROOT
    for item in args:
        token = str(item).strip().strip("'\"")
        if not token or token.startswith("-"):
            continue
        if token.startswith("~"):
            token = _ros.path.expanduser(token)
        target = token if token.startswith("/") else _ros.path.join(base, token)
        target = _ros.path.normpath(target)
        if not (target == _RUN_ROOT or target.startswith(_RUN_ROOT + _ros.sep)):
            return None
    return (program, use)


"""
