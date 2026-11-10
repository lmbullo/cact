"""run_prelude_setup: child imports and shared state — RUN_PRELUDE fragment 1/8.

WHAT IT IS
  Raw child source `_SETUP`, the first fragment of RUN_PRELUDE: the child's imports (`_rhash`, `_rio`,
  `_json`, `_ros`, `_re`, `_rshutil`, `_rsig`, `_rsp`, `_rtime`), _RUN_ROOT, the orphan journal, the
  per-run logs (_RUN_LOG, _CHECK_CACHE, _GATE_IN_RUN) and the gate tables and `_SpawnBlocked`.
  run_prelude.py concatenates it first; nothing may precede the child's imports.

ENTRY POINTS
  _SETUP   source text; inside: _proc_start, _journal_orphan, _SpawnBlocked, _RUN_SHELL_MUTATORS,
           _RUN_GIT_MUTATORS, _HINT, _VERB_DUPE, _DUPE_OPAQUE, _CHECK_SPECS, _SHELL_SPLIT

INVARIANTS
  fragment, not an API: later fragments read these names, never the reverse, and the child still execs ONE string.
  the mutator sets are the refusal policy: a shell verb that overwrites tracked content -> wrong_call
  + a use hint, because it bypasses the journal, so diff()/undo() would lie.

WHERE TO CHANGE
  refusal policy ... _RUN_SHELL_MUTATORS, _RUN_GIT_MUTATORS, _HINT   dupe flags ... _VERB_DUPE, _DUPE_OPAQUE
"""
_SETUP = r"""
import hashlib as _rhash, io as _rio, json as _json, os as _ros, re as _re, shutil as _rshutil
import signal as _rsig, subprocess as _rsp, time as _rtime
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
# Programs a typed verb already answers. One `note` on the result, never a refusal: the call
# worked, and the model should know the cheaper shape for next time.
_VERB_DUPE = {
    "grep": "search(pattern, path=[...], max_results=0)",
    "rg": "search(pattern, path=[...], max_results=0)",
    "cat": "read(path)",
    "ls": "map(path)",
    "find": "map(path, include='*.py')",
    "sed": "splice(path, start, end, text) or edit(path, old, new)",
    "mkdir": "mkdir(path)",
    "rm": "delete(path)",
}
# Short flags the typed verb cannot express (`grep -o`, `grep -v`, `find -exec`). With one of
# these the note stays silent: naming a call that would return something else is a wrong hint.
_DUPE_OPAQUE = {"grep": "olvABCPLxqmFzZ", "rg": "olvABCPLxqmFzZ",
                "find": "execoknewprmfa1", "ls": "1l", "cat": "AnbeEsTtv", "sed": "ns"}
_DUPE_NOTED = set()
# The plan the worker was started with: [{name, cmd}]. The kernel never runs it; it is only
# there to name the typed checks() call when a model reaches for a gate command through exec.
_CHECK_SPECS = []
try:
    _CHECK_SPECS = _json.loads(_ros.environ.get("KERNEL_CHECK_COMMANDS") or "[]")
except Exception:
    _CHECK_SPECS = []
_CHECK_NOTED = set()
_STATUS_NOTED = set()
# A plan command is the same program with the same arguments. A file argument or `-t name`
# is a narrowing: it runs less than the plan, so naming checks() there would be a wrong hint.
_SHELL_SPLIT = _re.compile(r"\s*(?:&&|\|\||;|\|)\s*")


"""
