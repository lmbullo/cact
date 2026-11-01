"""StateLoop: ToolLoop + state-card observations. The production mode (`CODE_TUI_MODE=state`,
`--mode state`), and the default in config.build.

WHAT IT IS
  Same action space as ToolLoop (one `python` tool), but a cell's result is rendered as a
  state card instead of a transcript:

    [state]  turn, elapsed, prompt tokens, and the files this cell changed
    [diff]   the exact hunks this cell changed (empty when nothing changed)
    [stderr] / [stdout]  digested: head, error lines, tail, plus an _OBS[n] handle
    [result] the last expression, clipped

  Plus two ledgers and a fold:
    coverage  every read() records the line ranges it delivered; the card prints
              `[read] path ranges/total`, and final_answer is rejected (UNREAD) when it
              cites a file whose delivered ranges leave holes.
    fold      when the provider's real prompt count crosses `context_budget`, old turns are
              replaced by one regenerated `[session state]` message built from the journal
              (changed files, their diffs, coverage, check results), not a summary.

ENTRY POINTS
  STATE_RULES = TOOL_RULES + the state-card contract (what the model is told)
  clip(text, limit)                  bounded text
  digest(text, budget, handle)       head + error lines + "[n lines, m chars; full text is
                                     _OBS[i]" + tail
  StateLoop(..., output_budget, context_budget, keep_messages)
    ._observe(cell, steps) -> str    the card
    ._compact(messages)              fold old turns into a [history] message; gated on
                                     `last_prompt_tokens`, the provider's real count
    health= / last_check             the cheap check rendered as the `[check]` line

INVARIANTS
  - Output is never silently dropped: the card always names the `_OBS[n]` entry holding the
    full text, and the kernel keeps `_OBS` in the namespace so `print(_OBS[n]['stdout'])`
    works. That is why the handle, not the text, is the contract.
  - `_compact` cuts only on an assistant tool-call boundary, so no tool result is orphaned
    from its call.
  - The `[state] changed:` line and `[diff]` both come from `Cell.changed` / `Cell.delta`,
    which the kernel's journal produces (kernel.py::_commit).
  - `[check]` is ToolLoop's `last_check` (the `health` plan's cheap subset, refreshed after a
    mutating cell at most every 20s); this class only renders it.
  - Compaction triggers on the real prompt token count at `context_budget` (config sets 85%
    of the 1M window), never on a character estimate; it cuts only on a tool-call boundary.

WHERE TO CHANGE
  Card layout / error-line selection ... `_observe`, `digest`, `_ERROR_LINE`, `clip`
  Compaction policy ..................... `_compact`, `keep_messages`, `context_budget`
                                          (config.COMPACT_AT / COMPACT_AT_TOKENS)
  What the model is told about cards ... STATE_RULES (keep it in sync with `_observe`)
"""
from __future__ import annotations

import re
from pathlib import Path

from .tool_loop import TOOL_RULES, ToolLoop

STATE_RULES = TOOL_RULES + """

STATE CARDS
Every tool result is a state card, not a terminal transcript:
  [state]  the turn, its cost, and the files this cell changed
  [diff]   the exact hunks this cell changed; empty when nothing changed
  [stderr] and [stdout] are bounded: when the text is large you get the head, the tail, the error lines and a handle
  [result] the last expression, clipped
  [read]   the file ranges this cell delivered; anything past them is not in your context
  [check]  the project's cheap verification result after a change, when the workspace defines one

Read the diff after a mutation. A file is only "read" up to the ranges shown by [read]; citing a file whose ranges leave holes is rejected at finish, so page the rest with read(path, offset=next). changes(), diff() and undo() are still available for the whole session. Large output is never silently lost: the card names the _OBS[n] handle that holds it, and print(_OBS[n]['stdout'][a:b]) pages it.
"""

_ERROR_LINE = re.compile(r"error|fail|✗|×|exception|panic|assert", re.IGNORECASE)
_CITED = re.compile(r"`([^`\n]+)`")
_NUMBERED = re.compile(r"^\s*\d+\| ?(.*)$")
_PLAIN_PATH = re.compile(r"(?<![\w/.-])([\w.-]+(?:/[\w.-]+)*\.(?:py|ts|tsx|js|md|json|csv|txt|toml|yaml|yml))")
_READ_GATE_MAX_LINES = 4000


def merge_ranges(ranges) -> list[list[int]]:
    out: list[list[int]] = []
    for a, b in sorted((max(1, int(a)), int(b)) for a, b in ranges if int(b) >= int(a)):
        if out and a <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def gaps(ranges, total: int) -> list[list[int]]:
    merged = merge_ranges(ranges)
    out: list[list[int]] = []
    cursor = 1
    for a, b in merged:
        if a > cursor:
            out.append([cursor, min(total, a - 1)])
        cursor = max(cursor, b + 1)
    if total and cursor <= total:
        out.append([cursor, total])
    return [item for item in out if item[1] >= item[0]]


def covered(ranges) -> int:
    return sum(b - a + 1 for a, b in merge_ranges(ranges))


