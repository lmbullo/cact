"""prelude_revert_check: revert_check(), the HEAD-vs-dirty proof verb — PRELUDE fragment 5/5h.

WHAT IT IS
  Raw child source `_REVERT_CHECK`: `revert_check(paths, cmd, timeout=120)` runs a test on HEAD
  and on the dirty tree, reporting the one verdict that proves the test is meaningful.
  prelude_verbs.py concatenates it last; `_snapshot()` follows it in the anchor.

ENTRY POINTS
  _REVERT_CHECK   source text; inside: revert_check

INVARIANTS
  fragment, not an API: the three verdict states (passes on HEAD, dirty failure, failed restore)
  and the timeout/restore behavior are byte-identical; it never leaves the tree dirty.

WHERE TO CHANGE
  verdict states and restore sequencing ... revert_check
"""
_REVERT_CHECK = r"""def revert_check(paths, cmd, timeout=120):
    # "Does this test fail without the fix?" Swap `paths` back to their HEAD content, run `cmd`,
    # and ALWAYS put the working copy back. A test that also passes on the original code proves
    # nothing about the change, so it comes back broken_test: true.
    if isinstance(paths, str):
        paths = _coerce_paths(paths)
        if isinstance(paths, str):
            paths = [paths]
    if not isinstance(paths, (list, tuple)) or not paths:
        return _fail("revert_check", "bad_arguments", "paths must be a non-empty list of paths",
                     "revert_check(paths=['calc.py'], cmd=['python3', '-m', 'pytest', '-q'])")
    shell = isinstance(cmd, str)
    if shell:
        argv, command = None, cmd.strip()
    elif (isinstance(cmd, (list, tuple)) and cmd
            and not any(isinstance(part, (list, tuple, dict, set)) for part in cmd)):
        argv, command = [str(part) for part in cmd], " ".join(str(part) for part in cmd)
    else:
        return _fail("revert_check", "bad_arguments", "cmd must be an argv list or a shell string",
                     "revert_check(paths=['calc.py'], cmd=['python3', '-m', 'pytest', '-q'])")
    if not command:
        return _fail("revert_check", "bad_arguments", "cmd is empty",
                     "revert_check(paths=['calc.py'], cmd=['python3', '-m', 'pytest', '-q'])")
    try:
        timeout = float(timeout)
    except (TypeError, ValueError):
        return _fail("revert_check", "bad_arguments", "timeout must be a number of seconds",
                     "revert_check(paths=[...], cmd=[...], timeout=120)")
    extend = globals().get("_CELL_EXTEND")
    if callable(extend):
        # Two runs of `timeout` plus two swaps. Without this the kernel kills the cell mid-test
        # and the restore, which lives in this process, never runs.
        extend(2.0 * timeout + 30)
    # --- the one path set, resolved and constrained -------------------------------------
    targets = []
    for item in paths:
        target, rel = _resolve(str(item))
        if target is None:
            return _outside("revert_check", str(item), "revert_check")
        if rel not in [one[1] for one in targets]:
            targets.append((target, rel))
    mine = [rel for _, rel in targets]
    for target, rel in targets:
        if not _os.path.isfile(target):
            return _fail("revert_check", "not_found",
                         "%s is not a file in the working tree" % rel,
                         "revert_check swaps a file's content; it cannot create one", path=rel)
        proc = _revert_git(["ls-files", "--error-unmatch", "--", rel])
        if proc is None:
            return _fail("revert_check", "no_git", "git is not available here",
                         "run revert_check in a git checkout", path=rel)
        if proc.returncode != 0:
            return _fail("revert_check", "untracked",
                         "%s is untracked, so HEAD has no version to swap in" % rel,
                         "commit it first (git add, then git commit); revert_check restores "
                         "from HEAD", path=rel)
    dirty = _revert_dirty()
    if dirty is None:
        return _fail("revert_check", "no_git", "git status failed in %s" % _ROOT,
                     "run revert_check in a git checkout")
    others = [item for item in dirty if item not in mine]
    if others:
        return _fail("revert_check", "dirty_tree",
                     "the working tree is dirty outside paths: %s" % ", ".join(others[:6]),
                     "commit or discard those paths first; revert_check touches only `paths` "
                     "and never stashes", dirty=others[:20])
    # --- the original content, and what the tree holds now -------------------------------
    originals, current, changed = {}, {}, []
    for target, rel in targets:
        proc = _revert_git(["show", "HEAD:%s" % rel])
        if proc is None or proc.returncode != 0:
            return _fail("revert_check", "not_in_head", "HEAD has no version of %s" % rel,
                         "commit the file once before swapping it", path=rel)
        originals[rel] = proc.stdout
        try:
            current[rel] = _revert_bytes(target)
        except OSError as exc:
            return _fail("revert_check", "unreadable", str(exc),
                         "read(path) names the failure", path=rel)
        if originals[rel] != current[rel]:
            changed.append(rel)
    exit_now, out_now, err_now, problem = _revert_run(command, argv, shell, timeout)
    if problem == "timeout":
        return _fail("revert_check", "timeout",
                     "the command did not finish in %ss" % timeout,
                     "raise timeout= or narrow the command; nothing was swapped",
                     command=command)
    if problem:
        return _fail("revert_check", "spawn_failed",
                     "the command did not start: %s" % str(err_now).strip()[:200],
                     "exec(argv=[...]) answers the same question without a swap; check the "
                     "program name", command=command)
    # --- swap, run the original, and put the working copy back no matter what ------------
    exit_original, out_old, err_old, problem_old = None, "", "", None
    failures, swapped = [], True
    try:
        for target, rel in targets:
            with _open(target, "wb") as fh:
                fh.write(originals[rel])
        exit_original, out_old, err_old, problem_old = _revert_run(command, argv, shell, timeout)
    except OSError as exc:
        swapped = False
        failures.append("swap: %s" % exc)
    finally:
        for target, rel in targets:
            try:
                with _open(target, "wb") as fh:
                    fh.write(current[rel])
            except OSError as exc:
                failures.append("%s: %s" % (rel, exc))
    digests, expected = {}, {}
    for target, rel in targets:
        expected[rel] = _revert_digest(current[rel])
        try:
            digests[rel] = _revert_digest(_revert_bytes(target))
        except OSError:
            digests[rel] = None
    restored = all(digests[rel] == expected[rel] for rel in expected)
    broken = exit_original == 0
    stdout = _revert_output(exit_now, out_now, err_now, exit_original, out_old, err_old)
    text = ("revert_check: broken_test=%s exit_now=%s exit_with_original=%s restored=%s"
            % (broken, exit_now, exit_original, restored))
    if not swapped:
        # The HEAD content never landed, so `exit_with_original` measures nothing: that is a
        # failed measurement, not a test that passes. The digest still proves the copy is intact.
        return _fail("revert_check", "swap_failed",
                     "could not put the HEAD content in place: %s" % "; ".join(failures),
                     "make `paths` writable first; nothing was measured and the working copy "
                     "is unchanged",
                     broken_test=False, exit_with_original=None, exit_now=exit_now,
                     restored=restored, digest=digests, expected_digest=expected, paths=mine,
                     command=command, stdout=stdout, text=text)
    if not restored:
        lost = [rel for rel in expected if digests[rel] != expected[rel]]
        return _fail("revert_check", "restore_failed",
                     "RESTORE FAILED for %s: it still holds the HEAD content, not your working "
                     "copy%s" % (", ".join(lost) or ", ".join(mine),
                                 (" (%s)" % "; ".join(failures)) if failures else ""),
                     "a human must see a failed restore: report it and stop. undo() restores the "
                     "session-start copy of the path; check diff() before you continue",
                     broken_test=broken, exit_with_original=exit_original, exit_now=exit_now,
                     restored=False, digest=digests, expected_digest=expected, paths=mine,
                     command=command, stdout=stdout, text=text)
    note = []
    if not changed:
        note.append("every path already matches HEAD, so the swap changed nothing")
    if broken:
        note.append("the test passes on the original code too, so it does not verify the fix")
    if exit_now not in (0, None) and not broken:
        note.append("the test does not pass on the working tree either (exit_now=%s)" % exit_now)
    if problem_old == "timeout":
        note.append("the command hit the %ss timeout against the original" % timeout)
    elif problem_old:
        note.append("the command did not start against the original: %s"
                    % str(err_old).strip()[:200])
    result = {"ok": True, "kind": "revert_check", "broken_test": broken,
              "exit_with_original": exit_original, "exit_now": exit_now,
              "restored": True, "digest": digests, "expected_digest": expected,
              "paths": mine, "changed": changed, "command": command,
              "timeout_s": timeout, "stdout": stdout, "text": text}
    if not failures and note:
        result["note"] = "; ".join(note)
    return result


"""
