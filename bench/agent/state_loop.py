"""StateLoop: ToolLoop + state-card observations. The production mode (`CODE_TUI_MODE=state`,
`--mode state`), and the default in config.build.

WHAT IT IS
  Same action space as ToolLoop (one `python` tool), but a cell's result is rendered as a
  state card instead of a transcript:

    [state]  turn, elapsed, prompt tokens, and the files this cell changed
    [diff]   the exact hunks this cell changed (empty when nothing changed)
    [stderr] / [stdout]  digested: head, error lines, tail, plus an _OBS[n] handle
    [result] the last expression, clipped

  Plus compaction: when the transcript grows past `context_budget`, the oldest turns are
  folded into one `[history]` message so the model keeps working from the workspace rather
  than from stale text.

ENTRY POINTS
  STATE_RULES = TOOL_RULES + the state-card contract (what the model is told)
  clip(text, limit)                  bounded text
  digest(text, budget, handle)       head + error lines + "[n lines, m chars; full text is
                                     _OBS[i]" + tail
  StateLoop(..., output_budget, context_budget, keep_messages)
    ._observe(cell, steps) -> str    the card
    ._compact(messages)              fold old turns into a [history] message
    ._estimate(messages)             chars/4 heuristic for the context budget

INVARIANTS
  - Output is never silently dropped: the card always names the `_OBS[n]` entry holding the
    full text, and the kernel keeps `_OBS` in the namespace so `print(_OBS[n]['stdout'])`
    works. That is why the handle, not the text, is the contract.
  - `_compact` cuts only on an assistant tool-call boundary, so no tool result is orphaned
    from its call.
  - The `[state] changed:` line and `[diff]` both come from `Cell.changed` / `Cell.delta`,
    which the kernel's journal produces (kernel.py::_commit).

WHERE TO CHANGE
  Card layout / error-line selection ... `_observe`, `digest`, `_ERROR_LINE`, `clip`
  Compaction policy ..................... `_compact`, `_estimate`, `keep_messages`,
                                          `context_budget`
  What the model is told about cards ... STATE_RULES (keep it in sync with `_observe`)
"""
from __future__ import annotations

import re

from .tool_loop import TOOL_RULES, ToolLoop

STATE_RULES = TOOL_RULES + """

STATE CARDS
Every tool result is a state card, not a terminal transcript:
  [state]  the turn, its cost, and the files this cell changed
  [diff]   the exact hunks this cell changed; empty when nothing changed
  [stderr] and [stdout] are bounded: when the text is large you get the head, the tail, the error lines and a handle
  [result] the last expression, clipped
  [check]  the project's cheap verification result after a change, when the workspace defines one

Read the diff after a mutation. changes(), diff() and undo() are still available for the whole session. Large output is never silently lost: the card names the _OBS[n] handle that holds it, and print(_OBS[n]['stdout'][a:b]) pages it.
"""

_ERROR_LINE = re.compile(r"error|fail|✗|×|exception|panic|assert", re.IGNORECASE)


def clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n...[+{len(text) - limit} chars]"


def digest(text: str, budget: int, handle: str) -> str:
    if not text or len(text) <= budget:
        return text
    lines = [line if len(line) <= 400 else line[:400] + " …" for line in text.splitlines()]
    head_count = max(8, budget // 120)
    tail_count = max(6, budget // 300)
    head = "\n".join(lines[:head_count])
    tail = "\n".join(lines[-tail_count:])
    errors = [line.rstrip() for line in lines if _ERROR_LINE.search(line)][:6]
    parts = [head]
    if errors:
        parts.append("error lines:\n" + "\n".join(errors))
    parts.append(
        f"...[{len(lines)} lines, {len(text)} chars; full text is {handle}; page it with "
        f"print({handle}['stdout'][a:b])]..."
    )
    parts.append(tail)
    return "\n".join(parts)


class StateLoop(ToolLoop):
    def __init__(
        self,
        *args,
        output_budget: int = 12_000,
        context_budget: int = 891_289,
        keep_messages: int = 20,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.output_budget = output_budget
        self.context_budget = context_budget
        self.keep_messages = keep_messages
        self.last_prompt_tokens = 0

    def _observe(self, cell, steps: int) -> str:
        parts = [f"[state] turn={steps} elapsed={cell.duration_ms}ms prompt_tokens={self.last_prompt_tokens}"]
        if cell.changed:
            parts.append(f"[state] changed: {', '.join(cell.changed)}")
        if cell.timeout:
            parts.append("[state] timeout: cell interrupted; namespace and variables are intact")
        if cell.delta:
            parts.append("[diff]\n" + clip(cell.delta, 6000))
        if self.last_check is not None:
            if self.last_check.ok:
                parts.append(f"[check] {self.last_check.cmd}: passed")
            else:
                parts.append("[check] failing\n" + clip(self.last_check.text(1200), 1500))
        handle = f"_OBS[{cell.obs_index}]" if cell.obs_index is not None else "_OBS[-1]"
        if cell.stderr:
            parts.append("[stderr]\n" + clip(digest(cell.stderr, self.output_budget, handle), self.output_budget))
        if cell.stdout:
            parts.append("[stdout]\n" + clip(digest(cell.stdout, self.output_budget, handle), self.output_budget))
        if cell.result is not None:
            parts.append("[result] " + clip(cell.result, 1500))
        return "\n".join(parts)

    def _compact(self, messages: list[dict]) -> None:
        if len(messages) < 8 or self.last_prompt_tokens < self.context_budget:
            return
        first = next((i for i, message in enumerate(messages)
                      if message.get("role") == "assistant" and message.get("tool_calls")), 2)
        head = messages[:first]
        body = messages[first:]
        cut = max(0, len(body) - self.keep_messages)
        while cut < len(body) and not (body[cut].get("role") == "assistant" and body[cut].get("tool_calls")):
            cut += 1
        dropped = body[:cut]
        if not dropped:
            return
        changed = ", ".join(sorted(self.changed)) or "(none)"
        summary = {
            "role": "user",
            "content": (
                f"[history] {len(dropped)} earlier messages were folded to keep the context bounded; {self.compactions + 1} fold(s). "
                f"Files changed this session: {changed}. "
                "The workspace is authoritative: read(), grep(), diff() and changes() return the current state. "
                "Do not re-derive what earlier turns already established."
            ),
        }
        messages[:] = head + [summary] + body[cut:]
        self.compactions += 1
