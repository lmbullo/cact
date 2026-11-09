"""state_text: card text: clipping, digesting, the [data] block: one card's size policy.

WHAT IT IS
  A state card is bounded on purpose. Process output -> digest: head, error lines, tail naming
  the _OBS[n] entry holding the whole text. A structured [data] value is clipped per key, never
  dropped with the block. This module decides only how much survives and where the rest is;
  state_card.py decides what the card says.

ENTRY POINTS
  clip(text, limit) -> str        head of text + explicit `[+N chars]` marker
  compact_stream(text) -> str     ANSI stripped; a run of identical lines capped at two
  clip_card(text, budget, handle) -> (text, clipped)   whole card, or head + pinned lines
  digest_card(text, budget, handle, errors=False) -> (text, digested)
  digest(text, budget, handle) -> str                  digest_card's text only
  data_block(data) -> (shown, clipped_keys)            [data] object, per key clipped
  DATA_LINES, DATA_CHARS ... per-value clip, in lines and chars

INVARIANTS
  loss always named: clipped value carries `[+N chars]`; digested stream names the _OBS[n] handle
  and the elided line range; a clipped card names itself in its marker.
  a budget is a delivery policy, not permission to lose a refusal: clip_card re-appends every
  [error]/[check] line the head cut dropped; digest_card(errors=True) keeps stderr error lines.
  pure text in, text out: no file, no cell, no kernel; same input -> same output.
  [data] never drops a field: data_block clips a value and names its key in one `[data] clipped:`
  note, so closest, valid and argv/results still reach the model.
  digest_card's second value = the only honest coverage signal: undigested stream reached the
  model whole; digested kept only head, error lines, tail.

WHERE TO CHANGE
  elision notice / head-tail split .... digest_card
  card marker / pinned lines .......... clip_card
  per-value clip ...................... DATA_LINES, DATA_CHARS, _data_value
  [data] field policy ................. data_block
"""
from __future__ import annotations

import json
import re

DATA_LINES = 8
DATA_CHARS = 1000

_ERROR_LINE = re.compile(r"error|fail|✗|×|exception|panic|assert", re.IGNORECASE)
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def clip(text: str, limit: int) -> str:
    """`text` cut to `limit` chars with an explicit `[+N chars]` marker."""
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n...[+{len(text) - limit} chars]"


def compact_stream(text: str) -> str:
    """Strip ANSI and cap a run of identical lines at two; full text stays on `_OBS`."""
    if not text:
        return text
    lines = _ANSI.sub("", text).splitlines()
    out: list[str] = []
    previous = None
    run = 0
    for line in lines:
        if line == previous:
            run += 1
            if run > 2:
                continue
        else:
            previous = line
            run = 1
        out.append(line)
    return "\n".join(out)


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
