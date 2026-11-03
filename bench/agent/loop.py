"""Shared loop types: `Budget`, `RunResult` and usage accounting.

WHAT IT IS
  The vocabulary every loop returns. No loop lives here anymore: `tool_loop.ToolLoop` is the
  production turn machine, `state_loop.StateLoop` extends it with state cards, and
  `repl.ReplLoop` extends it with fenced-block reply parsing. The former JSON tool-calling
  AgentLoop and its `tools.py` registry were removed: they had no production path and
  duplicated the kernel's file semantics.

ENTRY POINTS
  Budget(max_steps, max_tool_calls, max_output_tokens, max_wall_s, no_progress_limit)
  RunResult ... what run.py, worker.py and trace.py consume
  _usage_add(acc, usage) ... sums provider usage, including cache hit/miss

INVARIANTS
  - This module holds shared types only. There is no loop here: ToolLoop is the turn
    machine, StateLoop adds state cards, ReplLoop is the fenced-block alternate.
  - The benchmark defaults below are overridden by run.py flags and by config.budget for
    production; do not tune production from here. `max_tool_calls` and `no_progress_limit`
    are carried for compatibility but no loop enforces them: the live caps are
    `max_steps`, `max_wall_s` and `max_output_tokens`.
  - `_usage_add` is additive and tolerates missing provider fields, including cache hits.
  - `RunResult.steps` is the number of turns, i.e. primary `client.complete` calls. `calls`
    is the number of tool calls / kernel cells, a different axis. `llm_calls` is every model
    request the loop made, including the budget-forced `_push`, so `llm_calls >= steps`.

WHERE TO CHANGE
  New stop/usage field ....... RunResult
  Budget defaults (bench) .... Budget (production overrides via config.budget)
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Budget:
    max_steps: int = 200
    max_tool_calls: int = 500
    max_output_tokens: int = 393216
    max_wall_s: float = 3600.0
    no_progress_limit: int = 3

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
    misses: int
    repeats: int
    usage: dict
    wall_ms: float
    trace: str | None
    turns: list[dict] = field(default_factory=list)
    workspace: str = ""
    llm_calls: int = 0

def _usage_add(acc: dict, usage: dict) -> None:
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        acc[key] = acc.get(key, 0) + int(usage.get(key) or 0)
    details = usage.get("completion_tokens_details") or {}
    acc["reasoning_tokens"] = acc.get("reasoning_tokens", 0) + int(details.get("reasoning_tokens") or 0)
    for key in ("prompt_cache_hit_tokens", "prompt_cache_miss_tokens"):
        acc[key] = acc.get(key, 0) + int(usage.get(key) or 0)
    acc["local_hits"] = acc.get("local_hits", 0) + (1 if usage.get("local_cache") else 0)
