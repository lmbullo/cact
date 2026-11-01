"""Shared loop types: `Budget`, `RunResult` and usage accounting.

WHAT IT IS
  The vocabulary every loop returns. No loop lives here anymore: `tool_loop.ToolLoop` is the
  production turn machine, `state_loop.StateLoop` extends it with state cards, and
  `repl.ReplLoop` is the fenced-block alternate. The former JSON tool-calling AgentLoop and
  its `tools.py` registry were removed: they had no production path and duplicated the
  kernel's file semantics.

ENTRY POINTS
  Budget(steps, tool_calls, output, wall, no_progress)
  RunResult ... what run.py, worker.py and trace.py consume
  _usage_add(acc, usage) ... sums provider usage, including cache hit/miss

WHERE TO CHANGE
  New stop/usage field ....... RunResult
  Budget defaults (bench) .... Budget (production overrides via config.budget)

INVARIANTS
  - This module holds shared types only. There is no loop here: ToolLoop is the turn
    machine, StateLoop adds state cards, ReplLoop is the fenced-block alternate.
  - The benchmark defaults below are overridden by run.py flags and by config.budget for
    production; do not tune production from here.
  - `_usage_add` is additive and tolerates missing provider fields, including cache hits.
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
