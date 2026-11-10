"""tool_turns: ToolTurns: the turn record, the reply dialect and the budget reminders.

WHAT IT IS
  The bookkeeping half of ToolLoop, as a mixin: _open_messages opens a run, _turn_record writes one trace record per turn,
  _reply is the one seam a subclass may replace, _before_turn adds the budget nudges, _write_summary closes the trace.

ENTRY POINTS
  ToolTurns ... mixin base of tool_loop.ToolLoop;  _open_messages(prompt) -> (messages, card)
  hooks a subclass overrides: _reply, _observe, _compact, _goal_state, _on_run_start, _before_turn, _drift_count
  record shape ... _turn_record, _merge_calls;  finish seam ... _finish_reply, _reject, _skeptic_review
  budget nudges ... WRAP_UP_NOTE, LAST_TURN_NOTE (values in tool_loop.py), _before_turn
  _push(messages, usage) = one client.complete for a summary, never raising;  _write_summary(trace, ...) = trace summary then close

INVARIANTS
  turns = one record per turn; top-level code/observation/reads/notice mirror the last call that set them, runs concatenates.
  every run gets one wrap-up nudge at wrap_up_at and one last-turn nudge at max_steps - 1; a subclass changes wording, never timing.
  the skeptic review is cached per evidence digest (candidate, diff, check text, changed set): a repeated finish attempt is free.
  _push is a real client.complete; it turns its own LLMError into empty text and still counts as an llm_call.
  the summary record is written in run()'s finally block, so a crash still leaves a trace.

WHERE TO CHANGE
  record shape ... _turn_record, _merge_calls   another reply dialect ... _reply (ReplLoop)   cell text ... _observe (StateLoop)
  budget/goal wording ... tool_loop.py ToolLoop (BUDGET_NOTE, WRAP_UP_NOTE)   timing ... WRAP_UP_AT, _before_turn   summary ... _write_summary
"""

from __future__ import annotations

from .check_run import evaluate
from .env import environment, render_environment
from .finish_flow import finish_reply, reject
from .limits import effort_for
from .llm import LLMError
from .loop import _usage_add
from .records import CallRecord, FinishState, Tally, TurnOutcome
from .stops import GOAL_UNMET, STAGED


