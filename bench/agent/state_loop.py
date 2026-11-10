"""state_loop: StateLoop: ToolLoop + state-card observations; production default (CODE_TUI_MODE=state).

WHAT IT IS
  Same action space as ToolLoop (typed verbs + `python`); a cell result is a state card, not a transcript.
  state_card.py renders the card; state_text.py clips/digests/_OBS[n]; state_ranges.py ranges; state_fold.py fold.
  fold: real prompt tokens cross context_budget -> old turns replaced by one `[session state]` message from the journal.

ENTRY POINTS
  STATE_RULES = TOOL_RULES + the card contract (keep in sync with _observe); `[plan]` = durable RunState plan
  from the `plan` tool; CARD_BUDGET = default per-turn card budget; verifier / last_check = the `[check]` line.
  StateLoop(..., output_budget, context_budget, keep_messages); ._observe(cell, steps) -> str; ._compact(messages).
  re-exported: render_observation; clip/compact_stream/clip_card/digest_card/digest/data_block; merge_ranges/gaps/
  covered/numbered_lines/numbered_lines_by_path/delivered_ranges; session_state_message/cut_index.
INVARIANTS
  typed observation delivered whole: card renders page/data, only python/exec output is digested to _OBS[n].
  structured data never dropped by size: every field rendered, a large value clipped and named in `[data] clipped:`.
  coverage = data, never a guess: the loop opens no file; a card delivered exactly Cell.reads (digested: kept lines).
  restart never silent: Cell.restarted -> `[state] kernel restarted` line above the kernel's stderr message.
  card budget per turn: first card whole, rest share what is left; an error or check line is never cut.
  _compact cuts only on an assistant tool-call boundary; trigger = real prompt tokens at context_budget (85% of window).
  chg:/[diff] come from Cell.changed/deltas/delta (kernel _commit), never re-parsed; [check] = last_check cheap run.
WHERE TO CHANGE
  card layout / error line ... state_card.render_observation, state_text._ERROR_LINE   read ranges ... state_ranges.delivered_ranges, _observe
  compaction ... _compact, state_fold.cut_index, keep_messages, context_budget, config.COMPACT_AT   fold text ... state_fold.session_state_message
"""
from __future__ import annotations

from .state_card import render_observation
from .state_fold import cut_index, session_state_message
from .state_ranges import (covered, delivered_ranges, gaps, merge_ranges, numbered_lines,
                           numbered_lines_by_path)
from .state_text import clip, clip_card, compact_stream, data_block, digest, digest_card
from .tool_loop import TOOL_RULES, ToolLoop

__all__ = ["CARD_BUDGET", "STATE_RULES", "StateLoop", "clip", "clip_card", "compact_stream",
           "covered", "data_block", "delivered_ranges", "digest", "digest_card", "gaps",
           "merge_ranges", "numbered_lines", "numbered_lines_by_path", "render_observation"]

STATE_RULES = TOOL_RULES + """

STATE CARDS
A result is a state card, not a transcript: `[s]` step/cost, `p=` phase, `chg:` changed, `[obs]` typed result, `[page]` paging, `[data]` fields, `[diff]` mutation, `[check]` verdict, `[read]` delivered ranges. Read [page] before [data]. `next` is replayable; complete/has_more/truncated are the paging truth. Every `ok:false` carries code/message/use. Process output is bounded and names _OBS[n]; page it. Coverage is guidance, not a finish gate."""

CARD_BUDGET = 5_000


class StateLoop(ToolLoop):
    """ToolLoop plus state cards: typed observations verbatim, process output digested, coverage
    tracked per file, and the context folded at `context_budget` prompt tokens."""

    def __init__(
        self,
        *args,
        output_budget: int = 12_000,
        context_budget: int = 891_289,
        keep_messages: int = 20,
        card_budget: int = CARD_BUDGET,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.output_budget = output_budget
        self.context_budget = context_budget
        self.keep_messages = keep_messages
        self.card_budget = card_budget
        self._card_step = 0
        self._card_used = 0
        self.last_prompt_tokens = 0
        self.coverage: dict[str, dict] = {}
        self.deltas: dict[str, str] = {}

    def _note_cell(self, cell) -> None:
        for path, block in (cell.deltas or {}).items():
            self.deltas[path] = block

    def _observe(self, cell, steps: int) -> str:
        text, displayed = render_observation(
            cell, steps, self.last_prompt_tokens, self.last_check, self.output_budget,
            self.coverage, getattr(self, "last_check_handle", None),
            [self.state.phase_line(), *self.state.plan_lines()])
        if steps != self._card_step:
            self._card_step = steps
            self._card_used = 0
        handle = f"_OBS[{cell.obs_index}]" if cell.obs_index is not None else "_OBS[-1]"
        if self.card_budget > 0:
            room = max(0, self.card_budget - self._card_used)
            text, clipped = clip_card(text, room, handle)
            self._card_used += len(text)
            if clipped:
                displayed = delivered_ranges(cell.reads, text, digested=True)
        for path, ranges in displayed.items():
            total = int((cell.reads.get(path) or {}).get("total") or 0)
            entry = self.coverage.setdefault(path, {"ranges": [], "total": 0})
            entry["ranges"].extend(ranges)
            entry["total"] = total or entry["total"]
        return text

    def _compact(self, messages: list[dict]) -> None:
        """Fold old turns into one `[session state]` message; cuts only on a tool-call boundary."""
        if len(messages) < 8 or self.last_prompt_tokens < self.context_budget:
            return
        first = next((i for i, message in enumerate(messages)
                      if message.get("role") == "assistant" and message.get("tool_calls")), 2)
        head = messages[:first]
        body = messages[first:]
        cut = cut_index(body, self.keep_messages)
        dropped = body[:cut]
        if not dropped:
            return
        messages[:] = head + [session_state_message(self, len(dropped))] + body[cut:]
        self.compactions += 1
