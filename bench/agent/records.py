"""records: the small dataclasses one turn machine passes around.

WHAT IT IS
  Four per-run values owned by tool_loop.ToolLoop: FinishState (staged/accepted answer),
  CallRecord (one tool call), TurnOutcome (what a turn decided), Tally (counters). Independent of
  the loop's imports: a test can construct them without a client or kernel.

ENTRY POINTS
  FinishState()  .stage(v) / .accept(v) / .clear() / .staged / .terminal
  CallRecord(index, name, call_id)  .as_dict()
  TurnOutcome(stop, final, end)
  Tally()  steps, calls, errors, repeats, llm_calls

INVARIANTS
  FinishState.accepted = the one terminal predicate; staged is not terminal.
  CallRecord.as_dict = the trace shape; a field here reaches every trace.
  data only: no client, kernel, file or clock.

WHERE TO CHANGE
  finish protocol ........ FinishState
  trace record shape ..... CallRecord.as_dict
  turn outcome/counters .. TurnOutcome, Tally
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FinishState:
    """The staged-then-accepted answer of one run.

    `staged` is set by `final_answer`. If the cell also printed, the answer is held while
    the model reads the output. If the cell was silent, it is still held: the next assistant
    message is the handoff, so a model that calls `final_answer("short")` and then writes the
    real report cannot lose the report. The next reply with no tool call accepts it
    (`accept`), which sets `terminal`; a silent stage prefers that reply's content over the
    tool's short value. A non-final cell in a *later* turn clears it; a later call in the same
    turn does not. A no-tool reply whose goal check rejects the finish clears it too.
    """

    staged: str | None = None
    terminal: str | None = None
    staged_turn: int | None = None
    silent: bool = False
    content: str | None = None
    handoff: dict = field(default_factory=dict)

    @property
    def accepted(self) -> bool:
        """True once the finish was accepted (`terminal` set); the turn ends there."""
        return self.terminal is not None

    def stage(self, answer: Any, turn: int | None = None, *, silent: bool = False,
              content: str | None = None, handoff: dict | None = None) -> None:
        """Hold a `final_answer` value until a later reply accepts it; records the staging turn."""
        self.staged = answer
        self.staged_turn = turn
        self.silent = silent
        self.content = content
        self.handoff = dict(handoff or {})

    def clear(self, turn: int | None = None) -> None:
        """Drop the staged answer, unless `turn` staged it earlier in this same turn."""
        if turn is not None and self.staged is not None and self.staged_turn == turn:
            return
        self.staged = None
        self.staged_turn = None
        self.silent = False
        self.content = None
        self.handoff = {}

    def accept(self, answer: str) -> None:
        """Promote the staged answer to `terminal`: the turn is over and `answer` is final."""
        self.staged = None
        self.staged_turn = None
        self.silent = False
        self.content = None
        self.terminal = answer


@dataclass
class CallRecord:
    """One tool call of one turn: what ran, what came back, and how it ended."""

    index: int
    name: str
    call_id: str
    code: str | None = None
    args: dict | None = None
    observation: dict | None = None
    runs: list = field(default_factory=list)
    reads: dict | None = None
    notice: str | None = None
    stop: str | None = None
    obs: str | None = None
    check: dict | None = None
    skipped: bool = False

    def as_dict(self) -> dict:
        """The record as it goes into a trace file and `run.turns`."""
        out = {
            "index": self.index,
            "name": self.name,
            "call_id": self.call_id,
            "code": self.code,
            "args": self.args,
            "observation": self.observation,
            "runs": list(self.runs or []),
            "reads": self.reads or {},
            "notice": self.notice,
            "stop": self.stop,
            "obs": self.obs,
            "check": self.check,
        }
        if self.skipped:
            out["skipped"] = True
        return out


@dataclass
class TurnOutcome:
    """What one turn decided. `end` means the run stops with `stop` and `final`."""

    stop: str | None = None
    final: str | None = None
    end: bool = False


@dataclass
class Tally:
    """The run counters, mutated in place by the turn machine."""

    steps: int = 0
    calls: int = 0
    errors: int = 0
    repeats: int = 0
    llm_calls: int = 0
