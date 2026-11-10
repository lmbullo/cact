"""revert: revert_check answers "does this test fail without the fix?" and stays safe.

WHAT IT IS
  One temp git repo, its own kernel child, five cases:
    a fix that fails on HEAD     -> broken_test false, restored true, the file is the fixed one
    a test that passes on HEAD   -> broken_test true (it verifies nothing about the change)
    a dirty unrelated path       -> refusal naming it, and nothing is swapped
    an untracked path            -> refusal, `untracked` (HEAD has no version to swap in)
    a restore that cannot write  -> restored false and a message naming the path

ENTRY POINTS
  run(check)   called by bench/smoke.py::main after gates; builds and drops its own repo

INVARIANTS
  writes only under tempfile.mkdtemp; git init/commit run through -c user.email / -c user.name, so
  it needs no global config and cannot touch a real repo.
  the two versions of calc.py differ in LENGTH: a bytecode cache validates on size and mtime, so two
  same-size versions swapped inside one second would let a stale .pyc answer.
  every case asserts the bytes on disk after the call; restored is the claim, the file is the proof.
  the read-only case runs last and lifts its own chmod.

WHERE TO CHANGE
  swap/restore contract .. kernel/prelude_verbs.py::revert_check (_revert_run, _revert_git, _revert_dirty, _revert_digest);  refusal codes .. untracked, dirty_tree, restore_failed
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from bench.smoke_support import kernel_for

BUGGY = "def add(a, b):\n    return a - b  # the bug that is committed\n"
FIXED = "def add(a, b):\n    return a + b\n"
TEST = 'import calc\n\nassert calc.add(2, 3) == 5, calc.add(2, 3)\nprint("calc ok")\n'


def _git(root, *args):
    """One git command in the temp repo, with the throwaway identity `_repo` commits under."""
    return subprocess.run(
        ["git", "-c", "user.email=smoke@example.invalid", "-c", "user.name=smoke",
         "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main"] + list(args),
        cwd=str(root), capture_output=True, text=True)


def _repo():
    """A temp repo whose HEAD holds the bug: the working tree carries the fix and its test."""
    root = Path(tempfile.mkdtemp(prefix="cact-revert-"))
    (root / "calc.py").write_text(BUGGY, encoding="utf-8")
    (root / "notes.txt").write_text("clean\n", encoding="utf-8")
    _git(root, "init")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "calc: the bug")
    (root / "test_calc.py").write_text(TEST, encoding="utf-8")
    (root / "calc.py").write_text(FIXED, encoding="utf-8")
    _git(root, "add", "test_calc.py")
    _git(root, "commit", "-m", "calc: the test")
    return root


def _data(cell):
    """The structured half of one typed reply: what an agent reads, not the printout."""
    return (cell.envelope or {}).get("data") or {}


def _error(cell):
    """The structured refusal of one typed reply, or {}."""
    return (cell.envelope or {}).get("error") or {}


def run(check):
    """The four smoke cases plus the untracked refusal, over a fresh temp git repo."""
    root = _repo()
    try:
        with kernel_for(root) as kernel:
            cmd = [sys.executable, "test_calc.py"]

            cell = kernel.call("revert_check", {"paths": ["calc.py"], "cmd": cmd})
            data = _data(cell)
            check("revert: a test that fails on HEAD passes now -> broken_test false",
                  data.get("broken_test") is False and data.get("restored") is True
                  and data.get("exit_now") == 0 and data.get("exit_with_original") == 1
                  and (root / "calc.py").read_text(encoding="utf-8") == FIXED,
                  "%s | %r" % (data.get("text"), (root / "calc.py").read_text()[:40]))
            check("revert: the restore is verified by digest and reported",
                  data.get("digest", {}).get("calc.py") is not None
                  and data.get("digest") == data.get("expected_digest"),
                  str(data.get("digest"))[:200])

            cell = kernel.call("revert_check",
                               {"paths": ["calc.py"],
                                "cmd": [sys.executable, "-c", "import calc"]})
            data = _data(cell)
            check("revert: a test that passes on HEAD is broken_test true",
                  data.get("broken_test") is True and data.get("restored") is True
                  and data.get("exit_with_original") == 0
                  and (root / "calc.py").read_text(encoding="utf-8") == FIXED,
                  "%s | %r" % (data.get("text"), (root / "calc.py").read_text()[:40]))

            (root / "notes.txt").write_text("dirty\n", encoding="utf-8")
            cell = kernel.call("revert_check", {"paths": ["calc.py"], "cmd": cmd})
            error = _error(cell)
            check("revert: a dirty path outside `paths` is a refusal that names it",
                  cell.rejected and error.get("code") == "dirty_tree"
                  and "notes.txt" in str(error.get("message"))
                  and (root / "calc.py").read_text(encoding="utf-8") == FIXED,
                  str(error)[:240])
            (root / "notes.txt").write_text("clean\n", encoding="utf-8")

            (root / "extra.py").write_text("x = 1\n", encoding="utf-8")
            cell = kernel.call("revert_check", {"paths": ["extra.py"], "cmd": cmd})
            error = _error(cell)
            check("revert: an untracked path is refused (HEAD has no version of it)",
                  cell.rejected and error.get("code") == "untracked", str(error)[:240])
            (root / "extra.py").unlink()

            # The restore is the step under test, so the lock has to land AFTER the swap: the
            # script locks the path only once it already holds the HEAD content, which is the
            # second run. (Locking it on the first run would break the swap instead.)
            locked = [sys.executable, "-c",
                      "import os\n"
                      "src = open('calc.py').read()\n"
                      "if 'a - b' in src:\n"
                      "    os.chmod('calc.py', 0o444)\n"
                      "print('locked' if 'a - b' in src else 'open')"]
            cell = kernel.call("revert_check", {"paths": ["calc.py"], "cmd": locked})
            data = _data(cell)
            error = _error(cell)
            check("revert: a restore that cannot write is restored false, naming the path",
                  data.get("restored") is False and error.get("code") == "restore_failed"
                  and "RESTORE FAILED" in str(error.get("message"))
                  and "calc.py" in str(error.get("message")),
                  str(error)[:240])
            check("revert: a failed restore leaves the HEAD content, and says so",
                  (root / "calc.py").read_text(encoding="utf-8") == BUGGY,
                  (root / "calc.py").read_text()[:60])
            os.chmod(root / "calc.py", 0o644)
            (root / "calc.py").write_text(FIXED, encoding="utf-8")
    finally:
        os.chmod(root / "calc.py", 0o644)
