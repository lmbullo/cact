"""repl: ReplLoop: one fenced ```python block per reply (`--mode repl`) + shared rules text.

WHAT IT IS
  Fenced-block alternate to ToolLoop tool calls: the model writes code, it runs as a kernel cell, stdout/stderr/result
  return as an `[observation]`. Subclasses ToolLoop and reuses its turn machine; owns only the repl dialect: reply parsing
  (one fenced block / ANSWER line, reply.compile_reply), the observation transcript, finish switches (stop=call|var).

ENTRY POINTS
  REPL_RULES / BARE_RULES = system prompts (BARE_RULES = minimal variant); RUN_NOTE / RUN_NOTE_MUT when run() is enabled (repl_rules.py)
  observation(cell, cap) = one cell -> model-visible text (ToolLoop default);  history_entry(action) = parsed action -> transcript line
  ReplLoop(client, kernel, budget, rules, stop="call"|"var", history="full"|"obs"|"drop"|"action", obs_chars, goal,
           goal_label, goal_stops, wrap_up_at);  .run(...) inherited;  ._reply(...) reply -> Action -> cell -> TurnOutcome (the seam)

INVARIANTS
  reply with neither python block nor ANSWER line -> PROTOCOL + note, the run continues.
  final_answer in a printing cell -> UNREVIEWED (output shown, answer not accepted); with a goal both ANSWER and final_answer
  are rejected until it passes (GOAL_UNMET).
  history decides transcript retention (full reply, action only, nothing); large observations capped by obs_chars with an _OBS handle.
  wrap_up_at = fraction of the step budget for the wrap-up nudge; ToolLoop owns timing, this class supplies the fenced wording.
  budget, usage, goal gating, trace summary = ToolLoop's; repl wording (BUDGET_NOTE, CHECK_NOUN, REJECT_*_TAIL) + reminders in _before_turn.

WHERE TO CHANGE
  what counts as an action ... reply.py (compile_reply)   observation format ... `observation`   prompt text/run() notes ... repl_rules.py
  repl finish semantics ... `_reply` (UNREVIEWED/PROTOCOL/answer paths)   budget/usage/goal gating ... tool_loop.ToolLoop (run, _push, _reject)
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .kernel import Cell, Kernel
from .llm import Client
from .loop import Budget
from .reply import Action, compile_reply
from .repl_rules import BARE_RULES, REPL_RULES, RUN_NOTE, RUN_NOTE_MUT  # noqa: F401  (re-exported)
from .tool_loop import DONE, GOAL_UNMET, VERIFIED, ToolLoop, TurnOutcome


def history_entry(action: Action) -> str:
    """One past action as the text a `history=full` run replays (fence or ANSWER line)."""
    if action.kind == "code":
        return f"```python\n{action.code}\n```"
    if action.kind == "answer":
        return f"ANSWER: {action.value}"
    return ""


def observation(cell: Cell, cap: int = 0) -> str:
    """The repl-mode observation block for one cell; over `cap` chars the middle is hidden."""
    parts = [f"[observation] exit={'raise' if cell.stderr else 'ok'} time={cell.duration_ms}ms"]
    if cell.timeout:
        parts.append("timeout: the cell was interrupted; the namespace and variables are intact")
    if cell.result is not None:
        parts.append(f"result: {cell.result}")
    if cell.changed:
        parts.append(f"changed: {', '.join(cell.changed)}")
    if cell.stdout:
        parts.append(f"stdout:\n{cell.stdout.rstrip()}")
    if cell.stderr:
        parts.append(f"stderr:\n{cell.stderr.rstrip()}")
    body = "\n".join(parts)
    if cap and len(body) > cap:
        handle = f"_OBS[{cell.obs_index}]" if cell.obs_index is not None else "_OBS[-1]"
        head = body[: int(cap * 0.65)]
        tail = body[-int(cap * 0.25):]
        body = (f"{head}\n...[middle hidden: {len(body)} chars total; full output is {handle}"
                f" -- print({handle}['stdout'])]...\n{tail}")
    return body


class ReplLoop(ToolLoop):
    """ToolLoop whose action space is one ```python block per reply; `ANSWER:` or a bare reply
    finishes. It inherits the turn machine (pairing, stop reasons, goals) and overrides only the
    reply dialect."""

    BUDGET_NOTE = ("[runtime] The cell budget is exhausted. Reply with the final answer "
                   "as plain text and no code block.")
    CHECK_NOUN = "goal check"
    REJECT_NONE_TAIL = "The task is not complete. Continue working."
    REJECT_DETAIL_TAIL = "Keep working and re-run the check when you believe it passes."
    WRAP_UP_NOTE = ("[runtime] {left} steps left. Stop exploring. Make the smallest necessary "
                    "change, verify it, and finish with final_answer(<short summary>). Do not "
                    "start new investigations.")
    LAST_TURN_NOTE = ("[runtime] This is your last turn. Do not run more code or inspect more "
                      "files. Reply with exactly one fenced ```python block that calls "
                      "final_answer(<short summary>) and prints nothing.")

    def __init__(
        self,
        client: Client,
        kernel: Kernel,
        budget: Budget | None = None,
        rules: str | None = REPL_RULES,
        stop: str = "call",
        history: str = "full",
        obs_chars: int = 0,
        goal: Any = None,
        goal_label: str = "verified",
        goal_stops: bool = True,
        wrap_up_at: float | None = 0.8,
    ) -> None:
        super().__init__(
            client,
            kernel,
            budget,
            rules=rules,
            goal=goal,
            goal_label=goal_label,
            goal_stops=goal_stops,
            obs_chars=obs_chars,
            wrap_up_at=wrap_up_at,
        )
        self.tools = None                   # repl replies carry no tool_calls
        self.stop_mode = stop               # "call" -> cell.final, "var" -> cell.answer
        self.history = history
        self.wrap_up_at = wrap_up_at

    def _on_run_start(self, messages: list[dict]) -> None:
        super()._on_run_start(messages)
        self._prefix_key = hashlib.sha256(
            json.dumps(messages, sort_keys=True).encode()).hexdigest()
        self._drift = 0

    def _before_turn(self, messages: list[dict], tally) -> None:
        super()._before_turn(messages, tally)
        if hashlib.sha256(json.dumps(messages[:2], sort_keys=True).encode()).hexdigest() != self._prefix_key:
            self._drift += 1

    def _drift_count(self) -> int:
        return getattr(self, "_drift", 0)

    def _observe(self, cell, steps: int) -> str:
        return observation(cell, self.obs_chars)

    def _turn_record(self, task_id: str, step: int, resp: dict, hit: int, miss: int) -> dict:
        return {
            "task": task_id,
            "turn": step,
            "latency_ms": round(resp["latency_ms"]),
            "content": resp.get("content") or "",
            "reasoning": resp.get("reasoning") or "",
            "code": None,
            "observation": None,
            "cache": {"hit": hit, "miss": miss},
            # Same as the tool loop: the TUI folds this into its live in/out counters.
            "usage": dict(resp.get("usage") or {}),
            "runs": [],
        }

    def _reply(self, messages: list[dict], rec: dict, resp: dict, finish, tally,
               seen: set[str], step: int) -> TurnOutcome:
        """Parse one reply, run its cell, and decide the turn. Repl's only loop seam."""
        content = resp.get("content") or ""
        action = compile_reply(content)
        code = action.code if action.kind == "code" else None
        rec["code"] = code

        if code is None:
            if action.kind == "answer":
                met, check = self._goal_state()
                if not met:
                    if self.history != "drop":
                        messages.append({"role": "assistant", "content": history_entry(action) or content})
                    messages.append({"role": "user", "content": self._reject(check, "ANSWER rejected")})
                    return TurnOutcome(stop=GOAL_UNMET)
                return TurnOutcome(stop=DONE, final=action.value or "", end=True)
            if action.error:
                note = f"[observation] that reply did not produce code: {action.error}. Nothing ran."
                if action.detail:
                    note += f"\n{action.detail}"
                if action.source == "markup":
                    note += "\nThe call was understood but carried no runnable body. Reply with one ```python block."
            else:
                note = "[observation] No ```python block and no line 'ANSWER: <value>' in that reply. The task is still open."
            messages.append({"role": "user", "content": note})
            rec["error"] = action.error
            return TurnOutcome(stop="PROTOCOL")

        if code in seen:
            tally.repeats += 1
        else:
            seen.add(code)
        tally.calls += 1
        cell = self.kernel.run(code)
        rec["runs"] = cell.runs or []
        rec["observation"] = {
            "exit": "raise" if cell.stderr else "ok",
            "result": cell.result,
            "stdout": cell.stdout,
            "stderr": cell.stderr,
            "restarted": cell.restarted,
            "duration_ms": cell.duration_ms,
        }
        if cell.stderr:
            tally.errors += 1

        terminal = cell.final if self.stop_mode == "call" else cell.answer
        if terminal is not None and (cell.stdout.strip() or cell.stderr.strip()):
            if self.history != "drop":
                kept = content if self.history == "full" else history_entry(action)
                messages.append({"role": "assistant", "content": kept})
            messages.append({"role": "user", "content": observation(cell, self.obs_chars) + "\n[observation] final_answer was not accepted: this cell printed output, so you have not seen it yet. Read it, then call final_answer(<value>) in a cell that prints nothing."})
            return TurnOutcome(stop="UNREVIEWED")
        if terminal is not None:
            met, check = self._goal_state()
            if not met:
                if self.history != "drop":
                    kept = content if self.history == "full" else history_entry(action)
                    messages.append({"role": "assistant", "content": kept})
                messages.append({"role": "user", "content": self._reject(check, "final_answer rejected")})
                return TurnOutcome(stop=GOAL_UNMET)
            return TurnOutcome(stop=DONE, final=terminal.strip(), end=True)

        if self.goal_stops and self.goal is not None and cell.mutated and self._goal_state()[0]:
            return TurnOutcome(stop=VERIFIED, final=self.goal_label, end=True)

        if self.history != "drop":
            kept = content if self.history == "full" else history_entry(action)
            messages.append({"role": "assistant", "content": kept})
        messages.append({"role": "user", "content": observation(cell, self.obs_chars)})
        return TurnOutcome()
