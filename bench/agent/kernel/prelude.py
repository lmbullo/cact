"""prelude: PRELUDE, the workspace verbs + mutation journal the model calls.

WHAT IT IS
  Source string injected into every kernel child with a prelude. Provides the process-free
  verbs.VERBS verbs (map/read/session_diff/search/outline/skill/edit/splice/write/delete/
  mkdir/apply), the python helpers (guide/grep/recall/verbs/diff/changes/undo/commit/help/
  final_answer/emit), and the journal they all commit through.
  PRELUDE = one program assembled from five fragments; one change, one module:
    prelude_setup.py     imports, cell namespace, read ledgers, path matching
    prelude_journal.py   _commit, write-through open(), snapshot/diff/undo/changes
    prelude_index.py     _walk, map(), outline()
    prelude_read.py      read(), grep()/search(), recall()
    prelude_verbs.py     write/delete/mkdir/edit/splice/apply/commit, then _snapshot()
  HEADERS_SOURCE = headers.py source, exec'd into a private _HEADERS namespace: the model sees
  exactly one new verb, guide().

ENTRY POINTS
  PRELUDE          assembled child source (KERNEL_PRELUDE)
  HEADERS_SOURCE   headers.py as text (KERNEL_HEADERS); shipped, never imported: a cell runs in
                   a task workspace where `bench` is not on sys.path
  namespace: map/read/session_diff/search/outline/skill/edit/splice/write/delete/mkdir/apply,
  guide/grep/recall/verbs/changes/diff/undo/status/commit/help, final_answer/emit.
  _commit = the one journal entry point behind every mutation.

INVARIANTS
  fragment order = fixed, one valid program: a fragment may use names from an earlier fragment,
  never a later one. Join = plain concatenation, separated only by each fragment's trailing
  blank lines.
  no separator between fragments; _snapshot() stays the last statement (records the session-start
  tree that changes()/diff()/undo() compare against).
  every mutation -> _commit (prelude_journal.py): journal row, snapshot diff, _CELL_CHANGED. That
  single path feeds changes()/diff()/undo() and `chg:`. write() replaces and refuses to drop a
  tail without force=True; edit() reports ambiguity instead of guessing; splice() replaces
  1-based inclusive lines; apply() writes several files in one cell so one diff()/undo() covers
  the change set.
  read() = ledger, not firehose (prelude_read.py): no range -> whole file only while it fits the
  budget; repeated contained range -> pointer to _OBS[n]; repeat=True re-delivers.
  guide() = only name the header index adds, read-only. headers.py exec'd into _HEADERS, never
  inlined: a shared namespace once let its _walk shadow the prelude's and index every file.
  helper results = dicts that also answer attributes: r["stdout"] and r.stdout both work; the
  canonical keys are the contract.

WHERE TO CHANGE
  read/search/map/outline verb ..... prelude_read.py / prelude_index.py, then its verbs.py
                                     summary (provider description); TOOL_RULES/STATE_RULES
                                     stay policy, not a catalog
  journal / mutation verb .......... prelude_journal.py / prelude_verbs.py
  namespace, ledgers, path match ... prelude_setup.py
  assembled string ................. this module (order and count of fragments)
  header index hand-off ............ HEADERS_SOURCE + the guide() wrapper
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
