"""Production defaults and the loop factory used by the TUI (tui/python/worker.py).

WHAT IT IS
  The one place that decides which loop production runs and with which budgets.
  `MODE` is read from `CODE_TUI_MODE` at import (`state` by default; also `tool`, `repl`).

ENTRY POINTS
  CONTEXT_WINDOW / MAX_OUTPUT / COMPACT_AT / OBS_CHARS / HISTORY / AUTONOMY ... constants
  budget(steps, wall_s, output=MAX_OUTPUT) -> Budget         clamped to MAX_OUTPUT
  build(client, kernel, budget, *, rules, history, obs_chars, stop, goal, goal_label,
        goal_stops, mode=MODE, health=None, verifier=None) -> StateLoop | ToolLoop | ReplLoop

INVARIANTS
  - `build` is the only construction site in production; the evals construct loops through
    `bench/evals/loops.py::run_loop` because a case wires its own levers (skeptic, subagent,
    docs, autonomy, budgets).
  - Unknown `mode` falls through to ReplLoop, so a typo degrades to the simplest loop instead
    of crashing a worker.
  - Constants are TUI-facing. Eval and test defaults live in `loop.Budget` and each case.
  - COMPACT_AT_TOKENS (85% of the 1M window) is what StateLoop compares the provider's real
    prompt token count against; OBS_CHARS=0 means the state card decides, not a char cap.
  - `verifier` (check_plan.Verifier) is the production wiring: it owns the goal, the cheap
    subset, the throttle and the cache. `goal`/`health` stay accepted for callers that have
    a bare callable. ReplLoop has no verifier slot, so `build` hands it `goal_value`.

WHERE TO CHANGE
  Production loop selection ....... build + MODE
  Production budgets ............... budget, MAX_OUTPUT
  Compaction threshold ............. CONTEXT_WINDOW, COMPACT_AT, COMPACT_AT_TOKENS
  The unattended gate .............. AUTONOMY (CODE_TUI_AUTONOMY=0 turns it off)
  TUI observation/history policy ... OBS_CHARS, HISTORY (passed straight through to the loop)
"""
from __future__ import annotations

import os
from typing import Any

from .kernel import Kernel
from .llm import Client
from .loop import Budget
from .repl import ReplLoop
from .state_loop import StateLoop
from .tool_loop import ToolLoop

CONTEXT_WINDOW = 1_048_576
MAX_OUTPUT = 393_216
COMPACT_AT = 0.85
COMPACT_AT_TOKENS = int(CONTEXT_WINDOW * COMPACT_AT)
OBS_CHARS = 0
CARD_BUDGET = 6_000
HISTORY = "action"

MODE = os.environ.get("CODE_TUI_MODE", "state")
# Fire-and-forget is the production contract: unattended, no human answer, no same-session
# repair. The gate is on by default; a flag can turn it off for an interactive experiment.
AUTONOMY = os.environ.get("CODE_TUI_AUTONOMY", "1") != "0"


def budget(steps: int, wall_s: float, output: int = MAX_OUTPUT) -> Budget:
    """The production caps: `output` is clamped to MAX_OUTPUT, never raised past it."""
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
    verifier: Any = None,
    autonomy: bool = AUTONOMY,
    subagent: Any = None,
    skeptic: Any = None,
    extra_tools: list[dict] | None = None,
    docs: Any = None,
):
    """The one production loop factory: `mode` picks StateLoop | ToolLoop | ReplLoop."""
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
            verifier=verifier,
            context_budget=COMPACT_AT_TOKENS,
            card_budget=CARD_BUDGET,
            autonomy=autonomy,
            subagent=subagent,
            skeptic=skeptic,
            extra_tools=extra_tools,
            docs=docs,
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
            verifier=verifier,
            autonomy=autonomy,
            subagent=subagent,
            skeptic=skeptic,
            extra_tools=extra_tools,
            docs=docs,
        )
    return ReplLoop(
        client,
        kernel,
        budget,
        rules=rules,
        stop=stop,
        history=history,
        obs_chars=obs_chars,
        goal=goal if goal is not None else (verifier.goal_value if verifier is not None else None),
        goal_label=goal_label,
        goal_stops=goal_stops,
    )
