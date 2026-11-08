"""helpers: the pure functions the loops and cards depend on, with no process at all.

WHAT IT IS
  Reply parsing (```python fences, ANSWER:, prose), the state card digest, budget defaults,
  the mode enum, the LLM error type, the trace renderer, the runs summary verbs, the
  workspace helpers, and the generated tool/verb specs. No kernel child, no model, no fixture.

ENTRY POINTS
  run(check)   called by bench/smoke.py::main after suites

INVARIANTS
  - Everything here is pure or in-memory: a failure points at a helper's shape, never at the
    environment.
  - The verb/tool checks assert the *generated* surface: `python` is the escape hatch and is
    not generated as a typed tool; `exec` is typed and `run` is a python helper.

WHERE TO CHANGE
  The reply dialect ...................... reply.compile_reply (this file only scores it)
  The card digest ........................ state_loop.digest / digest_card
  A typed tool's spec .................... verbs.VERBS, tools.TYPE_TOOLS
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import bench.agent.config as config
import bench.agent.llm as llm
import bench.agent.loop as loop
import bench.agent.reply as reply
import bench.agent.state_loop as state_loop
import bench.agent.tools as tools
import bench.agent.trace as trace
import bench.agent.verbs as verbs
import bench.snapshot as snapshot
import bench.task_journal as task_journal
from bench.agent.kernel import Cell


def run(check):
    """The pure helpers the loops depend on: reply parsing, card digests, budgets, verb specs."""
    # --- pure helpers --------------------------------------------------------------------
    action = reply.compile_reply("thinking\n```python\nprint(1)\n```\n")
    check("reply: fenced block", action.kind == "code" and "print(1)" in (action.code or ""))
    action = reply.compile_reply("ANSWER: 42")
    check("reply: ANSWER line", action.kind == "answer" and action.value == "42")
    action = reply.compile_reply("just prose")
    check("reply: prose is not an action", action.kind == "none")

    check("state: clip", state_loop.clip("abcdef", 3).startswith("abc"))
    typed_cell = Cell(stdout="", stderr="", result='{"ok": true, "kind": "file"}',
                      final=None, duration_ms=1, tool="read")
    typed_card, _ = state_loop.render_observation(typed_cell, 1)
    check("state: a typed call names its tool", "[tool] read" in typed_card, typed_card[:200])
    card = state_loop.digest("\n".join(f"line {i}" for i in range(400)), 800, "_OBS[0]")
    check("state: digest names the handle", "_OBS[0]" in card and len(card) < 4000)
    check("state: an undigested stream is reported whole",
          state_loop.digest_card("short", 800, "_OBS[0]") == ("short", False))

    check("loop: budget defaults", loop.Budget().max_steps == 200)
    check("config: mode is a loop name", config.MODE in ("state", "tool", "repl"))
    check("llm: error type", issubclass(llm.LLMError, RuntimeError))
    check("trace: render is callable", callable(trace.render))
    check("task_journal: verbs", "final_answer" in task_journal.VERBS)
    _journal_metrics(check)
    check("snapshot: workspace helpers",
          callable(snapshot.manifest) and callable(snapshot.clone) and callable(snapshot.changed))
    typed_names = tuple(name for name, meta in verbs.VERBS.items() if meta.get("typed", True))
    check("verbs: every typed tool has a spec",
          tools.TOOL_NAMES == typed_names
          and [t["function"]["name"] for t in tools.TYPE_TOOLS] == list(typed_names),
          str(tools.TOOL_NAMES))
    check("verbs: every schema is an object and every summary is text",
          all(t["function"]["parameters"].get("type") == "object"
              and isinstance(t["function"].get("description"), str)
              for t in tools.TYPE_TOOLS))
    check("verbs: python is not generated as a typed tool", "python" not in tools.TOOL_NAMES)
    check("verbs: run is a python helper; exec is the typed process tool",
          "run" not in tools.TOOL_NAMES and "run" in verbs.VERBS and "exec" in tools.TOOL_NAMES,
          str(tools.TOOL_NAMES))


def _tui_events() -> list[dict]:
    """A journal shaped like a real TUI trace: flat `cell.start` records plus `tool_calls` turns."""
    def _turn(number, calls, tool, stdout, result):
        return {"kind": "turn", "data": {"turn": number, "tool_calls": calls,
                                         "observation": {"tool": tool, "stdout": stdout,
                                                         "result": json.dumps(result)}}}

    events = [
        {"kind": "cell.start", "cell": 1, "tool": "read", "args": {"path": "a.py"}},
        _turn(1, ["read"], "read", "abc",
              {"path": "a.py", "offset": 1, "next": {"offset": 71, "unit": "line"}}),
        {"kind": "cell.start", "cell": 2, "tool": "read",
         "args": {"path": "a.py", "start": 71}},
        _turn(2, ["read"], "read", "de", {"path": "a.py", "offset": 71, "next": None}),
        {"kind": "cell.start", "cell": 3, "tool": "exec",
         "args": {"argv": ["grep", "-rn", "x", "."]}},
        _turn(3, ["exec"], "exec", "",
              {"ok": True, "kind": "command",
               "note": "`grep` repeats a typed verb; search(pattern, path=[...]) answers it."}),
        {"kind": "cell.start", "cell": 4, "tool": "edit", "args": {"path": "src/a.ts"}},
        {"kind": "cell.start", "cell": 5, "tool": "exec",
         "args": {"commands": [["bun", "run", "typecheck"], ["bun", "test"]]}},
        _turn(4, ["edit", "exec"], "exec", "f", {"ok": True, "kind": "commands"}),
    ]
    for seq, event in enumerate(events, 1):
        event["seq"] = seq
    return events


def _journal_metrics(check) -> None:
    """summarize() over an archived journal and a TUI-shaped one: counts, escapes, causal keys."""
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp)
        (state / "tasks").mkdir()
        (state / "events").mkdir()
        (state / "tasks" / "t1.json").write_text(
            json.dumps({"status": "done", "stop": "DONE", "usage": {"total_tokens": 7}}),
            encoding="utf-8")
        events = [
            {"kind": "cell.start", "cell": 1, "tool": "exec",
             "args": {"argv": ["grep", "-rn", "x", "."]}},
            {"kind": "cell.start", "cell": 2, "tool": "exec",
             "args": {"argv": ["python3", "-c", "print(1)"]}},
            {"kind": "turn", "data": {"turn": 1, "code": "", "calls": [{}, {}],
                                      "observation": {"stdout": "abc", "stderr": ""}}},
            {"kind": "cell.start", "cell": 3, "tool": "exec", "args": {"shell": "echo hi"}},
            {"kind": "cell.start", "cell": 4, "tool": "write", "args": {"path": "a.txt"}},
            {"kind": "turn", "data": {"turn": 2, "code": "", "calls": [{}, {}],
                                      "observation": {"stdout": "defgh", "stderr": ""}}},
        ]
        (state / "events" / "t1.jsonl").write_text(
            "\n".join(json.dumps(event) for event in events) + "\n{not json\n",
            encoding="utf-8")
        row = task_journal.summarize(state, "t1")
        tui = _tui_events()
        (state / "tasks" / "t2.json").write_text(
            json.dumps({"status": "done", "stop": "DONE"}), encoding="utf-8")
        (state / "events" / "t2.jsonl").write_text(
            "\n".join(json.dumps(event) for event in tui) + "\n", encoding="utf-8")
        tui_row = task_journal.summarize(state, "t2")
    check("task_journal: summarize reports the escape metrics and the first mutation",
          row["exec_verb_dupes"] == 1 and row["python3_c"] == 1 and row["shell"] == 1
          and row["obs_chars"] == 9 and row["first_mutation_turn"] == 2
          and row["steps"] == 2 and row["cells"] == 4
          and {"tools", "verbs", "usage", "rejected"} <= set(row), str(row)[:300])
    check("task_journal: a TUI trace's counts come from cell.start, not turn.code",
          tui_row["tools"] == {"edit": 1, "exec": 2, "read": 2}
          and tui_row["verbs"]["exec"] == 2 and tui_row["verbs"]["read"] == 2
          and tui_row["edits"] == 1 and tui_row["check_runs"] == 1 and tui_row["test_runs"] == 1
          and sum(tui_row["tools"].values()) == tui_row["cells"] == 5, str(tui_row)[:300])
    check("task_journal: the causal keys name the note turn, the dupes after it, and paging",
          tui_row["dupe_note_turn"] == 3 and tui_row["dupes_after_note"] == 0
          and tui_row["paged_followups"] == 1 and tui_row["obs_chars_per_turn"] > 0,
          str(tui_row)[:300])
