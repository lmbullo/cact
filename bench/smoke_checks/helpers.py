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
