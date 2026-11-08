"""StateLoop: ToolLoop + state-card observations. The production mode (`CODE_TUI_MODE=state`,
`--mode state`), and the default in config.build.

WHAT IT IS
  Same action space as ToolLoop (the typed verbs plus the `python` escape hatch), but a
  cell's result is rendered as a state card instead of a transcript:

    [state]  turn, elapsed, prompt tokens, and the files this cell changed
    [observe]/[page]  a typed verb's envelope: op/status, shown/total/next/unit/has_more/truncated/complete
    [data]   the typed result's structured fields (bulk text stays on stdout); a value that
              is too large is clipped per key, with one `[data] clipped: <keys>` note
    [note] / [error]  explicit notes and the structured rejection
    [stderr] / [stdout]  for python/exec only: head, error lines, tail, plus an _OBS[n] handle
    [result] the last expression, clipped (python only; a typed observation is already data)

  Plus a ledger and a fold:
    coverage  every read() records the line ranges it delivered in `Cell.reads`; the renderer
              turns that plus the text it actually rendered into `(card, visible ranges)`, and
              the card prints `[read] path ranges/total (unread: holes)`. This is
              guidance, not a finish gate: reads through run()/open() are equally valid and are
              not tracked, and the loop never opens a workspace file to check.
    fold      when the provider's real prompt count crosses `context_budget`, old turns are
              replaced by one regenerated `[session state]` message built from the journal
              (changed files, their diffs, coverage, check results), not a summary.

ENTRY POINTS
  STATE_RULES = TOOL_RULES + the state-card contract (what the model is told); `[plan]` is
                the durable RunState plan, set by the `plan` tool
  clip(text, limit)                  bounded text
  digest(text, budget, handle)       head + error lines + "[n lines, m chars; full text is
                                     _OBS[i]" + tail
  digest_card(text, budget, handle)  the same, plus whether the text was digested at all
  numbered_lines(text)               the line numbers a numbered read block carries (pure parse)
  numbered_lines_by_path(text, paths)  the same, split by the `# path` headers a batched read emits
  delivered_ranges(reads, body, digested)
                                     {path: [[a, b], ...]} this card actually delivered
  render_observation(cell, steps, prompt_tokens, check, output_budget, coverage)
                                     -> (card text, visible read ranges)   pure; no I/O
  StateLoop(..., output_budget, context_budget, keep_messages)
    ._observe(cell, steps) -> str    render_observation + fold the ranges into `coverage`
    ._compact(messages)              fold old turns into a [session state] message; gated on
                                     `last_prompt_tokens`, the provider's real count
    verifier / last_check            the cheap check rendered as the `[check]` line

INVARIANTS
  - A typed observation is delivered whole. `Cell.envelope` is the normalized contract
    (`bench/agent/envelope.py`); the card renders `page`/`data` and never applies the output
    digest to it, because the verb already paged. Only python/exec output is digested, and
    that digest always names the `_OBS[n]` handle it left.
  - Structured `data` is never dropped by a size gate. Every field is rendered; a large value
    is clipped to about `DATA_LINES` lines and named in one `[data] clipped:` note, so
    `closest`/`context`, `dirs_matched`, `argv`/`results` and a refusal's `signature`/`valid`
    reach the model.
  - Coverage is data, never a guess. The loop opens no file and inverse-parses nothing: a
    verbatim typed card delivered exactly `Cell.reads`; a digested python/exec card delivered
    only the numbered lines `render_observation` kept. A raw read is never guessed,
    and a digested batch read is split by its `# path` headers so one file's line numbers are
    never credited to another.
  - A restart is never silent: `Cell.restarted` puts an explicit `[state] kernel restarted`
    line in the card, on top of the kernel's stderr message, so a lost namespace cannot look
    like a normal cell.
  - Output is never silently dropped: the card always names the `_OBS[n]` entry holding the
    full text, and `page(handle)` reads a slice of it from the kernel. That is why the handle,
    not the text, is the contract. The kernel persists `_OBS` across a restart, so `page()`
    keeps working when a cell kills the kernel.
  - `_compact` cuts only on an assistant tool-call boundary, so no tool result is orphaned
    from its call.
  - The `[state] changed:` line and `[diff]` both come from the kernel's journal
    (kernel/prelude.py::_commit): `Cell.changed` lists the paths, `Cell.deltas` carries each one's
    unified diff, and `Cell.delta` is only their joined text for the card. `_note_cell`
    reads the structured `deltas`; it never re-parses the text.
  - `[check]` is ToolLoop's `last_check` (the Verifier's cheap subset, refreshed after a
    mutating cell at most every 20s); this class renders one line: `pass`, or `<spec> :
    <first failing line>` with the full output named as the `_OBS[n]` handle ToolLoop queued.
  - Compaction triggers on the real prompt token count at `context_budget` (config sets 85%
    of the 1M window), never on a character estimate; it cuts only on a tool-call boundary.

WHERE TO CHANGE
  Card layout / error-line selection ... `render_observation`, `digest_card`, `_ERROR_LINE`, `clip`
  Read ranges / what counts as read ..... `delivered_ranges`, `numbered_lines`, `_observe`
                                          (keep them data; never re-read the file)
  Compaction policy ..................... `_compact`, `keep_messages`, `context_budget`
                                          (config.COMPACT_AT / COMPACT_AT_TOKENS)
  What the model is told about cards ... STATE_RULES (keep it in sync with `_observe`)
"""
from __future__ import annotations

