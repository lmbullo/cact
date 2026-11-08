"""helpers: the pure functions the loops and cards depend on, with no process at all.

WHAT IT IS
  Reply parsing (```python fences, ANSWER:, prose), the state card digest, budget defaults,
  the mode enum, the LLM error type, the trace renderer, the runs summary verbs, the
  workspace helpers, and the generated tool/verb specs. It also pins the journal reader's
  `cell.end` path (chars sum, rejected, dupe note turn, boolean restarts) and the worker's
  compact record. No kernel child, no model, no fixture.

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

import importlib.util
import inspect
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
from bench.agent.kernel import Cell, CellReply, Kernel

ROOT = Path(__file__).resolve().parents[2]


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
    under = Cell(stdout="small", stderr="", result=None, final=None, duration_ms=1,
                 obs_index=0)
    probe = state_loop.StateLoop(object(), object(), loop.Budget(), card_budget=1200)
    check("state: a card under the turn budget is byte-identical to today",
          probe._observe(under, 1) == state_loop.render_observation(under, 1)[0],
          probe._observe(under, 1)[:160])
    probe = state_loop.StateLoop(object(), object(), loop.Budget(), card_budget=6000)
    cells = [Cell(stdout="x" * 2700, stderr="", result=None, final=None, duration_ms=1,
                  obs_index=index) for index in range(3)]
    cards = [probe._observe(cell, 7) for cell in cells]
    check("state: a three-card turn renders within budget and names each clipped handle",
          sum(len(card) for card in cards) <= 6000
          and "[card clipped:" in cards[-1] and "_OBS[2]" in cards[-1]
          and "[card clipped:" not in cards[0], str([len(card) for card in cards]))
    text, _ = state_loop.clip_card("head\n" + "x" * 500 + "\n[error] keep me\n[check] pass",
                                   100, "_OBS[3]")
    check("state: a clipped card never hides an error or check line",
          "[error] keep me" in text and "[check] pass" in text, text[:200])

    check("loop: budget defaults", loop.Budget().max_steps == 200)
    check("config: mode is a loop name", config.MODE in ("state", "tool", "repl"))
    check("llm: error type", issubclass(llm.LLMError, RuntimeError))
    check("trace: render is callable", callable(trace.render))
    check("task_journal: verbs", "final_answer" in task_journal.VERBS)
    _journal_metrics(check)
    _cell_end_metrics(check)
    _worker_cell_end(check)
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
        (state / "server.json").write_text(json.dumps({"checks": [
            {"name": "test", "cmd": "cd tui && bun test"},
            {"name": "lint", "cmd": "python3 -m ruff check ."},
        ]}), encoding="utf-8")
        plan_events = [
            {"kind": "cell.start", "cell": 1, "tool": "exec",
             "args": {"argv": ["bun", "test"], "cwd": "tui"}},
            {"kind": "cell.start", "cell": 2, "tool": "exec",
             "args": {"argv": ["bun", "test", "tests/x.test.tsx"], "cwd": "tui"}},
            {"kind": "cell.start", "cell": 3, "tool": "exec",
             "args": {"argv": ["bun", "test", "-t", "name"], "cwd": "tui"}},
            {"kind": "cell.start", "cell": 4, "tool": "exec",
             "args": {"shell": "cd tui && bun test"}},
            {"kind": "cell.start", "cell": 5, "tool": "exec",
             "args": {"commands": [["python3", "-m", "ruff", "check", "."],
                                     ["bun", "test", "tests/x.test.tsx"]]}},
            {"kind": "cell.start", "cell": 6, "tool": "exec",
             "args": {"argv": ["bun", "run", "typecheck"]}},
            {"kind": "cell.start", "cell": 7, "tool": "exec",
             "args": {"shell": "git status"}},
            {"kind": "cell.start", "cell": 8, "tool": "git",
             "args": {"action": "log"}},
            {"kind": "cell.start", "cell": 9, "tool": "git",
             "args": {"action": "commit", "message": "x"}},
        ]
        (state / "tasks" / "t4.json").write_text(
            json.dumps({"status": "done", "stop": "DONE"}), encoding="utf-8")
        (state / "events" / "t4.jsonl").write_text(
            "\n".join(json.dumps(event) for event in plan_events) + "\n", encoding="utf-8")
        plan_row = task_journal.summarize(state, "t4")
    check("task_journal: a gate cell is an exact plan command; a narrowed run is its own key",
          plan_row["gate_cells"] == 2 and plan_row["narrow_cells"] == 3
          and plan_row["git_cells"] == 3 and plan_row["git_read_cells"] == 2
          and plan_row["git_write_cells"] == 1, str(plan_row)[:360])
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
    # checks()/status() run in the loop, so `turn.tool_calls` is the only record of them.
    parent_turns = [{"tool_calls": ["checks", "status", "checks"]},
                    {"tool_calls": [{"function": {"name": "docs"}}, "checks"]}]
    check("task_journal: parent-side calls come from turn.tool_calls",
          task_journal._tool_call_count(parent_turns, "checks") == 3
          and task_journal._tool_call_count(parent_turns, "status") == 1
          and task_journal._tool_call_count(parent_turns, "docs") == 1,
          str(parent_turns)[:240])


def _cell_end_metrics(check) -> None:
    """A journal with one `cell.end` per call: chars sum, rejection and the note turn."""
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp)
        (state / "tasks").mkdir()
        (state / "events").mkdir()
        (state / "tasks" / "t3.json").write_text(
            json.dumps({"status": "done", "stop": "DONE"}), encoding="utf-8")
        events = [
            {"kind": "cell.start", "cell": 1, "tool": "read", "args": {"path": "a.py"}},
            {"kind": "cell.end", "cell": 1, "tool": "read", "ok": True,
             "chars": 3, "duration_ms": 1, "notes": []},
            {"kind": "cell.start", "cell": 2, "tool": "exec", "args": {"argv": ["grep"]}},
            {"kind": "cell.end", "cell": 2, "tool": "exec", "ok": True,
             "chars": 5, "duration_ms": 2,
             "notes": ["`grep` repeats a typed verb; search(...) answers it."]},
            {"kind": "cell.start", "cell": 3, "tool": "edit", "args": {}},
            {"kind": "cell.end", "cell": 3, "tool": "edit", "ok": False,
             "code": "internal_error", "chars": 7, "duration_ms": 3, "notes": []},
            {"kind": "turn", "data": {"turn": 1, "calls": [{}, {}, {}],
                                      "card_chars": 11, "card_budget": 6000,
                                      "observation": {"stdout": "a doc line: empty namespace",
                                                      "stderr": "", "restarted": False}}},
            {"kind": "turn", "data": {"turn": 2, "calls": [],
                                      "observation": {"stdout": "", "stderr": "",
                                                      "restarted": True}}},
        ]
        (state / "events" / "t3.jsonl").write_text(
            "\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
        row = task_journal.summarize(state, "t3")
    check("task_journal: cell.end sums chars and counts a crashed call as rejected",
          row["obs_chars"] == 15 and row["rejected"] == 1 and row["cells"] == 3
          and row["dupe_note_turn"] == 1, str(row)[:360])
    check("task_journal: restarts reads the observation boolean, not the phrase",
          row["restarts"] == 1, str(row)[:360])
    check("task_journal: card_chars reports delivery against the turn budget",
          row["card_chars"] == 11 and row["card_chars_per_turn"] == 6
          and row["card_budget"] == 6000, str(row)[:360])


def _worker_cell_end(check) -> None:
    """StreamingKernel's compact record: counts and codes, never the payload."""
    spec = importlib.util.spec_from_file_location(
        "tui_worker", ROOT / "tui" / "python" / "worker.py")
    worker = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(worker)
    result = ('{"ok": true, "kind": "file", "shown": 1, "total": 2, "unit": "line", '
              '"has_more": true, "truncated": false, "complete": true}')
    reply = CellReply(stdout="abc", result=result, tool="read", obs_index=0)
    cell = Cell.from_reply(reply, duration_ms=7)
    record = worker.cell_end_payload(cell, 3, "read")
    check("worker: cell.end is compact, with counts and no payload body",
          record["kind"] == "cell.end" and record["cell"] == 3
          and record["tool"] == "read" and record["ok"] is True
          and record["chars"] == len("abc") + len(result)
          and record["page"]["shown"] == 1 and record["duration_ms"] == 7
          and "stdout" not in record and "result" not in record,
          str(record)[:320])
    # The emitter (not just the payload builder): one compact record per call, in order, and
    # the override must accept every keyword the base adds - a subclass that shadows
    # `extra_obs` breaks every typed call in the TUI while the kernel tests stay green.
    base_run = inspect.signature(Kernel.run).parameters
    base_call = inspect.signature(Kernel.call).parameters
    for name, base, override in (("run", base_run, worker.StreamingKernel.run),
                                 ("call", base_call, worker.StreamingKernel.call)):
        have = inspect.signature(override).parameters
        check("worker: StreamingKernel.%s accepts every Kernel.%s keyword" % (name, name),
              set(base) <= set(have), "%s vs %s" % (sorted(base), sorted(have)))
    outer_run, outer_call = Kernel.run, Kernel.call
    try:
        Kernel.run = lambda self, code, check_cache=None, extra_obs=None: Cell(
            stdout="a", stderr="", result=None, final=None, duration_ms=1)
        Kernel.call = lambda self, tool, args=None, check_cache=None, extra_obs=None: Cell(
            stdout="b", stderr="", result='{"ok": true, "kind": "file"}',
            final=None, duration_ms=1)
        stream = worker.StreamingKernel.__new__(worker.StreamingKernel)
        events: list[dict] = []
        stream._emit_event = events.append
        stream._cell_no = 0
        stream.run("print(1)")
        stream.call("read", {"path": "a"}, extra_obs=[{"stdout": "seeded"}])
        stream.call("map", {"path": "."})
    finally:
        Kernel.run, Kernel.call = outer_run, outer_call
    ends = [event for event in events if event["kind"] == "cell.end"]
    check("worker: a three-call turn emits three cell.end records",
          len(ends) == 3 and [event["cell"] for event in ends] == [1, 2, 3]
          and [event["tool"] for event in ends] == ["python", "read", "map"],
          str(ends)[:320])
