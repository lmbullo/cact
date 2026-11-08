"""Production defaults and the loop factory used by the TUI (tui/python/worker.py).

WHAT IT IS
  The one place that decides which loop production runs and with which budgets.
  `MODE` is read from `CODE_TUI_MODE` at import (`state` by default; also `tool`, `repl`).

ENTRY POINTS
  CONTEXT_WINDOW / MAX_OUTPUT / COMPACT_AT / OBS_CHARS / HISTORY / AUTONOMY ... constants
  PROFILE / PROFILES / CODE_MODE / EFFORT / EFFORT_POLICY / INLINE_FINISH ... the chosen arm
  budget(steps, wall_s, output=MAX_OUTPUT) -> Budget         clamped to MAX_OUTPUT
  production_scope() -> Scope     the production capability set and output ceiling
  build(client, kernel, budget, *, rules, history, obs_chars, stop, goal, goal_label,
        goal_stops, mode=MODE, health=None, verifier=None, scope=None)
    -> StateLoop | ToolLoop | ReplLoop

INVARIANTS
  - `build` is the only construction site in production; the evals construct loops through
    `bench/evals/loops.py::run_loop` because a case wires its own levers (skeptic, subagent,
    docs, autonomy, budgets).
  - Unknown `mode` falls through to ReplLoop, so a typo degrades to the simplest loop instead
    of crashing a worker.
  - Constants are TUI-facing. Eval and test defaults live in `loop.Budget` and each case.
  - `PROFILE` selects a measured arm. `m3-plan` is the default (python cell, fixed low
    effort, staged finish); `current` is the same with the inline finish. The individual
    CODE_TUI_* flags override the profile, so a run is reproducible without a new build.
  - `scope()` is the harness's capability limit, not a prompt: the loop filters the provider
    tools from it and refuses a call outside it. `CODE_TUI_ALLOWED_TOOLS` narrows the set;
    `CODE_TUI_ALLOW_EXEC=0` removes exec; `CODE_TUI_MAX_CALLS_PER_TURN` and
    `CODE_TUI_MAX_TURN_TOKENS` cap one turn; `CODE_TUI_EFFORT_POLICY=adaptive` spends low
    reasoning only after a surprise. ReplLoop has no scope slot.
  - COMPACT_AT_TOKENS (85% of the 1M window) is what StateLoop compares the provider's real
    prompt token count against; OBS_CHARS=0 means the state card decides, not a char cap.
  - `verifier` (check_plan.Verifier) is the production wiring: it owns the goal, the cheap
    subset, the throttle and the cache. `goal`/`health` stay accepted for callers that have
    a bare callable. ReplLoop has no verifier slot, so `build` hands it `goal_value`.

WHERE TO CHANGE
  Production loop selection ....... build + MODE
  Production budgets ............... budget, MAX_OUTPUT
  Production capabilities .......... production_scope() and its constants; agent/limits.py owns Scope
  The measured arm ................ PROFILE / PROFILES; per-knob CODE_TUI_* flags override it
  The orchestration cell .............. CODE_MODE (profile m3-plan keeps the python cell)
  Compaction threshold ............. CONTEXT_WINDOW, COMPACT_AT, COMPACT_AT_TOKENS
  The unattended gate .............. AUTONOMY (CODE_TUI_AUTONOMY=0 turns it off)
  TUI observation/history policy ... OBS_CHARS, HISTORY (passed straight through to the loop)
"""
from __future__ import annotations

import os
from typing import Any

from .kernel import Kernel
from .limits import DEFAULT_TOOLS, Scope
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
# The measured best arm: python cell (not code mode), fixed low effort, the environment card
# and durable plan, and the staged finish. `current` opts into the later inline finish; the
# individual env flags override either profile.
PROFILE = os.environ.get("CODE_TUI_PROFILE", "m3-plan")
PROFILES = {
    "m3-plan": {"code_mode": False, "effort": "low", "effort_policy": "fixed",
                "inline_finish": False, "task_tool": False},
    "current": {"code_mode": False, "effort": "low", "effort_policy": "fixed",
                "inline_finish": True, "task_tool": True},
}
_ACTIVE = PROFILES.get(PROFILE, PROFILES["m3-plan"])
CODE_MODE = os.environ.get("CODE_TUI_CODE_MODE",
                           "1" if _ACTIVE["code_mode"] else "0") == "1"
# Fire-and-forget is the production contract: unattended, no human answer, no same-session
# repair. The gate is on by default; a flag can turn it off for an interactive experiment.
AUTONOMY = os.environ.get("CODE_TUI_AUTONOMY", "1") != "0"

# The production capability set. These are ceilings, not targets: a turn may use less. The
# adaptive effort policy is opt-in until a live run proves the provider's omitted-effort
# default is not more expensive than `low`.
MAX_CALLS_PER_TURN = int(os.environ.get("CODE_TUI_MAX_CALLS_PER_TURN", "8"))
MAX_TURN_TOKENS = int(os.environ.get("CODE_TUI_MAX_TURN_TOKENS", "8192"))
MAX_REPORT_CHARS = int(os.environ.get("CODE_TUI_MAX_REPORT_CHARS", "4000"))
EFFORT = os.environ.get("CODE_TUI_EFFORT", _ACTIVE["effort"])
EFFORT_POLICY = os.environ.get("CODE_TUI_EFFORT_POLICY", _ACTIVE["effort_policy"])
ALLOW_EXEC = os.environ.get("CODE_TUI_ALLOW_EXEC", "1") != "0"
ALLOWED_TOOLS = os.environ.get("CODE_TUI_ALLOWED_TOOLS", "").strip()
INLINE_FINISH = os.environ.get("CODE_TUI_INLINE_FINISH",
                               "1" if _ACTIVE["inline_finish"] else "0") != "0"


def budget(steps: int, wall_s: float, output: int = MAX_OUTPUT) -> Budget:
    """The production caps: `output` is clamped to MAX_OUTPUT, never raised past it."""
    return Budget(max_steps=steps, max_wall_s=wall_s, max_output_tokens=min(output, MAX_OUTPUT))


def production_scope() -> Scope:
    """The production capability limit: allowed verbs, calls/turn, output/turn, effort policy."""
    allowed = frozenset(part.strip() for part in ALLOWED_TOOLS.split(",") if part.strip())
    tools = frozenset(allowed or DEFAULT_TOOLS)
    if not _ACTIVE.get("task_tool", True):
        tools = tools - {"task"}
    return Scope(
        tools=tools,
        allow_exec=ALLOW_EXEC,
        max_calls_per_turn=MAX_CALLS_PER_TURN,
        max_turn_tokens=MAX_TURN_TOKENS,
        max_report_chars=MAX_REPORT_CHARS,
        effort=EFFORT,
        effort_policy=EFFORT_POLICY,
        inline_finish=INLINE_FINISH,
    )


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
    scope: Scope | None = None,
):
    """The one production loop factory: `mode` picks StateLoop | ToolLoop | ReplLoop."""
    active = scope or production_scope()
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
            scope=active,
            code_mode=CODE_MODE,
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
            scope=active,
            code_mode=CODE_MODE,
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
