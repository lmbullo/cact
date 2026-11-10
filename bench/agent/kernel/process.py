"""process: Kernel, parent-side subprocess supervisor + its one Cell pipeline.
WHAT IT IS
  Kernel spawns the child (plain `python3 -u -c DRIVER`, or under bwrap with read-only `/`),
  supervises the per-cell deadline, restarts on death/hang/desync, reaps exec()/run() process
  groups, returns a Cell. .run(code) and .call(tool, args) are two requests through one _execute
  pipeline (shared journal, coverage, replay). __init__ stays here as the import anchor.

ENTRY POINTS
  Kernel(root, timeout=15.0, writable=False, prelude="off"|"all"|"mut", allow_run=False,
         allow_shell=None, supervise=True, extra_verbs=None, extra_prelude="",
         check_commands=None, env=None)
    .start() / .run(code, check_cache=None, extra_obs=None) / .call(tool, args, ...) / .stop()
  process_spawn.py SpawnMixin: _argv, start, _stop, stop, _kill_group, _restart
  process_reap.py  ReapMixin: _start_id, _reap_orphans
  process_cell.py  CellMixin: _protocol_error, run, call, _execute

INVARIANTS
  names/defaults re-exported by kernel/__init__.py are the package surface; mixins are
  implementation, not imported by callers. Wire env, deadline, restart and reply shapes are
  unchanged; each mixin's header states the half it owns.

WHERE TO CHANGE
  spawn / bwrap / env ... process_spawn.py;  orphan reaping ... process_reap.py
  run/call/_execute ..... process_cell.py;   construction ...... this module (Kernel.__init__)
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .process_cell import CellMixin
from .process_reap import ReapMixin
from .process_spawn import SpawnMixin
from .protocol import CELL_TIMEOUT


class Kernel(SpawnMixin, ReapMixin, CellMixin):
    """The parent-side supervisor of one kernel child.

    Spawns the child (bubblewrapped unless `allow_run`), sends one request line per cell, parses
    the `@@K@@` reply into a `Cell`, enforces the per-cell deadline, restarts a dead or stale
    child, and reaps the process groups its children started. `writable` decides whether the
    file verbs may commit; `prelude` selects which helpers the child has.
    """

    def __init__(self, root: Path, timeout: float = CELL_TIMEOUT, writable: bool = False,
                 prelude: str = "off", allow_run: bool = False, allow_shell: bool | None = None,
                 supervise: bool = True, extra_verbs: dict | None = None,
                 extra_prelude: str = "", check_commands: list | None = None,
                 env: dict | None = None) -> None:
        self.root = Path(root).resolve()
        self.timeout = timeout
        self.writable = writable
        self.prelude = prelude
        self.allow_run = allow_run
        self.allow_shell = bool(allow_run if allow_shell is None else (allow_shell and allow_run))
        self.supervise = supervise
        self.extra_verbs = dict(extra_verbs or {})
        self.extra_prelude = str(extra_prelude or "")
        self.check_commands = list(check_commands or [])
        self.env = {str(key): str(value) for key, value in (env or {}).items()}
        self.proc: subprocess.Popen | None = None
        self.orphan_journal: str | None = None
        self.obs_log: str | None = None
        self._python = shutil.which("python3") or "/usr/bin/python3"
