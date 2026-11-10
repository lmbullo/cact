"""tool_loop: ToolLoop, the turn machine: construction and the run driver.

WHAT IT IS
  The class production loops extend: wires model, kernel and policy; run() drives turns (one reply = one turn).
  typed call -> Kernel.call(name,args), python cell -> Kernel.run(code), both -> Cell; state: FinishState, TurnOutcome, CallRecord, Tally.

ENTRY POINTS
  ToolLoop(client, kernel, budget=, rules=, tools=, goal=, goal_label=, goal_stops=, obs_chars=, health=, verifier=,
           wrap_up_at=0.8, extra_tools=, subagent=, skeptic=, autonomy=, docs=, scope=, code_mode=);
           .run(task_id, prompt, trace) -> RunResult;  stop vocabulary (stops.py): DONE, VERIFIED, STEP_LIMIT,
           WALL_LIMIT, STAGED, GOAL_UNMET, TRUNCATED, BAD_ARGS, LLM_FAILED, TOKEN_LIMIT, SKEPTIC_UNMET, NEEDS_INPUT
  FinishState() .stage/.accept/.clear/.staged/.terminal; CallRecord(index,name,call_id); TurnOutcome(stop,final,end).
INVARIANTS
  run() never raises on a client.complete LLMError: stops LLM_FAILED, keeps the final/staged answer, writes the trace;
  tally.steps = turns (primary completes), tally.llm_calls = every request, so llm_calls >= steps.
  staged final_answer accepts on the next no-tool reply (after the goal check), a silent stage's content wins; a mutation,
  a later non-final cell or a rejected finish clears it; a silent final_answer skips the rest of the turn but pairs each call.
  GREEN = post-plan gate (green_gate.py): after a mutating run's checks pass, exec/checks are refused, one code cell may answer.
  goal: finishing before the check passes -> GOAL_UNMET; goal_stops=True + passing mutation -> VERIFIED; the Verifier owns it.
  a finish is never rejected for citation coverage; changed accumulates every file the kernel reports.
WHERE TO CHANGE
  turn seq/construction ... run, __init__, tool_specs.model_tools   opening msgs ... tool_turns._open_messages
  finish/stop codes ... finish_flow.py, stops.py, tool_calls.py   wording ... tool_turns BUDGET_NOTE/CHECK_NOUN/WRAP_UP_NOTE
  checks ... tool_health.py   plan/task/delegate/docs ... tool_subtools.py   schemas ... verbs.VERBS, tool_specs.py
"""
from __future__ import annotations

import time
from typing import Any

from .finish_flow import asks_for_input
from .green_gate import GreenGate
from .kernel import Kernel
from .limits import Scope, TokenMeter, effort_for
from .llm import Client, LLMError
from .loop import Budget, RunResult, _usage_add
from .records import FinishState, Tally, TurnOutcome
from .state import RunState
from .stops import (BAD_ARGS, DONE, GOAL_UNMET, GREEN, LLM_FAILED, NEEDS_INPUT, SKEPTIC_UNMET,
                    STAGED, STEP_LIMIT, STOPS, TOKEN_LIMIT, TRUNCATED, VERIFIED, WALL_LIMIT)
from .tool_calls import ToolCalls
from .tool_health import ToolHealth
from .tool_specs import CODE_TOOL, PLAN_TOOL, PYTHON_TOOL, TASK_TOOL, TOOL_RULES, model_tools
from .tool_subtools import ToolSubTools
from .tool_turns import ToolTurns


__all__ = ["BAD_ARGS", "DONE", "GOAL_UNMET", "GREEN", "LLM_FAILED", "NEEDS_INPUT",
           "SKEPTIC_UNMET", "STAGED", "STEP_LIMIT", "STOPS", "TOKEN_LIMIT", "TRUNCATED",
           "VERIFIED", "WALL_LIMIT", "CODE_TOOL", "PYTHON_TOOL", "PLAN_TOOL", "TASK_TOOL",
           "TOOL_RULES", "ToolLoop", "FinishState", "Tally", "TurnOutcome", "Budget",
           "RunResult", "asks_for_input"]

# The question detector is the finish protocol's; it stays importable under both names.
_asks_for_input = asks_for_input