import json
import re

from .tool_loop import TOOL_RULES, ToolLoop

STATE_RULES = TOOL_RULES + """

STATE CARDS
Every result is a state card, not a transcript. `[phase]` is orient/plan/act/verify/report; `task()` returns the durable state. `[plan]` is the durable plan set by plan(items=[...]); mark progress with plan(done=[...]). Read [page] before [data]; `next` is replayable, `complete`/`has_more`/`truncated` are the paging contract. [data] carries the typed fields, [diff] the mutation, [check] the project's verdict, [read] the delivered ranges. [stderr]/[stdout]/[result] are bounded and name the _OBS[n] handle; page(handle) reads a slice. Every `ok:false` carries error.code/message/use. Coverage is guidance, not a finish gate."""

DATA_LINES = 10
DATA_CHARS = 1200
CARD_BUDGET = 6_000

_ERROR_LINE = re.compile(r"error|fail|✗|×|exception|panic|assert", re.IGNORECASE)
_NUMBERED = re.compile(r"^\s*(\d+)\| ?(.*)$")
# Typed verbs already page and name their own loss. The card renders their observation
# verbatim; only process/python output is digested, because only it has no paging contract.
_VERBATIM_TOOLS = frozenset({"read", "search", "map", "outline", "guide", "recall", "page",
                            "session_diff", "git", "checks", "status"})


def merge_ranges(ranges) -> list[list[int]]:
    """Overlapping/adjacent (a, b) ranges -> sorted disjoint [[a, b], ...]."""
    out: list[list[int]] = []
    for a, b in sorted((max(1, int(a)), int(b)) for a, b in ranges if int(b) >= int(a)):
        if out and a <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def gaps(ranges, total: int) -> list[list[int]]:
    """The line ranges of 1..total that `ranges` does not cover."""
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
    """How many distinct lines the merged ranges cover."""
    return sum(b - a + 1 for a, b in merge_ranges(ranges))


def clip(text: str, limit: int) -> str:
    """`text` cut to `limit` chars with an explicit `[+N chars]` marker."""
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n...[+{len(text) - limit} chars]"


def clip_card(text: str, budget: int, handle: str) -> tuple[str, bool]:
    """`(text, was clipped)`: a whole card while it fits, else its head + an _OBS pointer.

    An `[error]` or `[check]` line is never hidden by the head cut: every such line not
    already in the head is appended after the marker, because a budget is a delivery policy,
    not permission to lose a refusal or a verdict.
    """
    if len(text) <= budget:
        return text, False
    marker = "[card clipped: %d chars, full text: %s]" % (len(text), handle)
    keep = max(0, int(budget) - len(marker) - 1)
    head = text[:keep]
    kept = set(head.splitlines())
    pinned = [line for line in text.splitlines()
              if line.startswith(("[error]", "[check]")) and line not in kept]
    out = head + ("\n" if head and not head.endswith("\n") else "") + marker
    if pinned:
        out += "\n" + "\n".join(pinned)
    return out, True


