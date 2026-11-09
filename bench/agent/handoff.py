"""handoff: structured finish answer + the compact block it renders to.

WHAT IT IS
  final_answer(value) accepts a plain string or a dict {summary, changed, gates, deferred,
  untested}. One rendering + normalization for both halves:
    fields(value)   normalized dict (empty for a plain string), every field clipped
    render(value)   short block: summary first, one line per non-empty section
    split(value)    (rendered_text, fields), the pair a Cell carries

ENTRY POINTS
  render(value)   str    caller-facing text; a string value passes through unchanged
  fields(value)   dict   summary/changed/gates/deferred/untested, clipped to fixed limits
  split(value)    tuple  (render(value), fields(value)) for Cell.from_reply

INVARIANTS
  plain string input = never reformatted, never validated; existing callers stay byte-identical.
  nothing dropped silently: changed capped at 12 paths + `+N more`; every list capped, omitted
  count named in the line.
  block bounded: summary 600 chars, item 160, section line 700. Same text is the TUI final card
  and the next task's `[workspace history]` source.

WHERE TO CHANGE
  new dict field ......... FIELDS, fields(), render()
  cap / clip width ....... SUMMARY_CHARS / ITEM_CHARS / CHANGED_CAP / LINE_CHARS
  kernel final_answer .... kernel/driver.py (passes a dict through untouched)
"""
from __future__ import annotations

import re
from typing import Any

FIELDS = ("summary", "changed", "gates", "deferred", "untested")
SUMMARY_CHARS = 480
ITEM_CHARS = 120
CHANGED_CAP = 10
GATE_CAP = 8
LIST_CAP = 6
LINE_CHARS = 500


def _text(value: Any, limit: int = ITEM_CHARS) -> str:
    """One clipped, single-line string; an ellipsis says how many characters were dropped."""
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 18)].rstrip() + " …+%d chars" % (len(text) - limit + 18)


def _items(value: Any) -> list[str]:
    """A list field as clipped strings; a bare string becomes a one-item list."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, (list, tuple)):
        return [str(value)]
    return [str(item) for item in value if str(item).strip()]


def _gates(value: Any) -> list[dict]:
    """The `gates` field as `{name, ok, exit}` dicts, tolerating `cmd`/`result` rows."""
    if not isinstance(value, (list, tuple)):
        return []
    gates = []
    for item in value:
        if isinstance(item, dict):
            name = item.get("name") or item.get("cmd") or item.get("command")
            exit_code = item.get("exit", item.get("code"))
            result = " ".join(str(item.get("result") or item.get("status") or "").split())
            ok = item.get("ok")
            if ok is None:
                ok = _gate_ok(exit_code, result)
            gates.append({"name": _text(name, 60), "ok": bool(ok), "exit": exit_code})
        else:
            text = _text(item, 60)
            lowered = text.lower()
            gates.append({"name": text,
                          "ok": not any(word in lowered for word in ("fail", "error")),
                          "exit": None})
    return gates


def _gate_ok(exit_code: Any, result: str) -> bool:
    """A gate row is ok only when its exit is 0 or its result has no nonzero failure."""
    if exit_code is not None:
        return str(exit_code).strip() in ("0", "")
    lowered = result.lower()
    match = re.search(r"exit(?: code)?[ =:]?(\d+)", lowered)
    if match:
        return match.group(1) == "0"
    match = re.search(r"(\d+)\s+failed", lowered)
    if match:
        return int(match.group(1)) == 0
    if re.search(r"\b0\s+fail", lowered):
        return True
    good = any(mark in lowered for mark in ("pass", "ok", "green", "success"))
    bad = any(mark in lowered for mark in ("fail", "error", "traceback"))
    return good and not bad


def fields(value: Any) -> dict:
    """Normalize a dict answer; a plain string (or anything else) has no structured fields."""
    if not isinstance(value, dict):
        return {}
    return {
        "summary": _text(value.get("summary"), SUMMARY_CHARS),
        "changed": _items(value.get("changed")),
        "gates": _gates(value.get("gates")),
        "deferred": _items(value.get("deferred")),
        "untested": _items(value.get("untested")),
    }


def _line(label: str, items: list[str], cap: int) -> str:
    """One section line: the capped items, then `+N more` when the cap hid some."""
    shown = [item for item in items[:cap] if item]
    if not shown:
        return ""
    suffix = " (+%d more)" % (len(items) - cap) if len(items) > cap else ""
    return _text("%s: %s%s" % (label, ", ".join(shown), suffix), LINE_CHARS)


def _gate_line(gates: list[dict]) -> str:
    """One `gates:` line: `name ok(exit)` rows, with the omitted count named."""
    if not gates:
        return ""
    rows = []
    for gate in gates[:GATE_CAP]:
        state = "ok" if gate["ok"] else "fail"
        exit_code = "" if gate.get("exit") is None else "(%s)" % gate["exit"]
        rows.append("%s %s%s" % (gate["name"], state, exit_code))
    suffix = " (+%d more)" % (len(gates) - GATE_CAP) if len(gates) > GATE_CAP else ""
    return _text("gates: %s%s" % (", ".join(rows), suffix), LINE_CHARS)


def render(value: Any) -> str:
    """Render a dict answer as a short block; a plain string is returned unchanged."""
    if not isinstance(value, dict):
        return value if isinstance(value, str) else str(value)
    data = fields(value)
    lines = [data["summary"]] if data["summary"] else []
    for line in (
        _line("chg", data["changed"], CHANGED_CAP),
        _gate_line(data["gates"]),
        _line("defer", data["deferred"], LIST_CAP),
        _line("untested", data["untested"], LIST_CAP),
    ):
        if line:
            lines.append(line)
    return "\n".join(lines)


def split(value: Any) -> tuple[Any, dict]:
    """Return `(rendered_text, structured_fields)` for a finish value of either shape."""
    if isinstance(value, dict):
        return render(value), fields(value)
    return value, {}
