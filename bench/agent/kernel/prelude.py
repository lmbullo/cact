"""prelude: PRELUDE, the workspace verbs + mutation journal the model calls.

WHAT IT IS
  Source string injected into every kernel child with a prelude: the process-free verbs.VERBS verbs (map/read/
  session_diff/search/outline/skill/edit/splice/write/delete/mkdir/apply), the python helpers (guide/grep/recall/verbs/
  diff/changes/undo/commit/help/final_answer/emit) and the journal they all commit through. HEADERS_SOURCE adds guide().

ENTRY POINTS
  PRELUDE = assembled child source (KERNEL_PRELUDE), five fragments in order: prelude_setup.py (namespace, read ledgers,
  path matching), prelude_journal.py (_commit, write-through open(), snapshot/diff/undo/changes), prelude_index.py (_walk,
  map, outline), prelude_read.py (read, grep/search, recall), prelude_verbs.py (write/delete/mkdir/edit/splice/apply/commit).
  HEADERS_SOURCE = headers.py as text (KERNEL_HEADERS), exec'd into _HEADERS where `bench` is not importable; _commit = the one journal entry.

INVARIANTS
  fragment order is fixed, one valid program: a fragment may use names from an earlier fragment, never a later one.
  no separator between fragments; _snapshot() stays the last statement (the session-start tree changes()/diff()/undo() use).
  every mutation -> _commit: journal row, snapshot diff, _CELL_CHANGED, one path for changes()/diff()/undo()/`chg:`.
  write() refuses to drop a tail without force=True; edit() reports ambiguity; splice() is 1-based inclusive; apply() is one undo unit.
  read() = ledger: whole file only while it fits the budget, a repeated range -> _OBS[n], repeat=True re-delivers; helper results
  are dicts that also answer attributes (r["stdout"] == r.stdout); guide() is read-only and the only verb the index adds.
WHERE TO CHANGE
  read/search/map/outline ... prelude_read.py / prelude_index.py, then the verbs.py summary (TOOL_RULES/STATE_RULES stay policy)
  journal/mutation ... prelude_journal.py / prelude_verbs.py   namespace/ledgers/path match ... prelude_setup.py
  assembled string ... this module (order and count)   header hand-off ... HEADERS_SOURCE + the guide() wrapper
"""
from __future__ import annotations

from pathlib import Path

from .prelude_index import _INDEX
from .prelude_journal import _JOURNAL
from .prelude_read import _READ
from .prelude_setup import _SETUP
from .prelude_verbs import _VERBS

# Header index shipped as source, not imported: a cell runs in a task workspace with bare
# `python3 -c`, where `bench` is not importable. Exec'd into its own namespace _HEADERS, so its
# helpers never collide with the prelude's (private _walk stays two functions) and the model
# sees exactly one new verb: guide(). Resolve the sibling module, not a same-dir assumption.
HEADERS_SOURCE = (Path(__file__).resolve().parents[1] / "headers.py").read_text(encoding="utf-8")

# One program, five areas, in dependency order. Do not reorder or join with a separator.
PRELUDE = _SETUP + _JOURNAL + _INDEX + _READ + _VERBS