def digest_card(text: str, budget: int, handle: str, errors: bool = False) -> tuple[str, bool]:
    """`(rendered text, was it digested at all)`.

    The flag is the only honest signal for coverage: an undigested stream reached the model
    whole, a digested one kept only its head, error lines and tail.
    """
    if not text or len(text) <= budget:
        return text, False
    lines = text.splitlines()
    head_count = max(8, budget // 120)
    tail_count = max(6, budget // 300)
    head = "\n".join(lines[:head_count])
    tail = "\n".join(lines[-tail_count:])
    error_lines = [line.rstrip() for line in lines if _ERROR_LINE.search(line)][:6]
    parts = [head]
    if errors and error_lines:
        parts.append("error lines:\n" + "\n".join(error_lines))
    first_elided = head_count + 1
    last_elided = max(first_elided - 1, len(lines) - tail_count)
    parts.append(
        f"...[elided {first_elided}-{last_elided} of {len(lines)} lines, {len(text)} chars; "
        f"full text is {handle}; page it with print({handle}['stdout'][a:b])]..."
    )
    parts.append(tail)
    return "\n".join(parts), True


def digest(text: str, budget: int, handle: str) -> str:
    """digest_card's text only: head + elision notice naming `handle` + tail."""
    return digest_card(text, budget, handle)[0]


def _data_value(value, limit: int = DATA_CHARS, lines: int = DATA_LINES) -> tuple[str, bool]:
    """One `[data]` value as text, clipped to about `lines` lines / `limit` chars.

    A dict/list that serializes long is pretty-printed first, so a `dirs_matched` map or an
    `edit.context` region is clipped by lines instead of hiding in one enormous JSON line.
    """
    try:
        text = json.dumps(value, sort_keys=True, default=repr)
        if isinstance(value, (dict, list)) and len(text) > 240:
            text = json.dumps(value, sort_keys=True, indent=1, default=repr)
    except (TypeError, ValueError):
        text = repr(value)
    chunks = text.splitlines()
    if len(chunks) <= lines and len(text) <= limit:
        return text, False
    head = "\n".join(chunks[:lines])
    if len(head) > limit:
        head = head[:limit]
    return head + f" ...[+{max(0, len(text) - len(head))} chars]", True


def data_block(data: dict) -> tuple[dict, list[str]]:
    """The `[data]` object: every non-duplicated field, each value clipped per key.

    `normalize` already keeps bulk payloads out; what is left is structured facts the model
    needs. There is no 600-char gate: a value is clipped (and its key named in the one
    `[data] clipped:` note) instead of disappearing with the whole block.
    """
    shown: dict = {}
    clipped: list[str] = []
    for key, value in data.items():
        if key in ("by_file", "sections"):
            continue
        text, cut = _data_value(value)
        shown[key] = text if cut else value
        if cut:
            clipped.append(key)
    return shown, clipped


def numbered_lines(text: str) -> set[int]:
    """The line numbers the numbered read format (`f"{n:>6}| line"`) puts in `text`.

    A deterministic parse of what was rendered: no file is opened and no text is compared
    against one, so a digested card can only claim the lines it actually kept.
    """
    out: set[int] = set()
    for line in (text or "").splitlines():
        match = _NUMBERED.match(line)
        if match:
            out.add(int(match.group(1)))
    return out


def numbered_lines_by_path(text: str, paths) -> dict[str, set[int]]:
    """`{path: line numbers}` for a digested batch read.

    `read([a, b])` joins the files with `# path` headers. Without this split, a number kept
    for one file would be credited to every file with a line at that number; with it, a
    digested batch card can only claim what each section actually kept.
    """
    wanted = {str(path) for path in (paths or ())}
    out: dict[str, set[int]] = {}
    current: str | None = None
    for line in (text or "").splitlines():
        if line.startswith("# ") and line[2:].strip() in wanted:
            current = line[2:].strip()
            out.setdefault(current, set())
            continue
        match = _NUMBERED.match(line)
        if match and current is not None:
            out[current].add(int(match.group(1)))
    return out


def delivered_ranges(reads, body: str = "", digested: bool = False) -> dict[str, list[list[int]]]:
    """`{path: [[a, b], ...]}`: the ranges this card actually delivered to the model.

    Undigested, that is exactly what `read()` recorded in `Cell.reads`. Digested, it is only the
    numbered lines still present in `body`; a batch read is split by its `# path` headers so a
    line number is never credited to the wrong file. A raw (non-numbered) read is never guessed.
    """
    kept = numbered_lines(body) if digested and len(reads or {}) <= 1 else None
    by_path = numbered_lines_by_path(body, reads) if digested and kept is None else {}
    out: dict[str, list[list[int]]] = {}
    for path, entry in (reads or {}).items():
        ranges: list[list[int]] = []
        path_kept = kept if kept is not None else by_path.get(path, set())
        for a, b in entry.get("ranges") or []:
            a, b = max(1, int(a)), int(b)
            if not digested:
                ranges.append([a, b])
            else:
                ranges.extend([n, n] for n in range(a, b + 1) if n in path_kept)
        merged = merge_ranges(ranges)
        if merged:
            out[path] = merged
    return out


def render_observation(cell, steps: int, prompt_tokens: int = 0, check=None,
                       output_budget: int = 12_000,
                       coverage: dict | None = None,
                       check_handle: str | None = None,
                       plan: list[str] | None = None) -> tuple[str, dict[str, list[list[int]]]]:
    """A cell -> `(the state card, the read ranges the card made visible)`.

    Pure: it reads no workspace file and does not mutate the loop's coverage ledger. The ranges
    come from `Cell.reads` (exact, recorded by the kernel) and from the text rendered right here,
    so `[read] path a-b/total (next N)` and the `reads:` fold line cannot drift from the card.
    """
    parts = [f"[state] step={steps} elapsed={cell.duration_ms}ms prompt_tokens={prompt_tokens}"]
    parts.extend(plan or ())
    if getattr(cell, "tool", None) and not getattr(cell, "envelope", None):
        parts.append(f"[tool] {cell.tool}")
    if cell.changed:
        parts.append(f"[state] changed: {', '.join(cell.changed)}")
    if cell.timeout:
        parts.append("[state] timeout: cell interrupted; namespace and variables are intact")
    if cell.restarted:
        parts.append("[state] kernel restarted: the namespace was lost; variables and imports "
                     "are gone (earlier output survives in recall())")
    if cell.delta:
        parts.append("[diff]\n" + clip(cell.delta, 6000))
    if check is not None:
        if check.ok:
            parts.append("[check] pass")
        else:
            name = check.cmd.split(": ", 1)[0] if ": " in check.cmd else check.cmd
            line = "%s : %s" % (name, check.first_failure() or "failed")
            if check_handle:
                line += " (full: %s)" % check_handle
            parts.append("[check] " + clip(line, 240))
    handle = f"_OBS[{cell.obs_index}]" if cell.obs_index is not None else "_OBS[-1]"
    body: list[str] = []
    digested = False
    typed = bool(getattr(cell, "tool", None))
    verbatim = typed and cell.tool in _VERBATIM_TOOLS
    env = getattr(cell, "envelope", None)
    if typed and env:
        page = env.get("page")
        status = "error" if not env.get("ok", True) else "ok"
        parts.append(f"[observe] {cell.tool} {status} handle={handle}")
        if page:
            bits = [f"shown={page.get('shown')}", f"total={page.get('total')}"]
            if page.get("unit"):
                bits.append(f"unit={page['unit']}")
            if page.get("offset") is not None:
                bits.append(f"offset={page['offset']}")
            if page.get("next") is not None:
                bits.append(f"next={page['next']}")
            if (env.get("data") or {}).get("hits") is not None:
                bits.append(f"hits={(env.get('data') or {}).get('hits')}")
            if page.get("pointer"):
                bits.append("pointer=true")
            bits.append("has_more=" + ("true" if page.get("has_more") else "false"))
            bits.append("truncated=" + ("true" if page.get("truncated") else "false"))
            bits.append("complete=" + ("true" if page.get("complete") else "false"))
            parts.append("[page] " + " ".join(bits))
        for note in env.get("notes") or []:
            parts.append("[note] " + str(note))
        data = env.get("data") or {}
        # Structured-only facts (did_you_mean, include_unmatched, edit closest/context, a
        # map's dirs_matched, an exec argv/results, a git sha) have no stdout delivery.
        # Render every non-duplicated field; clip each value per key and name what was cut.
        if data:
            shown, clipped = data_block(data)
            if shown:
                try:
                    payload = json.dumps(shown, sort_keys=True, default=repr)
                except (TypeError, ValueError):
                    payload = repr(shown)
                parts.append("[data] " + payload)
                if clipped:
                    parts.append("[data] clipped: " + ", ".join(clipped))
        by_file = data.get("by_file")
        if cell.tool == "search" and by_file and page and not page.get("complete"):
            for path, info in list(by_file.items())[:6]:
                numbers = info.get("lines") or []
                ranges = []
                for number in numbers:
                    number = int(number)
                    if ranges and number <= ranges[-1][1] + 1:
                        ranges[-1][1] = number
                    else:
                        ranges.append([number, number])
                where = ", ".join("%d-%d" % (a, b) if a != b else str(a)
                                  for a, b in ranges[:6])
                if len(ranges) > 6:
                    where += " +%d more" % (len(ranges) - 6)
                total_hits = page.get("total") or 0
                remain = max(0, int(total_hits) - int(page.get("offset") or 0) - int(page.get("shown") or 0))
                where_text = where or "(none)"
                parts.append("[hits] %s: %s hit(s) at %s; page of %d, %d remain"
                             % (path, info.get("count"), where_text, total_hits, remain))
        if not env.get("ok", True) and env.get("error"):
            err = env["error"]
            code = err.get("code") or "error"
            message = err.get("message") or err.get("path") or ""
            hint = err.get("use")
            if not hint and code == "outside_root":
                hint = "typed editors are workspace-only; use exec(shell=...) for a scratch path"
            line = "[error] %s: %s: %s" % (env.get("kind") or "error", code, message)
            if hint:
                line += "; use " + str(hint)
            if err.get("signature"):
                line += "; signature=" + clip(str(err["signature"]), 300)
            if err.get("valid"):
                valid = err["valid"]
                if isinstance(valid, (list, tuple)):
                    valid = ", ".join(str(item) for item in valid)
                line += "; valid=" + clip(str(valid), 300)
            if err.get("argument"):
                line += "; argument=" + str(err["argument"])
            parts.append(line)
    if cell.stderr:
        if verbatim:
            body.append("[error]\n" + cell.stderr)
        else:
            rendered, cut = digest_card(cell.stderr, output_budget, handle, errors=True)
            digested = digested or cut
            body.append("[stderr]\n" + clip(rendered, output_budget))
    if cell.stdout:
        if verbatim:
            body.append("[data]\n" + cell.stdout)
            if env and env.get("page") and not env["page"].get("complete"):
                body.append("[more] page.next=%r; use it to continue" % (env["page"].get("next"),))
        else:
            rendered, cut = digest_card(cell.stdout, output_budget, handle)
            digested = digested or cut
            body.append("[stdout]\n" + clip(rendered, output_budget))
    if cell.result is not None and not typed:
        body.append("[result] " + clip(cell.result, 1500))
    visible = "\n".join(body)
    shown = delivered_ranges(cell.reads, visible, digested)
    for path, ranges in sorted(shown.items()):
        total = int((cell.reads.get(path) or {}).get("total") or 0)
        session = (coverage or {}).get(path) or {}
        missing = gaps(list(session.get("ranges") or []) + ranges, total)
        tail = ""
        if missing:
            if len(missing) <= 6:
                shown_gaps = ", ".join("%d-%d" % (a, b) for a, b in missing)
            else:
                shown_gaps = "%d ranges / %d lines" % (
                    len(missing), sum(b - a + 1 for a, b in missing))
            tail = f" (unread: {shown_gaps})"
        text = ", ".join(f"{a}-{b}" for a, b in merge_ranges(ranges))
        parts.append(f"[read] {path} {text}/{total}{tail}")
    parts.extend(body)
    return "\n".join(parts), shown


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
        lines.append(self.state.phase_line())
        lines.extend(self.state.plan_lines())
        if self.last_check is not None:
            lines.append(f"check: {self.last_check.cmd}: {'passed' if self.last_check.ok else 'failed'}")
        summary = {"role": "user", "content": "\n".join(lines)}
        messages[:] = head + [summary] + body[cut:]
        self.compactions += 1
