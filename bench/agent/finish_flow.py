"""finish_flow: the finish protocol: stage, gate, reject, accept.
WHAT IT IS
  The turn-machine step turning a proposed finish into STAGED / rejected / accepted; called by
  tool_loop.py, owns no state of its own.

ENTRY POINTS
  asks_for_input(text) -> bool; finish_reply(loop, messages, rec, content, finish_reason, finish)
    -> TurnOutcome; report_cell(loop, obs, cell, record, finish, step) -> str (paired tool text)
  after_cell(loop, cell, finish, step) -> None;  reject(loop, check, prefix) -> str
  loop = tool_loop.ToolLoop; seam: .autonomy, .skeptic, .scope (inline_finish, clip_report),
    .goal_label, .goal_stops, ._surprise, ._gated, ._goal_state(), ._skeptic_review(), .state.phase

INVARIANTS
  gated run: no finish accepted without the goal check, nor while the skeptic reports a gap;
  both rejections clear the stage and continue the run.
  accepted candidate = reply text for a silent stage, staged value otherwise (finish.silent /
  finish.content); always passes clip_report; record.notice = last 2000 chars of the long text.
  rejection clears the stage: a later cell cannot resurrect an answer the goal or skeptic refused.
  autonomy=True + asks a human -> NEEDS_INPUT: no question, no wait, a defensible default.

WHERE TO CHANGE
  finish decision order ......... finish_reply, report_cell
  rejection wording ............. reject; ToolLoop CHECK_NOUN / REJECT_NONE_TAIL / REJECT_DETAIL_TAIL
  end without a stage / question  after_cell (goal_stops), finish_reply; ASK / asks_for_input
"""
from __future__ import annotations

import re
from typing import Any

from .records import FinishState, TurnOutcome
from .stops import DONE, GOAL_UNMET, NEEDS_INPUT, SKEPTIC_UNMET, STAGED, TRUNCATED, VERIFIED

ASK = re.compile(
    r"\b(would you like|do you want|should i\b|shall i\b|please (confirm|choose|tell me)|"
    r"let me know|can you (confirm|clarify)|awaiting (your|input)|which (one|option) "
    r"(should|do)|i need (your|more) (input|information|clarification))",
    re.IGNORECASE,
)


def asks_for_input(text: str) -> bool:
    """True when a proposed finish asks a human to decide; unattended runs must choose."""
    return bool(text) and bool(ASK.search(text))


def reject(loop: Any, check, prefix: str) -> str:
    """The re-prompt for a rejected finish: the loop's noun, the check text and its tail."""
    if check is None:
        return f"[observation] {prefix}: the {loop.CHECK_NOUN} still fails. {loop.REJECT_NONE_TAIL}"
    return (f"[observation] {prefix}: the {loop.CHECK_NOUN} says the task is not complete.\n"
            f"{check.text()}\n{loop.REJECT_DETAIL_TAIL}")


def _reject_turn(loop: Any, messages: list[dict], rec: dict, notice: str, stop: str,
                 finish: FinishState) -> TurnOutcome:
    """Clear the stage, hand the model the notice, and continue the run with `stop`."""
    messages.append({"role": "user", "content": notice})
    rec["notice"] = notice[:2000]
    loop._surprise = True
    finish.clear()
    return TurnOutcome(stop=stop)


def finish_reply(loop: Any, messages: list[dict], rec: dict, content: str, finish_reason,
                 finish: FinishState) -> TurnOutcome:
    """A reply with no tool call: continue, reject, or accept the finish."""
    if finish_reason == "length":
        loop._surprise = True
        messages.append({"role": "user", "content": "[runtime] Your reply was cut off before it finished. Continue from where it stopped."})
        return TurnOutcome(stop=TRUNCATED)
    text = content.strip()
    if finish.staged is not None:
        if finish.silent:
            candidate = text or finish.content or finish.staged
        else:
            candidate = finish.staged
    else:
        candidate = text
    if loop.autonomy and asks_for_input(candidate):
        notice = ("[observation] this is an unattended, fire-and-forget task: no human "
                  "will answer. Choose a defensible default, make the change, verify the "
                  "observable behavior, and report the choice. Do not ask a question.")
        return _reject_turn(loop, messages, rec, notice, NEEDS_INPUT, finish)
    met, check = loop._goal_state()
    if loop._gated and not met:
        notice = reject(loop, check, "You finished without a passing check")
        return _reject_turn(loop, messages, rec, notice, GOAL_UNMET, finish)
    if loop.skeptic is not None:
        verdict = loop._skeptic_review(candidate, check)
        if verdict is not None and not verdict.ok:
            notice = ("[observation] a read-only skeptic reviewed the finish and found a gap: "
                      + verdict.text + "\nFix the gap and verify again; do not accept the "
                      "current evidence as complete.")
            return _reject_turn(loop, messages, rec, notice, SKEPTIC_UNMET, finish)
    accepted = loop.scope.clip_report(candidate)
    finish.accept(accepted)
    return TurnOutcome(stop=DONE, final=accepted, end=True)


def report_cell(loop: Any, obs: str, cell, record, finish: FinishState, step: int) -> str:
    """A cell that called `final_answer`: stage, accept in place, or reject; returns the text."""
    loop._surprise = True
    loop.state.phase = "report"
    handoff = dict(getattr(cell, "handoff", None) or {})
    silent = not (cell.stdout.strip() or cell.stderr.strip())
    if loop.scope.inline_finish:
        met, check = loop._goal_state()
        if not loop._gated or met:
            if loop.skeptic is not None:
                verdict = loop._skeptic_review(cell.final, check)
                if verdict is not None and not verdict.ok:
                    finish.stage(cell.final, turn=step, handoff=handoff, silent=True)
                    notice = (obs + "\n[finish] rejected by the skeptic: " + verdict.text[:1200])
                    record.notice = notice[-2000:]
                    record.stop = SKEPTIC_UNMET
                    return notice
            accepted = loop.scope.clip_report(cell.final)
            finish.handoff = handoff
            finish.accept(accepted)
            record.stop = VERIFIED if accepted == loop.goal_label else DONE
            record.notice = (obs + "\n[finish] accepted in this cell; the run is "
                             "complete.")[-2000:]
            return record.notice
        finish.stage(cell.final, turn=step, handoff=handoff, silent=True)
        notice = obs + "\n" + reject(loop, check, "You finished without a passing check")
        record.notice = notice[-2000:]
        record.stop = GOAL_UNMET
        return notice
    finish.stage(cell.final, turn=step, handoff=handoff, silent=silent)
    notice = (obs + f"\n[finish] answer staged: {str(cell.final)[:400]!r}. "
              "The next reply with no tool call is the handoff (its content wins for "
              "a silent cell); write the real report there, or call final_answer(...) "
              "again to change it.")
    record.notice = notice[-2000:]
    record.stop = STAGED
    return notice


def after_cell(loop: Any, cell, finish: FinishState, step: int) -> None:
    """The ordinary cell: clear a stale stage; with `goal_stops`, accept once a mutation passes."""
    finish.clear(turn=step)
    if loop.goal_stops and loop._gated and cell.mutated and loop._goal_state()[0]:
        finish.accept(loop.goal_label)
