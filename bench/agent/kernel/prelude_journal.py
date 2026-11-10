"""prelude_journal: the mutation journal, snapshot/diff/undo -- PRELUDE fragment 2/5.

WHAT IT IS
  Fragment 2/5 of PRELUDE (see prelude.py), split into source modules this file concatenates in
  dependency order; the assembled _JOURNAL is byte-identical to the old single constant and the
  child still execs one PRELUDE string.
    prelude_journal_core.py  _HINT, _Writer, journalled open(), _resolve, _read_text, _commit
    prelude_snapshot.py      _BASE/_LAST_STATE, _snapshot, _tree_state, _row, _changed, _delta
    prelude_diff.py, prelude_status.py, prelude_undo.py  session_diff/diff_tool, status(), undo()
  Only this module names the order; prelude.py imports _JOURNAL from it.

ENTRY POINTS
  _JOURNAL   the complete fragment (core + snapshot + diff + status + undo);  _commit   the one
  journal entry point behind every mutation.

INVARIANTS
  fragment order = execution order: _commit records into the snapshot state defined next, and
  the views read both; do not reorder or add a separator (bytes must match the pre-split constant); every mutation -> _commit: journal row, snapshot diff, _CELL_CHANGED.

WHERE TO CHANGE
  journal / write-through open .. prelude_journal_core.py;  snapshot and delta ..
  prelude_snapshot.py;  diff / status .. prelude_diff.py, prelude_status.py;  changes / undo ..
  prelude_undo.py;  fragment order .. this module
"""
from __future__ import annotations

from .prelude_diff import _DIFF
from .prelude_journal_core import _JOURNAL_CORE
from .prelude_snapshot import _SNAPSHOT
from .prelude_status import _STATUS
from .prelude_undo import _UNDO

# One program fragment, order fixed by the kernel prelude. Do not join with a separator.
_JOURNAL = _JOURNAL_CORE + _SNAPSHOT + _DIFF + _STATUS + _UNDO
