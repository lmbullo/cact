"""limits: the capability set and the output meter the harness grants one run.

WHAT IT IS
  `Scope` is the model's action space, not a suggestion in a prompt: which typed verbs a run
  may call, whether it may spawn programs, how many tool calls one turn may carry, the
  per-turn output ceiling, and the final-report ceiling. `ToolLoop` filters the provider tools
  from it and refuses a call outside it, so the model cannot reach a capability the task did
  not grant. `TokenMeter` counts what the model actually emitted (completion, reasoning,
  content, tool arguments) per turn and per run, because output tokens are the cost the
  prompt cannot see.

ENTRY POINTS
  Scope(tools=None, allow_exec=True, max_calls_per_turn=8, max_turn_tokens=8192,
        max_report_chars=4000, effort="low", effort_policy="fixed",
        inline_finish=False)
    .allows(name) -> bool             the one capability check the loop uses
    .filter_tools(specs) -> list      the provider specs this scope permits
    .turn_tokens(budget_output) -> int  the per-turn max_tokens
    .clip_report(text) -> str         the final-report ceiling
  TokenMeter().add(usage, content, tool_calls) -> dict   one per-turn row
    .as_dict() -> dict                 the run totals plus reasoning_share
  effort_for(scope, surprise) -> str | None   fixed effort, or low only on a surprise

INVARIANTS
  - The scope is enforced twice: provider tools are filtered before the model sees them, and
    `_dispatch` refuses a name the model still invents. Filtering alone is not a limit.
  - `allow_exec=False` removes `exec` even when `tools` names it; the process gate is not a
    substitute for a capability gate.
  - `max_turn_tokens=0` means "use the run budget", never "unlimited": the provider still
    receives a finite max_tokens. Default 8192 is a ceiling, not a target.
  - `effort_for` returns `None` only for the adaptive policy on a non-surprising turn; the
    client omits `reasoning_effort` then, which is the provider's cheapest setting. A fixed
    policy always returns `scope.effort`.
  - `inline_finish=True` is the production one-call finish: a silent `final_answer` runs the
    goal check in the same turn and accepts immediately when it passes. The default is False
    so the staged protocol and its tests stay the benchmark contract.
  - `TokenMeter` reads provider usage; it never estimates. A missing field is 0, so an
    offline fake still produces a row.

WHERE TO CHANGE
  The default action space ............... Scope.tools, Scope.allow_exec
  The per-turn output ceiling ............ Scope.max_turn_tokens, config.scope
  The reasoning policy ................... Scope.effort_policy, effort_for
  What the meter records ................. TokenMeter.add, as_dict
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .verbs import TOOL_NAMES

# `serve` is operational (it can stop the server hosting this worker), not a coding
# capability. A task that genuinely needs it grants it through CODE_TUI_ALLOWED_TOOLS.
DEFAULT_TOOLS = (frozenset(TOOL_NAMES) - {"serve"}) | {"code", "delegate", "docs", "plan", "task"}


@dataclass(frozen=True)
class Scope:
    """The capabilities one run grants the model; the loop filters and refuses from it."""

    tools: frozenset[str] = DEFAULT_TOOLS
    allow_exec: bool = True
    max_calls_per_turn: int = 8
    max_turn_tokens: int = 8192
    max_report_chars: int = 4000
    effort: str | None = "low"
    effort_policy: str = "fixed"
    inline_finish: bool = False

    def allows(self, name: str) -> bool:
        """True when this scope grants `name`; `exec` also needs `allow_exec`."""
        if name == "python":
            name = "code"
        if name == "exec" and not self.allow_exec:
            return False
        return name in self.tools

    def filter_tools(self, specs: list[dict]) -> list[dict]:
        """The provider specs whose function name this scope grants, in the same order."""
        return [spec for spec in specs if self.allows(spec["function"]["name"])]

    def turn_tokens(self, budget_output: int) -> int:
        """The max_tokens for one turn: the scope ceiling, else the run's finite output cap."""
        return int(self.max_turn_tokens or budget_output)

    def clip_report(self, text: str) -> str:
        """Clip the final report to `max_report_chars`, naming the cut instead of hiding it."""
        cap = int(self.max_report_chars or 0)
        if cap <= 0 or len(text) <= cap:
            return text
        return text[:cap] + "\n...[report clipped at %d chars; the structured handoff is the full record]" % cap


def effort_for(scope: Scope, surprise: bool) -> str | None:
    """The reasoning effort for the next turn: fixed, or low only when the last turn surprised."""
    if scope.effort_policy != "adaptive":
        return scope.effort
    return "low" if surprise else None


@dataclass
class TokenMeter:
    """Per-turn and per-run output accounting from the provider's real usage rows."""

    turns: int = 0
    completion: int = 0
    reasoning: int = 0
    content_chars: int = 0
    arg_chars: int = 0
    last: dict = field(default_factory=dict)

    def add(self, usage: dict, content: str, tool_calls: list[dict]) -> dict:
        """Add one turn's usage and emitted text; return that turn's row."""
        row = _row(usage, content, tool_calls)
        self.turns += 1
        self.completion += row["completion"]
        self.reasoning += row["reasoning"]
        self.content_chars += row["content_chars"]
        self.arg_chars += row["arg_chars"]
        self.last = row
        return row

    def as_dict(self) -> dict:
        """The run totals, including the reasoning share of completion tokens."""
        share = round(self.reasoning / self.completion, 3) if self.completion else 0.0
        return {
            "turns": self.turns,
            "completion": self.completion,
            "reasoning": self.reasoning,
            "reasoning_share": share,
            "content_chars": self.content_chars,
            "arg_chars": self.arg_chars,
        }


def _row(usage: dict, content: str, tool_calls: list[dict]) -> dict:
    """One usage row: completion, reasoning, emitted content chars, emitted argument chars."""
    details = usage.get("completion_tokens_details") or {}
    args = 0
    for call in tool_calls or ():
        fn = call.get("function") or {}
        value = fn.get("arguments")
        args += len(value if isinstance(value, str) else json.dumps(value or {}))
    return {
        "completion": int(usage.get("completion_tokens") or 0),
        "reasoning": int(details.get("reasoning_tokens") or usage.get("reasoning_tokens") or 0),
        "content_chars": len(content or ""),
        "arg_chars": args,
    }
