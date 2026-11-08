"""prelude: PRELUDE, the workspace verbs and the mutation journal the model calls.

WHAT IT IS
  The source string injected into every kernel child that has a prelude. It provides the
  verbs from verbs.VERBS that need no process (map/read/session_diff/search/outline/skill/
  edit/splice/write/delete/mkdir/apply), the python helpers (guide/grep/recall/verbs/diff/
  changes/undo/commit/help/final_answer/emit), and the journal they all commit through.

  PRELUDE is one program assembled from five fragments, each its own module with its own
  header, so a change lands in the area it belongs to:
    prelude_setup.py     imports, the cell namespace, the read ledgers, path matching
    prelude_journal.py   _commit, the write-through open(), snapshot/diff/undo/changes
    prelude_index.py     _walk, map(), outline()
    prelude_read.py      read(), grep()/search(), recall()
    prelude_verbs.py     write/delete/mkdir/edit/splice/apply/commit, then `_snapshot()`

  HEADERS_SOURCE is `headers.py`'s *source*, exec'd into a private `_HEADERS` namespace so
  the model sees exactly one new verb, `guide()`.

ENTRY POINTS
  PRELUDE          the assembled child source (KERNEL_PRELUDE)
  HEADERS_SOURCE   headers.py as text (KERNEL_HEADERS); shipped, never imported, because a
                   cell runs in a task workspace where `bench` is not on sys.path
  Inside the namespace: map/read/session_diff/search/outline/skill/edit/splice/write/delete/
  mkdir/apply, guide/grep/recall/verbs/changes/diff/undo/status/commit/help, final_answer/emit.
  `_commit` is the one journal entry point behind every mutation.

INVARIANTS
  - The fragments are concatenated in the order above and must stay ONE valid program: a
    fragment may use names defined in an earlier fragment, never in a later one. The join is
    plain concatenation: nothing separates the fragments but their own trailing blank lines.
  - Never insert a separator between fragments: each one already ends with its blank lines,
    and `_snapshot()` must remain the last statement (it records the session-start tree that
    changes()/diff()/undo() compare against).
  - Every mutation goes through `_commit` (see prelude_journal.py): journal row, snapshot diff,
    `_CELL_CHANGED`. That single path is what changes()/diff()/undo() and `[state] changed:`
    read. `write()` replaces (refuses to drop a tail without force=True); `edit()` reports
    ambiguity instead of guessing; `splice()` replaces 1-based inclusive lines; `apply()`
    writes several files in one cell so one diff()/undo() covers the change set.
  - `read()` is a ledger, not a firehose (see prelude_read.py): no range returns a whole file
    only while it fits the budget, a repeated contained range is a pointer to `_OBS[n]`, and
    `repeat=True` re-delivers.
  - `guide()` is the only name the header index adds, and it is read-only. headers.py is
    exec'd into `_HEADERS` — never inlined: a shared namespace let its `_walk` shadow the
    prelude's and silently indexed every file instead of the source tree.
  - Helper results are dicts that also answer attributes, so `r["stdout"]` and `r.stdout`
    both work; the canonical keys are the contract.

WHERE TO CHANGE
  A read/search/map/outline verb .......... prelude_read.py / prelude_index.py, then the rule
                                            text in tool_loop.TOOL_RULES / repl.REPL_RULES /
                                            state_loop.STATE_RULES
  The journal or a mutation verb ........... prelude_journal.py / prelude_verbs.py
  The namespace, ledgers or path matching .. prelude_setup.py
  The assembled string ..................... this module (order and count of fragments)
  The header index hand-off ................ HEADERS_SOURCE + the `guide()` wrapper
"""
from __future__ import annotations

from pathlib import Path

from .prelude_index import _INDEX
from .prelude_journal import _JOURNAL
from .prelude_read import _READ
from .prelude_setup import _SETUP
from .prelude_verbs import _VERBS

# The header index is shipped to the child as source, not imported: a cell runs in a task
# workspace with a bare `python3 -c`, where `bench` is not importable. The source is exec'd
# into its own namespace `_HEADERS`, so its helpers can never collide with the prelude's (that
# namespace is why a private `_walk` here and one there stay two different functions), and the
# model sees exactly one new verb: guide(). The package moved one level down, so resolve the
# sibling module explicitly instead of assuming headers.py sits next to this file.
HEADERS_SOURCE = (Path(__file__).resolve().parents[1] / "headers.py").read_text(encoding="utf-8")

# One program, five areas, in dependency order. Do not reorder or join with a separator.
PRELUDE = _SETUP + _JOURNAL + _INDEX + _READ + _VERBS
