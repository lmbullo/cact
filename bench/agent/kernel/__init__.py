"""kernel: the action space and the subprocess that enforces it (package form).

WHAT IT IS
  Action space split by lifecycle; one change lands in one file:
    protocol.py      PROTOCOL, MARKER, wire shapes: CellRequest, CellReply, ExtendNotice,
                     Cell (the value every loop consumes)
    driver.py        DRIVER: child source. One JSON request per line in -> exec() a python
                     cell or dispatch one typed verb in the surviving namespace -> one `@@K@@`
                     JSON line out.
    prelude.py       HEADERS_SOURCE + assembled PRELUDE. Fragments joined in order
                     setup -> journal -> index -> read -> verbs, one program the child execs:
                     prelude_setup.py    namespace, read ledgers, path matching
                     prelude_journal.py  _commit, write-through open(), snapshot/diff/undo
                     prelude_index.py    _walk, map(), outline()
                     prelude_read.py     read(), grep()/search(), recall()
                     prelude_verbs.py    write/delete/mkdir/edit/splice/apply/commit
    run_prelude.py   RUN_PRELUDE: exec (argv/commands/shell), git, run() where allowed, the
                     mutation gate, shell_via_exec refusal, spawn gate.
    process.py       Kernel: spawn (plain or bwrap), per-cell deadline, restart on crash/hang/
                     desync, reap exec/run process groups, reply -> Cell.
  This file = import surface only, no logic. `from .kernel import Kernel, Cell` is the only
  import path the loops use.

ENTRY POINTS
  from .kernel import Kernel, Cell, PROTOCOL      # surface for the loops and run.py
  Kernel(root, timeout=15.0, writable=False, prelude="off"|"all"|"mut",
         allow_run=False, allow_shell=None, supervise=True)
    .start() / .run(code, check_cache=None) / .call(tool, args, check_cache=None) / .stop()
  re-exported constants: PROTOCOL, MARKER, MAX_CODE, MAX_OUTPUT, CELL_TIMEOUT
  re-exported sources:   DRIVER, PRELUDE, RUN_PRELUDE, HEADERS_SOURCE
  re-exported shapes:    CellRequest, CellReply, ExtendNotice, Cell

INVARIANTS
  names/defaults re-exported here = package public surface. Moving a symbol between submodules
  must not change what an importer sees; nothing outside the package imports a submodule directly.
  import direction = acyclic, one-way: protocol <- driver <- prelude <- run_prelude; process
  imports all four. A submodule imports siblings (`from .protocol import PROTOCOL`), never this
  package, so one piece is testable without the subprocess machinery.
  every submodule carries the four header sections; `python3 bench/guide.py --check` gates them.
  split = layout only: PROTOCOL stays the single version number; the child still receives
  DRIVER/PRELUDE/RUN_PRELUDE/HEADERS_SOURCE as source strings.

WHERE TO CHANGE
  protocol version / wire field .... protocol.py (PROTOCOL, CellRequest, CellReply, Cell,
                                     _cap, _json_result)
  cell execution in the child ...... driver.py (DRIVER)
  read/search/map/file verb ........ prelude_read.py / prelude_index.py, then rule text in
                                     tool_loop.TOOL_RULES / repl.REPL_RULES / state_loop.STATE_RULES
  exec / git / run / mutation+spawn gate ... run_prelude.py (RUN_PRELUDE)
  timeouts, restarts, orphan reaping, bwrap  process.py (Kernel)
"""
from __future__ import annotations

from .driver import DRIVER
from .prelude import HEADERS_SOURCE, PRELUDE
from .process import Kernel
from .protocol import (
    CELL_TIMEOUT,
    MARKER,
    MAX_CODE,
    MAX_OUTPUT,
    PROTOCOL,
    Cell,
    CellReply,
    CellRequest,
    ExtendNotice,
)
from .run_prelude import RUN_PRELUDE

__all__ = [
    "PROTOCOL", "MARKER", "MAX_CODE", "MAX_OUTPUT", "CELL_TIMEOUT",
    "DRIVER", "PRELUDE", "RUN_PRELUDE", "HEADERS_SOURCE",
    "CellRequest", "CellReply", "ExtendNotice", "Cell", "Kernel",
]
