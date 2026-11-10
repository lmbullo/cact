"""process_reap: identity-checked orphan reaping — the exec()/run() group cleanup half.

WHAT IT IS
  `ReapMixin`: `_start_id` reads a pid's /proc start id and `_reap_orphans` kills the process
  groups the dead/stopped child journaled, checking identity first. process.py inherits it into
  `Kernel`; `SpawnMixin._stop` and `start` call it.

ENTRY POINTS
  ReapMixin._start_id(pid) / ._reap_orphans()

INVARIANTS
  exec()/run() start each program in its own session, so _kill_group cannot reach them; the child
  journals their group leader. Reaping is best-effort and compares pid + /proc start id before
  SIGKILL, so a reused pid is not killed by mistake. The journal file is removed after a pass.

WHERE TO CHANGE
  pid identity ............ _start_id
  orphan kill and unlink .. _reap_orphans
"""
from __future__ import annotations

import json
import os
import signal
from pathlib import Path


class ReapMixin:
    """Kill identity-checked orphan process groups left by exec()/run(); one half of Kernel."""

    def _start_id(self, pid: int) -> str | None:
        try:
            data = Path(f"/proc/{pid}/stat").read_text()
            return data.rsplit(")", 1)[1].split()[19]
        except OSError:
            return None

    def _reap_orphans(self) -> None:
        """Kill exec()/run() process groups the dead/stopped kernel left behind.

        `exec()`/`run()` start each program in its own session, so `_kill_group()` (the kernel's
        group) never reaches it. The child journals its group leader; reaping is best-effort
        and identity-checked so a reused pid is not killed by mistake.
        """
        path = self.orphan_journal
        self.orphan_journal = None
        if not path:
            return
        seen: set[int] = set()
        try:
            lines = Path(path).read_text().splitlines()
        except OSError:
            lines = []
        for line in lines:
            try:
                record = json.loads(line)
                pid = int(record.get("pid") or 0)
            except (ValueError, TypeError):
                continue
            if pid <= 0 or pid in seen:
                continue
            seen.add(pid)
            start = record.get("start")
            if start and self._start_id(pid) not in (None, start):
                continue
            try:
                os.killpg(pid, signal.SIGKILL)
            except OSError:
                pass
        try:
            os.unlink(path)
        except OSError:
            pass
