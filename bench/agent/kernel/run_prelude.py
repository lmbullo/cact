"""run_prelude: RUN_PRELUDE, the one process tool + the mutation and spawn gates.

WHAT IT IS
  Source string injected into a child that may spawn programs: exec_argv(), git_tool(), serve(), run() (python-only modes),
  and the classifier that refuses a mutation bypassing the journal. Assembled here from raw-source fragments in dependency
  order, so the child still execs ONE string.

ENTRY POINTS
  RUN_PRELUDE = _SETUP + _NOTES + _GATE + _SHELL + _EXEC + _GIT + _SERVE + _SERVE_CTL (KERNEL_RUN_PRELUDE), present only
  when allow_run is on; namespace: exec_argv(), git_tool(), run() (KERNEL_SHELL=1 only), serve(); gates: _mutating_command,
  _HINT, _VERB_DUPE, _audit_spawn, _SpawnBlocked.
INVARIANTS
  fragment order is fixed, one valid program: a fragment may use names from an earlier fragment, never a later one.
  exec never parses a shell string: a shell with -c/-lc in argv -> shell_via_exec -> exec(shell=<string>); a bare string
  argv runs once through _run_shell with a note. Tracked-file mutation -> kind:"wrong_call" + use hint: it bypasses the
  journal, so diff()/undo() would lie; allowed: git add/commit/push/fetch/init/clone and an absolute target under /tmp,
  /var/tmp, /dev/shm, $TMPDIR. Direct spawn -> the sys.addaudithook gate in run_prelude_gate.py; a mutating spawn raises
  _SpawnBlocked -> driver `wrong_call`; each program: NEW session, leader journalled to KERNEL_ORPHAN_JOURNAL.
  a dupe of a typed verb runs normally + ONE note (_VERB_DUPE, silent for _DUPE_OPAQUE flags); a plan command or git
  status carries one note naming checks()/status() (silent when not in the plan; never refuses).
WHERE TO CHANGE
  imports/state/tables ... run_prelude_setup.py   notes ... run_prelude_notes.py   mutation/spawn gate ... run_prelude_gate.py
  shell ... run_prelude_shell.py   argv ... run_prelude_exec.py   git ... run_prelude_git.py
  serve ... run_prelude_serve.py / run_prelude_serve_ctl.py   order and count ... this module
"""
from __future__ import annotations

from .run_prelude_exec import _EXEC
from .run_prelude_gate import _GATE
from .run_prelude_git import _GIT
from .run_prelude_notes import _NOTES
from .run_prelude_serve import _SERVE
from .run_prelude_serve_ctl import _SERVE_CTL
from .run_prelude_setup import _SETUP
from .run_prelude_shell import _SHELL

# One program, eight areas, in dependency order. Do not reorder or join with a separator.
RUN_PRELUDE = _SETUP + _NOTES + _GATE + _SHELL + _EXEC + _GIT + _SERVE + _SERVE_CTL
