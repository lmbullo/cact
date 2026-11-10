"""kernel: the action space and the subprocess that enforces it (package form).

WHAT IT IS
  Action space split by lifecycle; one change lands in one file:
    protocol.py PROTOCOL/MARKER/wire shapes (CellRequest, CellReply, ExtendNotice, Cell); driver.py DRIVER (one JSON request
    per line -> exec() a python cell or dispatch one typed verb -> one @@K@@ line); prelude.py HEADERS_SOURCE + PRELUDE;
    run_prelude.py RUN_PRELUDE (exec/git/run, mutation + spawn gate); process.py Kernel (spawn, deadline, restart, reap).

ENTRY POINTS
  from .kernel import Kernel, Cell, PROTOCOL      # surface for the loops and run.py
  Kernel(root, timeout=15.0, writable=False, prelude="off"|"all"|"mut",
         allow_run=False, allow_shell=None, supervise=True)
    .start() / .run(code, check_cache=None) / .call(tool, args, check_cache=None) / .stop()
  re-exported: PROTOCOL, MARKER, MAX_CODE, MAX_OUTPUT, CELL_TIMEOUT; DRIVER, PRELUDE, RUN_PRELUDE, HEADERS_SOURCE;
  CellRequest, CellReply, ExtendNotice, Cell.
INVARIANTS
  names/defaults re-exported here = public surface; moving a symbol must not change what an importer sees, nothing outside imports a submodule directly.
  import direction acyclic: protocol <- driver <- prelude <- run_prelude, process imports all four; a submodule imports siblings, never this package.
  every submodule carries the four header sections; python3 bench/guide.py --check gates them.
  split = layout only: PROTOCOL stays the single version number; the child still receives the four sources as strings.
WHERE TO CHANGE
  protocol version/wire field ... protocol.py (PROTOCOL, CellRequest, CellReply, Cell, _cap, _json_result)   cell execution ... driver.py
  read/search/map/file verb ... prelude_read.py / prelude_index.py, then TOOL_RULES / REPL_RULES / STATE_RULES   exec/git/run/gates ... run_prelude.py + run_prelude_*.py fragments
  timeouts/restarts/reaping/bwrap ... process.py (Kernel)
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
