"""verbs: the verb registry: every typed action, its JSON schema, its one-line contract.

WHAT IT IS
  Single source of truth for the typed tool layer: VERBS[name] = description, JSON schema, mutates flag, prelude function
  (`function`, default name), typed flag vs python-only helper. tools.py builds provider specs from the typed entries;
  kernel/process.py ships the dict to the child as KERNEL_VERBS; rows live in four family tables merged in fixed order.

ENTRY POINTS
  VERBS          name -> {function, summary, schema, mutates, typed?, requires_run?};  TOOL_NAMES = typed tool names
                 (no `python`: that is the escape hatch); look up VERBS[name] directly, no accessor layer.
  verbs_read.py guide/skill/map/read/session_diff/checks/serve/revert_check/status;  verbs_search.py search/recall/page/outline;
  verbs_mutate.py edit/splice/write/delete/mkdir/apply;  verbs_exec.py exec/run/git.

INVARIANTS
  data only: no stdlib import, no bench code, no filesystem; the JSON ships to the child, so each family module stays serializable.
  schema = contract, the real signature = authority: the driver binds with inspect.signature().bind, so a mismatch is
  bad_args/bad_arguments, never a traceback; help() prints each live signature. Fix the schema, never loosen the check.
  mutates=True = the function writes the workspace and must go through _commit; the kernel journal stays the authority.
  typed=False (run) stays in the registry for help() but is never a provider tool; `python` is deliberately absent.
  merge order = registry order (read, search, mutate, exec); the provider tool list and help() follow it.

WHERE TO CHANGE
  a verb's entry ... its family table   arguments ... the family-table schema + the PRELUDE signature   model text ... summary
  a new typed action ... family table + kernel/prelude.py function + a Kernel.call case in bench/smoke_checks/verbs.py
"""
from __future__ import annotations

from .verbs_exec import VERBS_EXEC
from .verbs_mutate import VERBS_MUTATE
from .verbs_read import VERBS_READ
from .verbs_search import VERBS_SEARCH

VERBS: dict[str, dict] = {
    **VERBS_READ,
    **VERBS_SEARCH,
    **VERBS_MUTATE,
    **VERBS_EXEC,
}

TOOL_NAMES = tuple(name for name, meta in VERBS.items() if meta.get("typed", True))
