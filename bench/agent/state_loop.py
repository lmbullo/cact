"""state_loop: StateLoop: ToolLoop + state-card observations. Production default
(`CODE_TUI_MODE=state`, `--mode state`, config.build).

WHAT IT IS
  Same action space as ToolLoop (typed verbs + `python` escape hatch); a cell's result renders as
  a state card, not a transcript. Card: state_card.render_observation. This module: the loop that
  calls it, per-turn card budget, coverage ledger, fold.
  Pieces by module:
    state_card.py    the card: [s], [obs]/[page], [data], [note]/[error], [stderr]/[stdout],
                     [result], [read]
    state_text.py    clipping, digesting, the _OBS[n] pointer, per-key [data] clip
    state_ranges.py  merge_ranges / gaps / covered / delivered_ranges
    here             the loop, the coverage ledger, the fold
  coverage: every read() records delivered line ranges in Cell.reads; renderer turns that + the
  text it rendered into (card, visible ranges); card prints `[read] path ranges/total (unread:
  holes)`. Guidance, not a finish gate: reads through run()/open() are valid and untracked; the
  loop never opens a workspace file to check.
  fold: when the provider's real prompt count crosses context_budget, old turns are replaced by
  one regenerated `[session state]` message built from the journal (changed files, diffs,
  coverage, check results).

ENTRY POINTS
  STATE_RULES = TOOL_RULES + the state-card contract; `[plan]` = durable RunState plan, set by
                the `plan` tool
  CARD_BUDGET ... per-turn card budget a StateLoop gets by default
  StateLoop(..., output_budget, context_budget, keep_messages)
    ._observe(cell, steps) -> str    render_observation + turn card budget + coverage
    ._compact(messages)              fold old turns into one `[session state]` message
    verifier / last_check            cheap check rendered as the `[check]` line
  re-exported for existing callers, defined in split modules:
    render_observation ......................... state_card.py
    clip / compact_stream / clip_card / digest_card / digest / data_block ... state_text.py
    merge_ranges / gaps / covered / numbered_lines / numbered_lines_by_path /
    delivered_ranges ........................... state_ranges.py
    session_state_message / cut_index .......... state_fold.py

INVARIANTS
  typed observation delivered whole. Cell.envelope = the normalized contract
  (bench/agent/envelope.py); the card renders page/data, never applies the output digest (the
  verb already paged). Only python/exec output is digested, and the digest names its _OBS[n].
  structured data never dropped by a size gate: every field rendered; a large value clipped and
  named in one `[data] clipped:` note, so closest/context, dirs_matched, argv/results and a
  refusal's signature/valid reach the model.
  coverage = data, never a guess. Loop opens no file, inverse-parses nothing: a verbatim typed
  card delivered exactly Cell.reads; a digested python/exec card delivered only the numbered
  lines the card kept.
  restart never silent: Cell.restarted -> explicit `[state] kernel restarted` line on top of the
  kernel's stderr message; a lost namespace never looks like a normal cell.
  card budget per turn: first card gets the whole card_budget; the rest share what is left; a
  clipped card recomputes its visible ranges. An error or check line is never cut.
  _compact cuts only on an assistant tool-call boundary: no tool result orphaned from its call.
  chg: and [diff] both come from the kernel journal (kernel/prelude.py::_commit):
  Cell.changed = path list, Cell.deltas = per-path unified diff, Cell.delta = joined text for the
  card. _note_cell reads structured deltas; never re-parses the text.
  [check] = ToolLoop's last_check (Verifier cheap subset, refreshed after a mutating cell at most
  every 20s); card renders one line: `pass`, or `<spec> : <first failing line>` with full output
  named as the _OBS[n] handle ToolLoop queued.
  compaction triggers on real prompt token count at context_budget (config sets 85% of the 1M
  window), never a character estimate; cuts only on a tool-call boundary.

WHERE TO CHANGE
  card layout / error-line selection .... state_card.render_observation, state_text._ERROR_LINE
  read ranges / what counts as read ..... state_ranges.delivered_ranges, _observe (keep them
                                          data; never re-read the file)
  compaction policy ..................... _compact, state_fold.cut_index, keep_messages,
                                          context_budget, config.COMPACT_AT / COMPACT_AT_TOKENS
  fold message text ..................... state_fold.session_state_message
  model-facing card contract ........... STATE_RULES (keep in sync with _observe)
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
