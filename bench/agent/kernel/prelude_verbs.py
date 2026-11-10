"""prelude_verbs: write/edit/splice/apply/commit/revert and the start-up snapshot — 5/5.
WHAT IT IS
  Fragment 5/5 of PRELUDE (prelude.py); concatenates its sub-fragments in dependency order into
  `_VERBS`, byte-identical to the old single constant, exec'd by the child as one PRELUDE string.
  prelude_verbs_intro.py verbs()/help, checks(), write-path constants; prelude_mutate.py write(),
  delete(), mkdir(); prelude_edit.py _closest_lines/_replace_text/_edit_context, edit();
  prelude_lines.py edit_many(), splice(); prelude_patch.py unified-diff parser, apply();
  prelude_commit.py commit(); prelude_revert.py revert_check() helpers; prelude_revert_check.py
  revert_check(). `_snapshot()` stays here and must be the assembled PRELUDE's last statement.

ENTRY POINTS
  _VERBS   intro + mutate + edit + lines + patch + commit + revert + revert_check, then _snapshot().

INVARIANTS
  fragment order = execution order: later verbs call _commit and helpers defined earlier; do not
  reorder or add a separator, bytes must match, and _snapshot() stays last.

WHERE TO CHANGE
  basic file verbs ......... prelude_mutate.py;  exact-text edit ... prelude_edit.py
  line-addressed edits ..... prelude_lines.py;  patch / commit ..... prelude_patch.py / prelude_commit.py
  revert_check ............. prelude_revert_check.py;  fragment order/tail ... this module
"""
from __future__ import annotations

from .prelude_commit import _COMMIT
from .prelude_edit import _EDIT
from .prelude_lines import _LINES
from .prelude_mutate import _MUTATE
from .prelude_patch import _PATCH
from .prelude_revert import _REVERT
from .prelude_revert_check import _REVERT_CHECK
from .prelude_verbs_intro import _VERBS_INTRO

# One program fragment, order fixed by the kernel prelude; _snapshot() must remain the final
# statement of the assembled PRELUDE. Do not join the fragments with a separator.
_VERBS = (_VERBS_INTRO + _MUTATE + _EDIT + _LINES + _PATCH + _COMMIT + _REVERT
          + _REVERT_CHECK + "_snapshot()\n")
