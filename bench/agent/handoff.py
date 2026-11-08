"""handoff: the structured finish answer and the compact block it renders to.

WHAT IT IS
  `final_answer(value)` accepts either the old plain string or a dict:
  `{summary, changed, gates, deferred, untested}`. This module owns the one rendering and
  normalization used by both halves:
    fields(value)   the normalized dict (empty for a plain string), with every field clipped
    render(value)   a short block: summary first, one line per non-empty section
    split(value)    `(rendered_text, fields)`, the pair a Cell carries

ENTRY POINTS
  render(value)   str       the text a caller sees, unchanged when value is already a string
  fields(value)   dict      summary/changed/gates/deferred/untested, clipped to fixed limits
  split(value)    tuple     `(render(value), fields(value))` for Cell.from_reply

INVARIANTS
  - A plain string is never reformatted or validated: existing callers stay byte-identical.
  - Nothing is dropped silently: `changed` is capped at 12 paths and says `+N more`; every
    other list is capped too and the omitted count is named in the line.
  - The block is bounded (summary 600 chars, each item 160, each section line 700) because it
    is also the TUI's final card and the next task's `[workspace history]` source.

WHERE TO CHANGE
  A new field in the dict ............... FIELDS, fields(), render()
  A cap or a clip width ................. SUMMARY_CHARS / ITEM_CHARS / CHANGED_CAP / LINE_CHARS
  The kernel's `final_answer` ........... kernel/driver.py (it passes a dict through untouched)
"""
from __future__ import annotations

from typing import Any

FIELDS = ("summary", "changed", "gates", "deferred", "untested")
SUMMARY_CHARS = 600
ITEM_CHARS = 160
CHANGED_CAP = 12
GATE_CAP = 8
LIST_CAP = 6
LINE_CHARS = 700


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
    """The `gates` field as `{name, ok, exit}` dicts, tolerating a string or a bare list."""
    if not isinstance(value, (list, tuple)):
        return []
    gates = []
    for item in value:
        if isinstance(item, dict):
            gates.append({
                "name": _text(item.get("name"), 60),
                "ok": bool(item.get("ok")),
                "exit": item.get("exit"),
            })
        else:
            gates.append({"name": _text(item, 60), "ok": False, "exit": None})
    return gates


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
        _line("changed", data["changed"], CHANGED_CAP),
        _gate_line(data["gates"]),
        _line("deferred", data["deferred"], LIST_CAP),
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
