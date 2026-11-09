"""subagent: a delegated, read-only agent with its own context and kernel.

WHAT IT IS
  The `delegate` tool: parent sends one scoped question; a fresh child loop explores in a
  separate kernel and returns a short answer + its own token count. The parent's context never
  sees the child's reads: exploration is what makes a long run expensive; delegation makes it one
  observation. Subagent builds the child; DELEGATE_TOOL = provider spec; Skeptic (skeptic.py) is
  a second use of the same machinery (a reviewer that must falsify a finish).

ENTRY POINTS
  DELEGATE_TOOL                     provider tool spec the parent exposes
  Subagent(client, budget, workspace, *, rules=None, tools=None)
    .run(task, prompt) -> SubagentResult   answer, stop, steps, usage, error
  SubagentResult.as_text()          the observation the parent receives

INVARIANTS
  child read-only: Kernel(writable=False) under bwrap; tool list filtered to python +
  read/search/map/outline/recall. No mutation, no exec, no nested delegate: no recursion, no
  bypass of the parent's journal.
  child own budget (max_steps, max_total_tokens, max_wall_s) + own Tally. Usage returned, never
  dropped: the parent adds it to the run's usage.
  child failure = normal observation (ok: false), never an exception into the parent; the parent
  can retry with a narrower prompt.
  stop = child's loop stop name. answer = child's final, or its last message when it hit a
  budget: a partial finding stays useful.

WHERE TO CHANGE
  child permissions ...... _child_tools, _RULES
  child budgets .......... Subagent.__init__ defaults
  observation shape ...... SubagentResult.as_text
  delegate schema ........ DELEGATE_TOOL
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .kernel import Kernel
from .loop import Budget
from .tools import TYPE_TOOLS
from .verbs import VERBS

DELEGATE_TOOL = {
    "type": "function",
    "function": {
        "name": "delegate",
        "description": (
            "Send one scoped question to a read-only subagent with a fresh context and its "
            "own kernel. Use it for broad exploration ('where is X handled', 'which files "
            "reference Y') so the parent context gets one summary instead of many reads. "
            "The child can read, search, map and outline but cannot edit or run programs. "
            "Ask a specific question; a vague task wastes a whole child run."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "A short label for the child run."},
                "prompt": {"type": "string",
                           "description": "The question to answer, with the scope and the expected shape of the answer."},
            },
            "required": ["prompt"],
            "additionalProperties": False,
        },
    },
}

_RULES = """You are a read-only explorer inside a larger coding agent. Answer one scoped question from the workspace. Use map, read, search and outline; do not edit, run programs, or ask questions. Return a compact answer: the files and line ranges that matter, the fact that settles the question, and any uncertainty. Your reply is the only thing the parent sees, so it must stand alone."""

_READ_ONLY = ("read", "search", "map", "outline", "recall", "guide", "skill")


@dataclass
class SubagentResult:
    """One child run: its answer, its stop reason, its counters and its usage."""

    answer: str
    stop: str
    steps: int
    usage: dict = field(default_factory=dict)
    error: str = ""
    task: str = ""

    @property
    def ok(self) -> bool:
        """True when the child produced an answer without a loop-level failure."""
        return bool(self.answer) and self.stop not in ("LLM_FAILED",)

    def as_text(self) -> str:
        """The observation the parent receives: counters first, then the child's answer."""
        tokens = int(self.usage.get("total_tokens") or 0)
        head = f"[subagent] stop={self.stop} steps={self.steps} tokens={tokens}"
        body = self.answer or ("(no answer) " + (self.error or ""))
        return head + "\n" + body


class Subagent:
    """A read-only child agent: one question, one fresh kernel, one compact answer."""

    def __init__(self, client, workspace: str | Path, *,
                 max_steps: int = 10, max_total_tokens: int = 80_000,
                 max_wall_s: float = 180.0, rules: str | None = None,
                 tools: list[dict] | None = None) -> None:
        self.client = client
        self.workspace = Path(workspace).resolve()
        self.max_steps = max_steps
        self.max_total_tokens = max_total_tokens
        self.max_wall_s = max_wall_s
        self.rules = rules or _RULES
        self.tools = tools

    def _child_tools(self) -> list[dict]:
        if self.tools is not None:
            return self.tools
        from .tool_loop import PYTHON_TOOL

        picked = [spec for spec in TYPE_TOOLS
                  if spec["function"]["name"] in _READ_ONLY
                  and not VERBS[spec["function"]["name"]].get("requires_run")]
        return [PYTHON_TOOL, *picked]

    def run(self, task: str, prompt: str) -> SubagentResult:
        """Run the child to a stop and return its answer and usage; never raises a child error."""
        from .tool_loop import ToolLoop

        kernel = Kernel(self.workspace, writable=False, prelude="all", allow_run=False)
        budget = Budget(max_steps=self.max_steps, max_total_tokens=self.max_total_tokens,
                        max_wall_s=self.max_wall_s, max_output_tokens=16_384)
        loop = ToolLoop(self.client, kernel, budget, rules=self.rules,
                        tools=self._child_tools(), goal=None, goal_stops=False)
        try:
            result = loop.run(task or "delegate", prompt, None)
        except Exception as exc:  # noqa: BLE001 - a child failure is an observation
            return SubagentResult(answer="", stop="ERROR", steps=0,
                                  error=f"{type(exc).__name__}: {exc}", task=task)
        finally:
            kernel.stop()
        answer = (result.final or "").strip()
        if not answer:
            # A budget stop has no accepted final; the last real content is still the finding.
            for turn in reversed(result.turns):
                text = (turn.get("content") or "").strip()
                if text:
                    answer = text
                    break
        return SubagentResult(answer=answer, stop=result.stop, steps=result.steps,
                              usage=dict(result.usage or {}), error=result.error,
                              task=task)