class ToolTurns:
    """The turn record, the reply dialect and the budget reminders; mixed into ToolLoop."""

    def _open_messages(self, prompt: str) -> tuple[list[dict], str]:
        """The opening messages (rules, [env] card, task) and the card the loop pushed."""
        messages: list[dict] = []
        if self.rules:
            messages.append({"role": "system", "content": self.rules})
        root = getattr(self.kernel, "root", None)
        checks = []
        if self.verifier is not None:
            checks = [{"name": spec.name, "cmd": spec.cmd}
                      for spec in getattr(self.verifier, "specs", ()) or ()]
        card = render_environment(environment(root, prompt, checks)) if root is not None else ""
        messages.append({"role": "user", "content": (card + "\n\n" + prompt) if card else prompt})
        self._on_run_start(messages)
        self._task_prompt = prompt
        self.state.task = prompt
        self.state.env = card
        return messages, card

    @property
    def _gated(self) -> bool:
        """True when a finish must satisfy a check (a Verifier or a bare goal callable)."""
        return (self.verifier is not None and self.verifier.has_goal) or self.goal is not None

    def _observe(self, cell, steps: int) -> str:
        from .repl import observation

        return observation(cell, self.obs_chars)

    def _compact(self, messages: list[dict]) -> None:
        return None

    def _goal_state(self):
        if self.verifier is not None:
            return self.verifier.authoritative()
        return evaluate(self.goal)

    def _note_cell(self, cell) -> None:
        return None

    def _reject(self, check, prefix: str) -> str:
        """The re-prompt for a rejected finish, worded with this loop's noun and tails."""
        return reject(self, check, prefix)

    def _on_run_start(self, messages: list[dict]) -> None:
        """Hook: initialise per-run policy state (ReplLoop's prefix drift, for example)."""
        self._last_turn = False
        self._wrapped = False
        self._task_prompt = str(messages[-1].get("content") or "") if messages else ""
        self._skeptic_key: str | None = None
        self._sub_usage: dict = {}

    def _before_turn(self, messages: list[dict], tally: Tally) -> None:
        """Add the budget reminders every loop needs: wrap-up, then one last-turn notice.

        A loop that never says how many turns are left lets a model keep finding more work
        after the task is green. These are deterministic, once-per-run messages at the
        budget boundary; subclasses word them through WRAP_UP_NOTE / LAST_TURN_NOTE.
        """
        b = self.budget
        if not self._last_turn and tally.steps >= max(1, b.max_steps - 1):
            self._last_turn = True
            messages.append({"role": "user", "content": self.LAST_TURN_NOTE})
        elif self.wrap_up_at is not None and not self._wrapped:
            threshold = max(1, int(b.max_steps * self.wrap_up_at))
            if tally.steps >= threshold and tally.steps < max(1, b.max_steps - 1):
                self._wrapped = True
                messages.append({"role": "user",
                                 "content": self.WRAP_UP_NOTE.format(left=b.max_steps - tally.steps)})

    def _drift_count(self) -> int:
        return 0

    def _push(self, messages: list[dict], usage: dict | None = None) -> str:
        msgs = messages + [{
            "role": "user",
            "content": self.BUDGET_NOTE,
        }]
        try:
            resp = self.client.complete(msgs, None, self.scope.turn_tokens(self.budget.max_output_tokens),
                                        effort=effort_for(self.scope, True))
        except LLMError:
            return ""
        if usage is not None:
            _usage_add(usage, resp["usage"])
        return (resp.get("content") or "").strip()

    # --- the turn machine --------------------------------------------------------------
    def _turn_record(self, task_id: str, step: int, resp: dict, hit: int, miss: int) -> dict:
        tool_calls = resp.get("tool_calls") or []
        return {
            "task": task_id,
            "turn": step,
            "latency_ms": round(resp["latency_ms"]),
            "content": resp.get("content") or "",
            "reasoning": resp.get("reasoning") or "",
            "tool_calls": [tc.get("function", {}).get("name") for tc in tool_calls],
            "code": None,
            "observation": None,
            "cache": {"hit": hit, "miss": miss},
            # The provider's own usage for this turn: the TUI's live in/out and cache
            # counters are folded from it (server.ts), and the trace keeps it for a
            # later post-mortem.
            "usage": dict(resp.get("usage") or {}),
            "runs": [],
            "finish": resp.get("finish"),
            "card_chars": 0,
            "card_budget": int(getattr(self, "card_budget", 0) or 0),
        }

    def _finish_reply(self, messages: list[dict], rec: dict, content: str, finish_reason,
                      finish: FinishState) -> TurnOutcome:
        """A reply with no tool call: continue, reject, or accept the finish (finish_flow)."""
        return finish_reply(self, messages, rec, content, finish_reason, finish)

    def _skeptic_review(self, candidate: str, check):
        """One cached review per evidence digest; returns None when there is nothing to review."""
        if not candidate:
            return None
        import hashlib

        deltas = getattr(self, "deltas", {}) or {}
        diff = "\n".join(str(deltas[path]) for path in sorted(deltas) if deltas[path])[:12000]
        check_text = check.text() if check is not None else (
            self.last_check.text() if self.last_check is not None else "")
        key = hashlib.sha256(
            (candidate + "\x00" + diff + "\x00" + ",".join(sorted(self.changed))).encode()
        ).hexdigest()
        if key == self._skeptic_key:
            return None
        self._skeptic_key = key
        verdict = self.skeptic.review(self._task_prompt, candidate, diff=diff,
                                      check_text=check_text, changed=sorted(self.changed))
        _usage_add(self._sub_usage, verdict.usage)
        return verdict

    def _merge_calls(self, rec: dict, records: list[CallRecord]) -> None:
        """Mirror the pre-`calls` turn shape: last writer wins per field, runs concatenate."""
        runs: list = []
        for record in records:
            runs.extend(record.runs or [])
        rec["runs"] = runs
        for key in ("code", "observation", "reads", "obs", "check"):
            value = None
            for record in records:
                item = getattr(record, key)
                if item is not None:
                    value = item
            if value is not None:
                rec[key] = value
        # The turn-level notice belongs to the finish protocol.
        notices = [r.notice for r in records if r.stop in (STAGED, GOAL_UNMET) and r.notice]
        if notices:
            rec["notice"] = notices[-1]

    def _reply(self, messages: list[dict], rec: dict, resp: dict, finish: FinishState,
               tally: Tally, seen: set[str], step: int) -> TurnOutcome:
        """Turn one reply into an outcome. The one seam ReplLoop overrides."""
        content = resp.get("content") or ""
        tool_calls = resp.get("tool_calls") or []
        finish_reason = resp.get("finish")
        if not tool_calls:
            return self._finish_reply(messages, rec, content, finish_reason, finish)
        messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})
        return self._answer_calls(messages, rec, tool_calls, finish_reason, finish,
                                  tally, seen, step)

    def _write_summary(self, trace, task_id: str, stop: str, tally: Tally, usage: dict,
                       final: str, error: str, wall_ms: float, attestation: dict) -> None:
        """Write the run's summary record to the trace, then close it."""
        if not trace:
            return
        trace.write({
            "task": task_id,
            "summary": True,
            "stop": stop,
            "steps": tally.steps,
            "calls": tally.calls,
            "llm_calls": tally.llm_calls,
            "errors": tally.errors,
            "repeats": tally.repeats,
            "drift": self._drift_count(),
            "usage": usage,
            "wall_ms": round(wall_ms),
            "final": final,
            "error": error,
            "economy": self.meter.as_dict(),
            "state": self.state.summary(),
            "attestation": attestation,
        })
        trace.close()
