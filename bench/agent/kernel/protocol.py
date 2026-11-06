"""protocol: the one wire shape shared by the parent and the kernel child.

WHAT IT IS
  Everything both halves must agree on, and nothing that spawns anything:
    PROTOCOL, MARKER, MAX_CODE, MAX_OUTPUT, CELL_TIMEOUT  the version and the limits
    CellRequest   one request as it goes on the wire (a python cell or a typed verb call)
    CellReply     the typed body of one `@@K@@` line
    ExtendNotice  the interim `{"extend": seconds}` line a long cell sends
    Cell          what the loops consume: stdout/stderr/result/final/envelope plus the
                  journal view (changed/deltas), reads, runs, and the failed/rejected split
    _json_result, _cap  the two normalizers Cell.from_reply needs

ENTRY POINTS
  CellRequest(...).to_wire() -> dict           the one JSON line written to the child
  CellReply.from_wire(dict)                     parse a reply (the version is checked by
                                               process.Kernel._protocol_error on arrival)
  ExtendNotice.from_wire(dict)                 the deadline extension
  Cell.failure(message, ...)                   the one restart/error constructor
  Cell.from_reply(reply, duration_ms=...)      the one success constructor; normalizes a
                                               typed result into `Cell.envelope`

INVARIANTS
  - Bump PROTOCOL whenever a field changes meaning or disappears. The child embeds the same
    number through the `__PROTOCOL__` placeholder in driver.py; the parent compares it on
    the first message of a cell and restarts on a mismatch, so a stale child can never be
    read through the current shape.
  - `Cell.rejected` (a typed verb answered `{"ok": false}`) and `Cell.failed` (an exception,
    a timeout, a dead kernel) are different states; the loop counts both as errors.
  - `changed` is the list of paths, `deltas` is the per-path unified diff; `delta` is only
    the joined text the card renders. Never re-parse `delta`.
  - This module imports `envelope.normalize` and the stdlib only: no subprocess, no verbs,
    so a test can build a Cell without a child.

WHERE TO CHANGE
  The version number ............................ PROTOCOL
  A new field on the wire ...................... CellRequest/CellReply (and driver.py's
                                                 final `emit({...})`, then bump PROTOCOL)
  A new field on the Cell ...................... Cell + CellReply + Cell.from_reply
  The output cap ............................... MAX_OUTPUT, `_cap`
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..envelope import normalize

# --- the protocol version: bump when either half's shape changes ---------------------------
# The child embeds the same number through the __PROTOCOL__ placeholder; the parent compares
# it on the first message of every cell.
PROTOCOL = 4


MARKER = "@@K@@"
MAX_CODE = 20_000
MAX_OUTPUT = 0
CELL_TIMEOUT = 15.0


_RESTART_DIED = "kernel died; restarted with an empty namespace (earlier output survives in recall())"
_RESTART_DESYNC = "kernel protocol desync; restarted with an empty namespace (earlier output survives in recall())"


@dataclass
class CellRequest:
    """One request as it goes on the wire: a code cell or a typed verb call."""

    timeout: float
    code: str | None = None
    kind: str = "code"
    tool: str | None = None
    args: dict = field(default_factory=dict)
    protocol: int = PROTOCOL
    check_cache: dict = field(default_factory=dict)

    def to_wire(self) -> dict:
        """The one JSON object written as a line to the child: protocol, timeout, kind, body."""
        payload = {"protocol": self.protocol, "timeout": float(self.timeout), "kind": self.kind}
        if self.kind == "call":
            payload["tool"] = self.tool
            payload["args"] = dict(self.args or {})
        else:
            payload["code"] = self.code
        if self.check_cache:
            payload["check_cache"] = self.check_cache
        return payload


@dataclass
class CellReply:
    """The typed body of one `@@K@@` line; `protocol` must equal PROTOCOL."""

    stdout: str = ""
    stderr: str = ""
    result: str | None = None
    final: str | None = None
    answer: str | None = None
    runs: list[str] = field(default_factory=list)
    obs_index: int | None = None
    mutated: bool = False
    changed: list[str] = field(default_factory=list)
    deltas: dict[str, str] = field(default_factory=dict)
    delta: str = ""
    timeout: bool = False
    reads: dict = field(default_factory=dict)
    tool: str | None = None
    failed: bool = False
    rejected: bool = False
    protocol: int | None = None

    @classmethod
    def from_wire(cls, data: dict) -> "CellReply":
        """Parse one `@@K@@` payload, defaulting every absent field."""
        return cls(
            stdout=str(data.get("stdout") or ""),
            stderr=str(data.get("stderr") or ""),
            result=data.get("result"),
            final=data.get("final"),
            answer=data.get("answer"),
            runs=[str(item) for item in (data.get("runs") or [])],
            obs_index=data.get("obs_index"),
            mutated=bool(data.get("mutated")),
            changed=[str(item) for item in (data.get("changed") or [])],
            deltas={str(path): str(text) for path, text in (data.get("deltas") or {}).items()},
            delta=str(data.get("delta") or ""),
            timeout=bool(data.get("timeout")),
            reads=dict(data.get("reads") or {}),
            tool=data.get("tool"),
            failed=bool(data.get("failed")),
            rejected=bool(data.get("rejected")),
            protocol=data.get("protocol"),
        )


@dataclass
class ExtendNotice:
    """An interim `@@K@@` line a long cell sends before its reply."""

    seconds: float | None = None
    protocol: int | None = None

    @classmethod
    def from_wire(cls, data: dict) -> "ExtendNotice":
        """Parse the interim `{"extend": seconds}` line; seconds=None means it was not one."""
        seconds = data.get("extend")
        return cls(seconds=None if seconds is None else float(seconds),
                   protocol=data.get("protocol"))


@dataclass
class Cell:
    """What the loops consume from one finished cell: output, the journal view, reads, runs, and
    the failed/rejected split."""

    stdout: str
    stderr: str
    result: str | None
    final: str | None
    duration_ms: int
    envelope: dict | None = None
    restarted: bool = False
    answer: str | None = None
    runs: list[str] | None = None
    obs_index: int | None = None
    mutated: bool = False
    changed: list[str] = field(default_factory=list)
    deltas: dict[str, str] = field(default_factory=dict)
    delta: str = ""
    timeout: bool = False
    reads: dict = field(default_factory=dict)
    tool: str | None = None
    failed: bool = False
    rejected: bool = False

    @classmethod
    def failure(cls, message: str, *, restarted: bool = True, timeout: bool = False,
                duration_ms: int = 0) -> "Cell":
        """The one error/restart constructor: no namespace state, the message is the card."""
        return cls(stdout="", stderr=message, result=None, final=None,
                   duration_ms=duration_ms, restarted=restarted, timeout=timeout,
                   failed=True)

    @classmethod
    def from_reply(cls, reply: CellReply, *, duration_ms: int) -> "Cell":
        """The one success constructor: a validated reply becomes the loop's Cell."""
        envelope = None
        if reply.tool:
            envelope = normalize(reply.tool, _json_result(reply.result),
                                 "_OBS[%s]" % reply.obs_index if reply.obs_index is not None else None)
        return cls(
            stdout=_cap(reply.stdout),
            stderr=_cap(reply.stderr),
            result=reply.result,
            envelope=envelope,
            final=reply.final,
            duration_ms=duration_ms,
            restarted=False,
            answer=reply.answer,
            runs=list(reply.runs),
            obs_index=reply.obs_index,
            mutated=reply.mutated,
            changed=list(reply.changed),
            deltas=dict(reply.deltas),
            delta=reply.delta,
            timeout=reply.timeout,
            reads=dict(reply.reads),
            tool=reply.tool,
            failed=reply.failed,
            rejected=reply.rejected,
        )


def _json_result(result):
    if isinstance(result, dict):
        return result
    if not isinstance(result, str):
        return {}
    try:
        value = json.loads(result)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _cap(text: str, limit: int = MAX_OUTPUT) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"
