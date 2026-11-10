"""loop: shared loop types: Budget, RunResult, usage accounting.
WHAT IT IS
  The vocabulary every loop returns; no loop lives here (tool_loop, state_loop, repl).
ENTRY POINTS
  Budget(max_steps, max_output_tokens, max_wall_s, max_total_tokens)
  RunResult    what run.py, worker.py and trace.py consume
  _usage_add(acc, usage)    sums provider usage incl. cache hit/miss
INVARIANTS
  shared types only; benchmark defaults are overridden by eval flags and config.budget.
  caps: max_steps, max_wall_s, max_output_tokens, max_total_tokens (0 = unlimited); every cap is
  read by a loop, a field no loop reads is not Budget.
  _usage_add additive; tolerates missing provider fields incl. cache hits.
  RunResult.error = provider/loop failure detail for a stop with no final answer (LLM_FAILED
  first); empty string = no separate detail.
  RunResult.steps = turns = primary client.complete calls; calls = tool calls / kernel cells;
  llm_calls = every model request incl. budget-forced _push, so llm_calls >= steps.
  RunResult.economy = limits.TokenMeter totals (completion, reasoning, emitted content/arguments):
  what the model wrote, never an estimate; attestation = durable state at stop (plan, changed
  paths, checks, evidence, open items), evidence not a claim.
WHERE TO CHANGE
  new stop/usage field ..... RunResult
  bench budget defaults .... Budget (production overrides via config.budget)
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Budget:
    """The caps every loop enforces: turns, output tokens, wall-clock seconds and total tokens."""

    max_steps: int = 200
    max_output_tokens: int = 393216
    max_wall_s: float = 3600.0
    max_total_tokens: int = 0


@dataclass
class RunResult:
    """One finished run. `steps` are turns, `calls` are tool calls, and `llm_calls` is
    every client request (including the budget-forced `_push`), so `llm_calls >= steps`."""

    task: str
    stop: str
    final: str
    steps: int
    calls: int
    errors: int
    repeats: int
    usage: dict
    wall_ms: float
    trace: str | None
    turns: list[dict] = field(default_factory=list)
    workspace: str = ""
    llm_calls: int = 0
    error: str = ""
    handoff: dict = field(default_factory=dict)
    economy: dict = field(default_factory=dict)
    attestation: dict = field(default_factory=dict)


def _usage_add(acc: dict, usage: dict) -> None:
    """Add one provider usage dict into `acc` in place, tolerating missing fields."""
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        acc[key] = acc.get(key, 0) + int(usage.get(key) or 0)
    details = usage.get("completion_tokens_details") or {}
    acc["reasoning_tokens"] = acc.get("reasoning_tokens", 0) + int(details.get("reasoning_tokens") or 0)
    for key in ("prompt_cache_hit_tokens", "prompt_cache_miss_tokens"):
        acc[key] = acc.get(key, 0) + int(usage.get(key) or 0)
    acc["local_hits"] = acc.get("local_hits", 0) + (1 if usage.get("local_cache") else 0)