class ToolLoop(ToolTurns, ToolCalls, ToolSubTools, ToolHealth):
    """The turn machine: tool calls -> kernel cells -> paired tool results -> a staged finish.

    Owns the finish protocol (staging, goal rejection, LLM_FAILED), the stop vocabulary and the
    run. StateLoop and ReplLoop override only the reply dialect and the observation rendering;
    every other concern lives in one mixin beside this file.
    """

    # The shared turns policy is parameterised where a second loop (ReplLoop) words it
    # differently; keep the wording here so repl.py owns only its reply format.
    BUDGET_NOTE = ("[runtime] The step budget is exhausted. Reply with a short final "
                   "summary and no tool call.")
    CHECK_NOUN = "project check"
    REJECT_NONE_TAIL = "Keep working."
    REJECT_DETAIL_TAIL = ("Keep working and re-run the check when you believe it passes.")
    WRAP_UP_AT = 0.8
    WRAP_UP_NOTE = ("[runtime] {left} steps left. Stop exploring. Finish the current change, "
                    "verify it, then reply with a final summary and no tool call. Do not start "
                    "new investigations.")
    LAST_TURN_NOTE = ("[runtime] This is your last turn. Do not run more tools or inspect more "
                      "files. Reply with a final summary and no tool call.")

    def __init__(
        self,
        client: Client,
        kernel: Kernel,
        budget: Budget | None = None,
        rules: str = TOOL_RULES,
        tools: list[dict] | None = None,
        goal: Any = None,
        goal_label: str = "verified",
        goal_stops: bool = False,
        obs_chars: int = 0,
        health: Any = None,
        verifier: Any = None,
        wrap_up_at: float | None = WRAP_UP_AT,
        extra_tools: list[dict] | None = None,
        subagent: Any = None,
        skeptic: Any = None,
        autonomy: bool = False,
        docs: Any = None,
        scope: Scope | None = None,
        code_mode: bool = False,
    ) -> None:
        self.client = client
        self.kernel = kernel
        self.budget = budget or Budget()
        self.scope = scope or Scope()
        self.meter = TokenMeter()
        self.state = RunState()
        self._surprise = True
        self.rules = rules
        self.scope, self.tools = model_tools(self.scope, kernel, tools, code_mode=code_mode,
                                             extra_tools=extra_tools, subagent=subagent,
                                             docs=docs)
        self.extra_names = {spec["function"]["name"] for spec in (extra_tools or [])}
        self.subagent = subagent
        self.skeptic = skeptic
        self.autonomy = autonomy
        self.docs = docs
        self.goal = goal
        self.goal_label = goal_label
        self.goal_stops = goal_stops
        self.obs_chars = obs_chars
        self.changed: set[str] = set()
        self.compactions = 0
        self.health = health
        self.verifier = verifier
        self.wrap_up_at = wrap_up_at
        self.last_check: Any = None
        self.last_check_handle: str | None = None
        self._health_at = 0.0
        self._extra_obs: list[dict] = []
        self.check_cache: dict[str, str] = {}
        self.gate = GreenGate()

    def run(self, task_id: str, prompt: str, trace: Any = None) -> RunResult:
        """Drive the turns until a stop reason fires; always writes the summary record and returns."""
        b = self.budget
        t0 = time.perf_counter()
        messages, card = self._open_messages(prompt)

        tally = Tally()
        usage: dict = {}
        turns: list[dict] = []
        final = ""
        error = ""
        stop = STEP_LIMIT
        seen: set[str] = set()
        finish = FinishState()

        try:
            while True:
                self._compact(messages)
                if tally.steps >= b.max_steps or (time.perf_counter() - t0) > b.max_wall_s:
                    tally.llm_calls += 1      # _push is a real client.complete
                    final = self._push(messages, usage)
                    stop = STEP_LIMIT if tally.steps >= b.max_steps else WALL_LIMIT
                    break

                self._before_turn(messages, tally)
                tally.steps += 1
                tally.llm_calls += 1
                resp = self.client.complete(messages, self.tools,
                                            self.scope.turn_tokens(b.max_output_tokens),
                                            effort=effort_for(self.scope, self._surprise))
                _usage_add(usage, resp["usage"])
                self.meter.add(resp.get("usage") or {}, resp.get("content") or "",
                               resp.get("tool_calls") or [])
                if b.max_total_tokens and (int(usage.get("total_tokens") or 0)
                                           + int(self._sub_usage.get("total_tokens") or 0)) >= b.max_total_tokens:
                    stop = TOKEN_LIMIT
                    final = finish.terminal or finish.staged or ""
                    break
                cu = resp.get("usage") or {}
                hit = int(cu.get("prompt_cache_hit_tokens") or 0)
                miss = int(cu.get("prompt_cache_miss_tokens") or 0)
                self.last_prompt_tokens = hit + miss
                rec = self._turn_record(task_id, tally.steps, resp, hit, miss)
                rec["economy"] = dict(self.meter.last)
                outcome = self._reply(messages, rec, resp, finish, tally, seen, tally.steps)

                if outcome.stop is not None:
                    rec["stop"] = outcome.stop
                turns.append(rec)
                if trace:
                    trace.write(rec)
                if outcome.end:
                    final = outcome.final or ""
                    stop = outcome.stop or DONE
                    break
        except LLMError as exc:
            # Primary model call failed: stop, never escape; keep any staged/accepted answer.
            # Keep provider detail: "LLM_FAILED" alone hides a 429, a 500 or a bad request.
            stop = LLM_FAILED
            error = f"{type(exc).__name__}: {exc}"
            final = final or finish.terminal or finish.staged or ""
        finally:
            _usage_add(usage, self._sub_usage)
            wall_ms = (time.perf_counter() - t0) * 1000.0
            attestation = {**self.state.summary(), "stop": stop, "steps": tally.steps}
            self._write_summary(trace, task_id, stop, tally, usage, final, error, wall_ms,
                                attestation)

        return RunResult(
            task=task_id,
            stop=stop,
            final=final,
            handoff=dict(finish.handoff),
            steps=tally.steps,
            calls=tally.calls,
            errors=tally.errors,
            repeats=tally.repeats,
            usage=usage,
            wall_ms=wall_ms,
            trace=getattr(trace, "path", None),
            turns=turns,
            llm_calls=tally.llm_calls,
            error=error,
            economy=self.meter.as_dict(),
            attestation=attestation,
        )
