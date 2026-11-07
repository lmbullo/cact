"""kernel: the agent's action space and the subprocess that enforces it (package form).

WHAT IT IS
  The action space, split by lifecycle so a change lands in one small file:

    protocol.py      PROTOCOL, MARKER and the wire shapes: CellRequest, CellReply,
                     ExtendNotice, and `Cell` (the value every loop consumes)
    driver.py        DRIVER: the source the child runs. One JSON request per line in ->
                     exec() a python cell or dispatch one typed verb in the surviving
                     namespace -> one `@@K@@` JSON line out.
    prelude.py       HEADERS_SOURCE + the assembled PRELUDE string; the fragments below are
                     its five areas, joined in order (setup -> journal -> index -> read ->
                     verbs) into the one program the child execs:
                     prelude_setup.py    namespace, read ledgers, path matching
                     prelude_journal.py  _commit, the write-through open(), snapshot/diff/undo
                     prelude_index.py    _walk, map(), outline()
                     prelude_read.py     read(), grep()/search(), recall()
                     prelude_verbs.py    write/delete/mkdir/edit/splice/apply/commit
    run_prelude.py   RUN_PRELUDE: exec (argv/commands/shell), git, run() where allowed, the
                     mutation gate, the shell_via_exec refusal and the spawn gate.
    process.py       Kernel: spawn (plain or bwrap), supervise the per-cell deadline,
                     restart on crash/hang/desync, reap exec/run process groups, and turn
                     one reply into one `Cell`.

  This file is the import surface only. It holds no logic, so there is exactly one place
  for each fact: `from .kernel import Kernel, Cell` is the only way the loops import it.

ENTRY POINTS
  from .kernel import Kernel, Cell, PROTOCOL      # the surface the loops and run.py use
  Kernel(root, timeout=15.0, writable=False, prelude="off"|"all"|"mut",
         allow_run=False, allow_shell=None, supervise=True)
    .start() / .run(code, check_cache=None) / .call(tool, args, check_cache=None) / .stop()
  Re-exported constants: PROTOCOL, MARKER, MAX_CODE, MAX_OUTPUT, CELL_TIMEOUT
  Re-exported sources:   DRIVER, PRELUDE, RUN_PRELUDE, HEADERS_SOURCE
  Re-exported shapes:    CellRequest, CellReply, ExtendNotice, Cell

INVARIANTS
  - The names and defaults re-exported here are the package's public surface. Moving a
    symbol between submodules must not change what an importer sees; nothing outside the
    package imports a submodule directly.
  - Import direction is acyclic and one-way:
    protocol <- driver <- prelude <- run_prelude, and process imports all four.
    A submodule always imports its siblings (`from .protocol import PROTOCOL`), never this
    package, so importing one piece for a test does not drag in the subprocess machinery.
  - Every submodule carries the four header sections; `python3 bench/map.py --check` gates
    them and `guide(prelude|driver|protocol|kernel)` reaches the contract without reading a
    1600-line source string.
  - The split is layout, not behaviour: PROTOCOL stays the single version number, and the
    child still receives DRIVER/PRELUDE/RUN_PRELUDE/HEADERS_SOURCE as source strings.

WHERE TO CHANGE
  The protocol version or a wire field ......... protocol.py (PROTOCOL, CellRequest,
                                                 CellReply, Cell, `_cap`, `_json_result`)
  How a cell runs in the child ................. driver.py (DRIVER)
  A read/search/map/file verb or helper ........ prelude_read.py / prelude_index.py (then the
                                                 matching rule text in tool_loop.TOOL_RULES /
                                                 repl.REPL_RULES / state_loop.STATE_RULES)
  exec / git / run, the mutation and spawn gate  run_prelude.py (RUN_PRELUDE)
  Timeouts, restarts, orphan reaping, bwrap ..... process.py (Kernel)
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
