"""process_cell: one request through the child, reply -> Cell — the Cell pipeline half.

WHAT IT IS
  `CellMixin`: `run(code)`, `call(tool, args)` and `_execute(request)`, plus `_protocol_error`.
  Both public verbs build one CellRequest and share the same write/select/timeout/reply path, so
  a python cell and a typed verb share journal, coverage and replay. process.py inherits it into
  `Kernel`.

ENTRY POINTS
  CellMixin.run(code, check_cache=None, extra_obs=None) -> Cell
  CellMixin.call(tool, args=None, check_cache=None, extra_obs=None) -> Cell
  CellMixin._execute(request)  wire write, @@K@@/ExtendNotice read, deadline, protocol check

INVARIANTS
  cell over timeout, dead child, or non-@@K@@ line -> kill + restart with an EMPTY namespace;
  never pretend state survived. First message of a cell is compared with PROTOCOL; a mismatch
  restarts and returns an error Cell, never a misread stale reply. run() refuses code over
  MAX_CODE before it reaches the child.

WHERE TO CHANGE
  request construction ... run, call
  deadline/restart/reply ... _execute, _protocol_error
"""
from __future__ import annotations

import json
import selectors
import time

from .protocol import (
    MARKER,
    MAX_CODE,
    PROTOCOL,
    Cell,
    CellReply,
    CellRequest,
    ExtendNotice,
    _RESTART_DESYNC,
    _RESTART_DIED,
)


class CellMixin:
    """Run one request through the child and turn its reply into a Cell; one half of Kernel."""

    @staticmethod
    def _protocol_error(message: dict) -> str | None:
        """None when the child speaks this parent's protocol, else the restart message."""
        got = message.get("protocol")
        if got == PROTOCOL:
            return None
        return (f"kernel protocol mismatch: child speaks {got!r}, parent expects {PROTOCOL}; "
                "restarted with an empty namespace (earlier output survives in recall())")

    def run(self, code: str, check_cache: dict | None = None,
            extra_obs: list | None = None) -> Cell:
        """One python cell -> one Cell; restarts the child on a timeout, a crash or a stale protocol."""
        if len(code) > MAX_CODE:
            return Cell.failure("kernel: code exceeds 20000 characters; split it into smaller cells",
                                restarted=False)
        return self._execute(CellRequest(code=code, timeout=float(self.timeout),
                                         check_cache=dict(check_cache or {}),
                                         extra_obs=list(extra_obs or [])))

    def call(self, tool: str, args: dict | None = None, check_cache: dict | None = None,
             extra_obs: list | None = None) -> Cell:
        """Execute one typed verb through the same child, journal and Cell pipeline."""
        return self._execute(CellRequest(kind="call", tool=str(tool), args=dict(args or {}),
                                         timeout=float(self.timeout),
                                         check_cache=dict(check_cache or {}),
                                         extra_obs=list(extra_obs or [])))

    def _execute(self, request: CellRequest) -> Cell:
        if self.proc is None:
            self.start()
        assert self.proc is not None and self.proc.stdin and self.proc.stdout

        started = time.perf_counter()
        try:
            self.proc.stdin.write(json.dumps(request.to_wire()) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            self._restart()
            return Cell.failure(_RESTART_DIED)

        sel = selectors.DefaultSelector()
        sel.register(self.proc.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + self.timeout + 5.0
        payload = None
        checked = False
        try:
            while payload is None:
                remain = deadline - time.monotonic()
                if remain <= 0:
                    self._kill_group()
                    self._restart()
                    return Cell.failure(
                        f"kernel did not answer within {self.timeout:.0f}s; killed and restarted with an empty namespace (earlier output survives in recall())",
                        timeout=True,
                        duration_ms=int((time.perf_counter() - started) * 1000),
                    )
                if not sel.select(remain):
                    continue
                line = self.proc.stdout.readline()
                if not line:
                    self._restart()
                    return Cell.failure(_RESTART_DIED)
                if not line.startswith(MARKER):
                    self._restart()
                    return Cell.failure(_RESTART_DESYNC)
                message = json.loads(line[len(MARKER):])
                if not checked:
                    checked = True
                    error = self._protocol_error(message)
                    if error is not None:
                        self._restart()
                        return Cell.failure(error)
                notice = ExtendNotice.from_wire(message)
                if notice.seconds is not None:
                    deadline = time.monotonic() + notice.seconds + 30.0
                    continue
                payload = message
        finally:
            sel.close()

        reply = CellReply.from_wire(payload)
        return Cell.from_reply(reply, duration_ms=int((time.perf_counter() - started) * 1000))