def split_delta(delta: str) -> dict[str, str]:
    out: dict[str, list[str]] = {}
    current = None
    for line in (delta or "").splitlines():
        if line.startswith("+++ b/"):
            current = line[6:].strip()
            out.setdefault(current, [])
        elif line.startswith("--- a/") or line.startswith("diff --git"):
            continue
        elif current is not None:
            out[current].append(line)
    return {path: "\n".join(lines) for path, lines in out.items()}


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
        read_gate: bool = True,
        **kwargs,
    ) -> None:
        super().__init__(*args, read_gate=read_gate, **kwargs)
        self.output_budget = output_budget
        self.context_budget = context_budget
        self.keep_messages = keep_messages
        self.last_prompt_tokens = 0
        self.coverage: dict[str, dict] = {}
        self.deltas: dict[str, str] = {}

    def _note_cell(self, cell) -> None:
        for path, block in split_delta(cell.delta or "").items():
            self.deltas[path] = block

    def _displayed(self, cell, body: str) -> dict[str, list[list[int]]]:
        out: dict[str, list[list[int]]] = {}
        root = str(getattr(self.kernel, "root", ""))
        for path, entry in (cell.reads or {}).items():
            try:
                target = Path(root, path)
                if target.stat().st_size > 2_000_000:
                    continue
                lines = target.read_text(errors="replace").splitlines()
            except OSError:
                continue
            kept: list[list[int]] = []
            run: list[int] = []
            total = int(entry.get("total") or 0)
            seen = set()
            for line in body.splitlines():
                match = _NUMBERED.match(line)
                seen.add((match.group(1) if match else line).rstrip())
            for a, b in entry.get("ranges") or []:
                for number in range(int(a), min(int(b), len(lines)) + 1):
                    text = lines[number - 1]
                    probe = text.rstrip()
                    shown = bool(probe.strip()) and (probe in seen or (len(probe) > 400 and probe[:400] in seen))
                    if shown:
                        run.append(number)
                    elif run:
                        kept.append([run[0], run[-1]])
                        run = []
            if run:
                kept.append([run[0], run[-1]])
            if kept:
                out[path] = kept
                current = self.coverage.setdefault(path, {"ranges": [], "total": 0})
                current["ranges"].extend(kept)
                current["total"] = total or current["total"]
        return out

    def _read_rejection(self, final: str) -> str | None:
        if not self.coverage:
            return None
        root = str(getattr(self.kernel, "root", ""))
        cited = []
        candidates = _CITED.findall(final or "") + _PLAIN_PATH.findall(final or "")
        for raw in candidates:
            path = raw.strip().strip(".,;:)").lstrip("./")
            if path in self.coverage:
                if path not in cited:
                    cited.append(path)
                continue
            if root and Path(root, path).exists() and path not in cited:
                cited.append(path)
        seen = set()
        for path in cited:
            if path in seen:
                continue
            seen.add(path)
            entry = self.coverage.get(path)
            if not entry:
                continue
            total = int(entry.get("total") or 0)
            if not total or total > _READ_GATE_MAX_LINES:
                continue
            missing = gaps(entry.get("ranges") or [], total)
            if missing:
                a, b = missing[0]
                return (f"[observation] final_answer rejected: it cites `{path}` but only "
                        f"{covered(entry.get('ranges') or [])} of {total} lines were delivered; "
                        f"unread {a}-{b}. Read it with read(path={path!r}, offset={a}), "
                        "or drop the citation and do not claim what you did not read.")
        return None

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
        body: list[str] = []
        if cell.stderr:
            body.append("[stderr]\n" + clip(digest(cell.stderr, self.output_budget, handle), self.output_budget))
        if cell.stdout:
            body.append("[stdout]\n" + clip(digest(cell.stdout, self.output_budget, handle), self.output_budget))
        if cell.result is not None:
            body.append("[result] " + clip(cell.result, 1500))
        visible = "\n".join(body)
        displayed = self._displayed(cell, visible)
        for path, ranges in sorted(displayed.items()):
            session = self.coverage.get(path) or {}
            total = int(session.get("total") or 0)
            shown = ", ".join(f"{a}-{b}" for a, b in merge_ranges(ranges))
            missing = gaps(session.get("ranges") or [], total)
            tail = f" (next {missing[0][0]})" if missing else ""
            parts.append(f"[read] {path} {shown}/{total}{tail}")
        parts.extend(body)
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
        lines = [f"[session state] {len(dropped)} earlier messages were folded; the workspace is authoritative. "
                 f"{self.compactions + 1} fold(s). Do not re-derive what earlier turns established."]
        if self.changed:
            lines.append("changed: " + ", ".join(sorted(self.changed)))
        diffs = [self.deltas[path] for path in sorted(self.deltas) if self.deltas[path]]
        if diffs:
            lines.append("[diff] current change set\n" + clip("\n".join(diffs), 6000))
        if self.coverage:
            reads = []
            for path in sorted(self.coverage)[:16]:
                entry = self.coverage[path]
                total = int(entry.get("total") or 0)
                ranges = entry.get("ranges") or []
                missing = gaps(ranges, total)
                suffix = f" unread {missing[0][0]}-{missing[-1][1]}" if missing else ""
                reads.append(f"{path} {covered(ranges)}/{total}{suffix}")
            lines.append("reads: " + "; ".join(reads))
        if self.last_check is not None:
            lines.append(f"check: {self.last_check.cmd}: {'passed' if self.last_check.ok else 'failed'}")
        summary = {"role": "user", "content": "\n".join(lines)}
        messages[:] = head + [summary] + body[cut:]
        self.compactions += 1