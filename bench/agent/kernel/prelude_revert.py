"""prelude_revert: revert_check()'s execution helpers — PRELUDE fragment 5/5g.

WHAT IT IS
  Raw child source `_REVERT`: byte capture, digest, git restore, dirty-tree detection, command
  execution and the combined-output shape that `revert_check()` uses to prove a test passed on
  HEAD and failed on the dirty tree. prelude_verbs.py concatenates it before revert_check().

ENTRY POINTS
  _REVERT   source text; inside: _revert_bytes, _revert_digest, _revert_git, _revert_dirty,
            _revert_run, _revert_output

INVARIANTS
  fragment, not an API: restore/run/output shapes are unchanged; revert_check owns the command
  and the tests that proved stdout/stderr survive a restore.

WHERE TO CHANGE
  restore and dirty-tree checks ... _revert_git, _revert_dirty, _revert_bytes
  command execution and output ... _revert_run, _revert_output
"""
_REVERT = r"""def _revert_bytes(path):
    with _open(path, "rb") as fh:
        return fh.read()


def _revert_digest(data):
    # One digest for the restore proof: the same function reads both sides, so a mismatch is
    # never an encoding artefact.
    return _hashlib.sha256(data).hexdigest()[:16]


def _revert_git(argv):
    # Read-only git, bytes out: `show HEAD:<path>` must not pass through a text codec, because a
    # restore that decodes and re-encodes is not a restore. None when git could not run at all.
    try:
        return _sp.run(["git", "-c", "core.quotepath=false"] + argv, cwd=_ROOT,
                       capture_output=True, timeout=30)
    except (OSError, _sp.TimeoutExpired):
        return None


def _revert_dirty():
    # Every TRACKED path git reports as different from HEAD; None when git itself failed. Untracked
    # files are left out on purpose: a test run leaves build artifacts (`__pycache__/`, `.tmp`)
    # that the HEAD swap cannot reach, so refusing on them would make the verb refuse after every
    # run. `paths` itself is checked for trackedness separately. A rename `R  a -> b` names both
    # sides, so the refusal names the path the caller did not pass.
    proc = _revert_git(["status", "--porcelain=v1", "--untracked-files=no"])
    if proc is None or proc.returncode != 0:
        return None
    out = []
    for raw in proc.stdout.decode("utf-8", "replace").splitlines():
        if len(raw) < 4:
            continue
        rest = raw[3:]
        for part in (rest.split(" -> ") if " -> " in rest else [rest]):
            name = part.strip().strip('"')
            if name and name not in out:
                out.append(name)
    return out


def _revert_run(command, argv, shell, timeout):
    # (exit_code, stdout, stderr, problem). A non-zero exit is the answer, not an error, so
    # `problem` stays None for it; only a timeout or a refused/unstartable spawn sets one.
    try:
        proc = _sp.run(command if shell else list(argv), shell=shell, cwd=_ROOT,
                       capture_output=True, text=True, errors="replace", timeout=float(timeout))
    except _sp.TimeoutExpired:
        return None, "", "", "timeout"
    except Exception as exc:
        return None, "", "%s: %s" % (type(exc).__name__, exc), "spawn_failed"
    return proc.returncode, proc.stdout or "", proc.stderr or "", None


def _revert_output(exit_now, out_now, err_now, exit_original, out_old, err_old):
    # Both runs, tailed: the failing assertion is the answer, and a full test dump would spend the
    # turn's context on one check.
    blocks = []
    for label, code, out, err in (("now", exit_now, out_now, err_now),
                                  ("HEAD", exit_original, out_old, err_old)):
        body = (str(out or "") + str(err or "")).strip()
        if len(body) > 2000:
            body = "...[+%d chars]\n" % (len(body) - 2000) + body[-2000:]
        blocks.append("--- %s (exit %s)%s" % (label, code, ("\n" + body) if body else ""))
    return "\n".join(blocks)


"""
