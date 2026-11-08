"""limits: the capability scope and output meter the loop enforces.

WHAT IT IS
  Offline checks for `bench/agent/limits.py`: a scope filters the provider tools and refuses a
  call outside it, the per-turn call ceiling skips the excess, the per-turn token ceiling
  reaches the client, the report ceiling clips with a visible note, and the meter reads the
  provider's usage instead of estimating.

ENTRY POINTS
  run(check)   called by bench/smoke.py after the turn-machine group

INVARIANTS
  - No kernel child and no model: FakeClient/FakeKernel from smoke_support answer the script.
  - The refusal and the skip are both asserted from the paired tool message, not from prose.
  - The meter is checked through a real loop run, so wiring is covered, not only the pure
    dataclass.

WHERE TO CHANGE
  The scope fields or the meter row ....... bench/agent/limits.py
  The loop's enforcement points ........... bench/agent/tool_loop.py
"""
from __future__ import annotations

from bench.agent import limits
from bench.smoke_support import _call, _cell, _reply, _run_loop


def run(check):
    """The capability gate, the call ceiling, the output ceiling and the meter."""
    default = limits.Scope()
    check("limits: the default scope grants read, write and exec",
          default.allows("read") and default.allows("write") and default.allows("exec"),
          str(sorted(default.tools))[:200])
    check("limits: the default scope grants the durable plan/task and withholds serve",
          default.allows("plan") and default.allows("task") and not default.allows("serve"),
          str(sorted(default.tools))[:200])

    narrow = limits.Scope(tools=frozenset({"read"}), allow_exec=False)
    check("limits: a narrow scope refuses write and exec",
          narrow.allows("read") and not narrow.allows("write") and not narrow.allows("exec"))
    check("limits: python is the code alias, so withholding code withholds both",
          not narrow.allows("code") and not narrow.allows("python"))
    check("limits: filter_tools keeps only the granted provider specs",
          [spec["function"]["name"] for spec in narrow.filter_tools(
              [{"function": {"name": "read"}}, {"function": {"name": "write"}}])] == ["read"])

    result, client, kernel = _run_loop(
        [_reply(calls=[_call("w1", None, name="write",
                             arguments='{"path": "a.txt", "content": "x"}')]),
         _reply(content="done", finish="stop")], {},
        typed={"write": _cell(stdout="wrote\n")}, scope=narrow)
    notice = result.turns[0]["calls"][0]["notice"] or ""
    check("limits: a call outside the scope is refused before the kernel sees it",
          kernel.calls == [] and "outside this task's scope" in notice,
          f"{kernel.calls} {notice[:160]}")
    check("limits: a refused call is an error and still pairs a tool message",
          result.errors == 1 and result.calls == 0 and result.final == "done",
          f"errors={result.errors} calls={result.calls} final={result.final!r}")

    no_code = limits.Scope(tools=frozenset({"read"}), allow_exec=False)
    result, client, kernel = _run_loop(
        [_reply(calls=[_call("c1", "print(1)", name="code")]),
         _reply(content="done", finish="stop")], {}, scope=no_code)
    check("limits: code itself is a capability, not an unconditional escape hatch",
          kernel.codes == [] and "outside this task's scope" in
          (result.turns[0]["calls"][0]["notice"] or ""),
          str(result.turns[0]["calls"][0])[:200])

    one = limits.Scope(max_calls_per_turn=1)
    result, client, kernel = _run_loop(
        [_reply(calls=[_call("r1", None, name="read", arguments='{"path": "a.txt"}'),
                       _call("r2", None, name="read", arguments='{"path": "b.txt"}')]),
         _reply(content="done", finish="stop")], {},
        typed={"read": _cell(stdout="ok\n")}, scope=one)
    check("limits: calls beyond the per-turn ceiling are skipped, not run",
          len(kernel.calls) == 1 and result.turns[0]["calls"][1].get("skipped") is True,
          str(result.turns[0]["calls"])[:240])
    check("limits: the skipped call names the ceiling in its tool message",
          "at most 1 tool calls per turn" in (result.turns[0]["calls"][1]["notice"] or ""),
          str(result.turns[0]["calls"][1])[:200])

    small = limits.Scope(max_turn_tokens=1234)
    result, client, kernel = _run_loop([_reply(content="done", finish="stop")], {}, scope=small)
    check("limits: the per-turn token ceiling reaches the client",
          client.limits and client.limits[0]["max_tokens"] == 1234, str(client.limits[:1]))

    check("limits: a fixed effort policy always returns the scope effort",
          limits.effort_for(limits.Scope(effort="low"), surprise=False) == "low")
    check("limits: adaptive effort is low on a surprise and absent otherwise",
          limits.effort_for(limits.Scope(effort_policy="adaptive"), surprise=True) == "low"
          and limits.effort_for(limits.Scope(effort_policy="adaptive"), surprise=False) is None)

    clipped = limits.Scope(max_report_chars=10).clip_report("abcdefghijklmnop")
    check("limits: the report ceiling clips with a named note",
          clipped.startswith("abcdefghij") and "report clipped" in clipped, clipped)

    result, client, kernel = _run_loop([_reply(content="done", finish="stop")], {})
    check("limits: the meter records provider completion and turn count",
          result.economy.get("turns") == 1 and result.economy.get("completion") == 1,
          str(result.economy))
