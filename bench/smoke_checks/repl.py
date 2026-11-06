"""repl: the fenced-block dialect reuses the turn machine instead of copying it.

WHAT IT IS
  Three checks on ReplLoop: it subclasses ToolLoop, a ```python block runs and an ANSWER: line
  finishes through the shared loop, and a printing final_answer is UNREVIEWED before it is
  accepted. That is the whole point of the class — one turn machine, a different reply dialect.

ENTRY POINTS
  run(check)   called last by bench/smoke.py::main

INVARIANTS
  - Offline: FakeClient scripts the fenced replies, FakeKernel scripts the cells.
  - The checks assert `repl_loop.tools is None`: a repl loop must not offer typed tools.

WHERE TO CHANGE
  The reply dialect ...................... bench/agent/repl.py::ReplLoop._reply
  The UNREVIEWED rule .................... repl.ReplLoop._observe
"""
from __future__ import annotations

import bench.agent.loop as loop
import bench.agent.repl as repl
import bench.agent.tool_loop as tool_loop
from bench.smoke_support import FakeClient, FakeKernel, _cell, _reply


def run(check):
    """ReplLoop is a ToolLoop dialect: fences run, ANSWER finishes, no copied turn machine."""
    # --- the fenced-block dialect reuses that machine, not a copy of it -------------------
    check("repl: the loop reuses the turn machine", issubclass(repl.ReplLoop, tool_loop.ToolLoop))
    repl_code = "print('hi')"
    repl_client = FakeClient([_reply(content=f"```python\n{repl_code}\n```\n", finish="stop"),
                              _reply(content="ANSWER: 7", finish="stop")])
    repl_kernel = FakeKernel({repl_code: _cell(stdout="hi\n")})
    repl_loop = repl.ReplLoop(repl_client, repl_kernel, loop.Budget())
    repl_result = repl_loop.run("smoke", "task")
    check("repl: a fenced block runs and ANSWER finishes through the shared loop",
          repl_result.steps == 2 and repl_result.final == "7"
          and repl_result.stop == tool_loop.DONE and repl_kernel.codes == [repl_code]
          and repl_loop.tools is None,
          f"{repl_result.stop} {repl_result.final!r} {repl_kernel.codes}")

    printing = "print('seen'); final_answer('A')"
    repl_client = FakeClient([_reply(content=f"```python\n{printing}\n```\n", finish="stop"),
                              _reply(content="```python\nfinal_answer('A')\n```\n", finish="stop")])
    repl_kernel = FakeKernel({printing: _cell(final="A", stdout="seen\n"),
                              "final_answer('A')": _cell(final="A")})
    repl_result = repl.ReplLoop(repl_client, repl_kernel, loop.Budget()).run("smoke", "task")
    check("repl: a printing final_answer is UNREVIEWED, then accepted",
          repl_result.turns[0]["stop"] == "UNREVIEWED" and repl_result.final == "A"
          and repl_result.stop == tool_loop.DONE,
          f"{repl_result.stop} {repl_result.final!r} {repl_result.turns[0].get('stop')}")
