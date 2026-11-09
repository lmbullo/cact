"""state_fold: the fold: which turns a long run may drop + the one `[session state]` message.

WHAT IT IS
  When real prompt tokens cross context_budget, StateLoop._compact replaces old turns with one
  regenerated message built from the journal: changed files, diffs, what was read, check result,
  plan. State, not a summary of what was said. This module owns the two pure pieces: where the
  cut is legal, and the message text.

ENTRY POINTS
  cut_index(body, keep) -> int            where a fold may cut body (never mid tool exchange)
  session_state_message(loop, folded) -> dict   the `[session state]` message, one dict

INVARIANTS
  cut lands on an assistant message carrying tool_calls, or past the end: no role:"tool" result
  is ever orphaned from the call that produced it.
  message = state, not narrative: changed paths, current diff, coverage per file as
  covered/total + first unread range, phase, plan, last check. A model reading it can act
  without the dropped turns.
  rebuilt from the journal on every fold: at most one fold stale; says how many messages went
  and which fold ordinal this is.
  pure: reads loop fields, returns a dict; the caller mutates messages.

WHERE TO CHANGE
  cut policy ...... cut_index, StateLoop.keep_messages
  fold trigger .... StateLoop._compact (last_prompt_tokens vs context_budget)
  message text .... session_state_message
"""
from __future__ import annotations

from typing import Any

from .state_ranges import covered, gaps
from .state_text import clip


def cut_index(body: list[dict], keep: int) -> int:
    """The first index at or after `len(body) - keep` that is an assistant tool-call message."""
    cut = max(0, len(body) - keep)
    while cut < len(body) and not (body[cut].get("role") == "assistant"
                                   and body[cut].get("tool_calls")):
        cut += 1
    return cut


def session_state_message(loop: Any, folded: int) -> dict:
    """The regenerated `[session state]` message: journal facts, not a summary of the turns."""
    lines = [f"[session state] {folded} earlier messages were folded; the workspace is authoritative. "
             f"{loop.compactions + 1} fold(s). Do not re-derive what earlier turns established."]
    if loop.changed:
        lines.append("changed: " + ", ".join(sorted(loop.changed)))
    diffs = [loop.deltas[path] for path in sorted(loop.deltas) if loop.deltas[path]]
    if diffs:
        lines.append("[diff] current change set\n" + clip("\n".join(diffs), 6000))
    if loop.coverage:
        reads = []
        for path in sorted(loop.coverage)[:16]:
            entry = loop.coverage[path]
            total = int(entry.get("total") or 0)
            ranges = entry.get("ranges") or []
            missing = gaps(ranges, total)
            suffix = f" unread {missing[0][0]}-{missing[-1][1]}" if missing else ""
            reads.append(f"{path} {covered(ranges)}/{total}{suffix}")
        lines.append("reads: " + "; ".join(reads))
    lines.append(loop.state.phase_line())
    lines.extend(loop.state.plan_lines())
    if loop.last_check is not None:
        lines.append(f"check: {loop.last_check.cmd}: {'passed' if loop.last_check.ok else 'failed'}")
    return {"role": "user", "content": "\n".join(lines)}
