"""config: production defaults and the loop factory used by the TUI (tui/python/worker.py).

WHAT IT IS
  Sole production construction site: which loop runs and with which budgets; build() is what the worker calls.
  MODE = CODE_TUI_MODE read at import; default `state`; also `tool`, `repl`.

ENTRY POINTS
  CONTEXT_WINDOW / MAX_OUTPUT / COMPACT_AT / OBS_CHARS / HISTORY / AUTONOMY ... constants
  PROFILE / PROFILES / CODE_MODE / EFFORT / EFFORT_POLICY / INLINE_FINISH / RULES ... the measured arm
  budget(steps, wall_s, output=MAX_OUTPUT) -> Budget (clamped to MAX_OUTPUT);  production_scope() -> Scope
  build(client, kernel, budget, *, rules, history, obs_chars, stop, goal, goal_label, goal_stops, mode=MODE,
        health=None, verifier=None, scope=None) -> StateLoop | ToolLoop | ReplLoop

INVARIANTS
  build = only production construction site; evals construct via bench/evals/loops.py::run_loop; constants are TUI-facing.
  unknown mode -> ReplLoop: a typo degrades to the simplest loop, never crashes a worker.
  PROFILE: fast-code (default) = lean typed set + one code cell, inline finish, green-plan stop; m3-plan = historical
  python-cell arm; current = m3-plan + inline finish; code-act = code-only; CODE_TUI_* flags override the profile.
  scope() = harness capability limit: CODE_TUI_ALLOWED_TOOLS narrows, ALLOW_EXEC=0 drops exec, MAX_CALLS_PER_TURN and
  MAX_TURN_TOKENS cap a turn, EFFORT_POLICY=adaptive; COMPACT_AT_TOKENS = 85% of 1M vs the real prompt count; OBS_CHARS=0 = the card decides.
  verifier (check_plan.Verifier) = production wiring; goal/health stay for bare callables; ReplLoop has no verifier/scope slot.
WHERE TO CHANGE
  loop selection ... build + MODE   budgets ... budget, MAX_OUTPUT   capabilities ... production_scope() + constants (agent/limits.py owns Scope)
  measured arm ... PROFILE / PROFILES + CODE_TUI_* flags   orchestration cell ... CODE_MODE   compaction ... CONTEXT_WINDOW, COMPACT_AT
"""
from __future__ import annotations

import os
from typing import Any

from .kernel import Kernel
from .limits import DEFAULT_TOOLS, Scope
from .llm import Client
from .loop import Budget
from .repl import ReplLoop
from .state_loop import STATE_RULES, StateLoop
from .tool_loop import ToolLoop

CONTEXT_WINDOW = 1_048_576
MAX_OUTPUT = 393_216
COMPACT_AT = 0.85
COMPACT_AT_TOKENS = int(CONTEXT_WINDOW * COMPACT_AT)
OBS_CHARS = 0
CARD_BUDGET = 5_000
HISTORY = "action"

MODE = os.environ.get("CODE_TUI_MODE", "state")
# Measured arm: python cell (not code mode), fixed low effort, env card + durable plan, staged
# finish. `current` = inline finish. Individual env flags override either profile.
PROFILE = os.environ.get("CODE_TUI_PROFILE", "fast-code")
_FAST_TOOLS = ("read,edit,write,search,checks,exec,status,outline,page,apply,delete,mkdir,"
               "splice,git,guide,skill,map")
PROFILES = {
    "m3-plan": {"code_mode": False, "effort": "low", "effort_policy": "fixed",
                "inline_finish": False, "task_tool": False},
    "current": {"code_mode": False, "effort": "low", "effort_policy": "fixed",
                "inline_finish": True, "task_tool": True},
    "code-act": {"code_mode": True, "effort": "low", "effort_policy": "fixed",
                 "inline_finish": True, "task_tool": False,
                 "allowed_tools": "code,read,edit,write,search,checks,exec,status,outline"},
    "fast": {"code_mode": False, "effort": "low", "effort_policy": "fixed",
             "inline_finish": True, "task_tool": False, "allowed_tools": _FAST_TOOLS},
    "fast-code": {"code_mode": False, "effort": "low", "effort_policy": "fixed",
                  "inline_finish": True, "task_tool": False,
                  "allowed_tools": "code," + _FAST_TOOLS},
}
_ACTIVE = PROFILES.get(PROFILE, PROFILES["fast-code"])
CODE_MODE = os.environ.get("CODE_TUI_CODE_MODE",
                           "1" if _ACTIVE["code_mode"] else "0") == "1"
# Production contract = fire-and-forget: unattended, no human answer, no same-session repair.
# Gate on by default; CODE_TUI_AUTONOMY=0 disables it for an interactive experiment.
AUTONOMY = os.environ.get("CODE_TUI_AUTONOMY", "1") != "0"

# Production capability ceilings, not targets: a turn may use less. Adaptive effort policy is
# opt-in until a live run shows the omitted-effort default is not more expensive than `low`.
MAX_CALLS_PER_TURN = int(os.environ.get("CODE_TUI_MAX_CALLS_PER_TURN", "8"))
MAX_TURN_TOKENS = int(os.environ.get("CODE_TUI_MAX_TURN_TOKENS", "8192"))
MAX_REPORT_CHARS = int(os.environ.get("CODE_TUI_MAX_REPORT_CHARS", "4000"))
EFFORT = os.environ.get("CODE_TUI_EFFORT", _ACTIVE["effort"])
EFFORT_POLICY = os.environ.get("CODE_TUI_EFFORT_POLICY", _ACTIVE["effort_policy"])
ALLOW_EXEC = os.environ.get("CODE_TUI_ALLOW_EXEC", "1") != "0"
ALLOWED_TOOLS = os.environ.get("CODE_TUI_ALLOWED_TOOLS", "").strip()
INLINE_FINISH = os.environ.get("CODE_TUI_INLINE_FINISH",
                               "1" if _ACTIVE["inline_finish"] else "0") != "0"
RULES = STATE_RULES


def budget(steps: int, wall_s: float, output: int = MAX_OUTPUT) -> Budget:
    """The production caps: `output` is clamped to MAX_OUTPUT, never raised past it."""
    return Budget(max_steps=steps, max_wall_s=wall_s, max_output_tokens=min(output, MAX_OUTPUT))


def production_scope() -> Scope:
    """The production capability limit: allowed verbs, calls/turn, output/turn, effort policy."""
    raw = ALLOWED_TOOLS or str(_ACTIVE.get("allowed_tools") or "")
    allowed = frozenset(part.strip() for part in raw.split(",") if part.strip())
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
