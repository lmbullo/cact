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
COMPACT_AT = 0.8
OBS_CHARS = 0
HISTORY = "action"
import os

MODE = os.environ.get("CODE_TUI_MODE", "state")


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
