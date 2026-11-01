"""Production defaults and the loop factory used by the TUI (tui/python/worker.py).

WHAT IT IS
  The one place that decides which loop production runs and with which budgets.
  `MODE` is read from `CODE_TUI_MODE` at import (`state` by default; also `tool`, `repl`).

ENTRY POINTS
  CONTEXT_WINDOW / MAX_OUTPUT / COMPACT_AT / OBS_CHARS / HISTORY ... production constants
  budget(steps, wall_s, output=MAX_OUTPUT) -> Budget         clamped to MAX_OUTPUT
  build(client, kernel, budget, *, rules, history, obs_chars, stop, goal, goal_label,
        goal_stops, mode=MODE, health=None) -> StateLoop | ToolLoop | ReplLoop

INVARIANTS
  - `build` is the only construction site in production; the benchmark CLI constructs loops
    directly (bench/run.py) because it needs per-suite wiring.
  - Unknown `mode` falls through to ReplLoop, so a typo degrades to the simplest loop instead
    of crashing a worker.
  - Constants are TUI-facing. Benchmark defaults live in loop.Budget and run.py flags.
  - COMPACT_AT_TOKENS (85% of the 1M window) is what StateLoop compares the provider's real
    prompt token count against; OBS_CHARS=0 means the state card decides, not a char cap.
  - `health` is only wired for the state/tool loops (the goal's cheap subset); ReplLoop
    ignores it.

WHERE TO CHANGE
  Production loop selection ....... build + MODE
  Production budgets ............... budget, MAX_OUTPUT
  Compaction threshold ............. CONTEXT_WINDOW, COMPACT_AT, COMPACT_AT_TOKENS
  TUI observation/history policy ... OBS_CHARS, HISTORY (passed straight through to the loop)
"""
from __future__ import annotations

from typing import Any

from .kernel import Kernel
from .llm import Client
from .loop import Budget
from .repl import ReplLoop
from .state_loop import StateLoop
from .tool_loop import TOOL_RULES, ToolLoop

CONTEXT_WINDOW = 1_048_576
MAX_OUTPUT = 393_216
COMPACT_AT = 0.85
COMPACT_AT_TOKENS = int(CONTEXT_WINDOW * COMPACT_AT)
OBS_CHARS = 0
HISTORY = "action"
import os

MODE = os.environ.get("CODE_TUI_MODE", "state")
READ_GATE = os.environ.get("CODE_TUI_READ_GATE", "1") != "0"


def budget(steps: int, wall_s: float, output: int = MAX_OUTPUT) -> Budget:
    return Budget(max_steps=steps, max_wall_s=wall_s, max_output_tokens=min(output, MAX_OUTPUT))


def build(
    client: Client,
    kernel: Kernel,
    budget: Budget,
    *,
    rules: str,
    history: str = HISTORY,
    obs_chars: int = OBS_CHARS,
    stop: str = "call",
    goal: Any = None,
    goal_label: str = "verified",
    goal_stops: bool = False,
    mode: str = MODE,
    health: Any = None,
    read_gate: bool = READ_GATE,
):
    if mode == "state":
        return StateLoop(
            client,
            kernel,
            budget,
            rules=rules,
            goal=goal,
            goal_label=goal_label,
            goal_stops=goal_stops,
            obs_chars=obs_chars,
            health=health,
            context_budget=COMPACT_AT_TOKENS,
            read_gate=read_gate,
        )
    if mode == "tool":
        return ToolLoop(
            client,
            kernel,
            budget,
            rules=rules,
            goal=goal,
            goal_label=goal_label,
            goal_stops=goal_stops,
            obs_chars=obs_chars,
            health=health,
            read_gate=read_gate,
        )
    return ReplLoop(
        client,
        kernel,
        budget,
        rules=rules,
        stop=stop,
        history=history,
        obs_chars=obs_chars,
        goal=goal,
        goal_label=goal_label,
        goal_stops=goal_stops,
    )