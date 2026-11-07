"""turns: the turn machine — finish protocol, stop vocabulary and call pairing.

WHAT IT IS
  ToolLoop.run over a scripted client and a scripted kernel: a staged answer accepted by the
  next no-tool reply, the staging notice in the tool result, a typed tool dispatched through
  Kernel.call, staging surviving later calls in its turn, a later non-final cell clearing it,
  a silent final_answer stopping the rest of its turn, the handoff winning over the short
  value, skipped calls still paired, one tool message per call even when dispatch raises or
  the arguments are unparsable, LLM_FAILED with a summary record, the budget-forced push,
  the restart card, and a goal check re-prompting (and rejecting a staged answer) until it
  passes.

ENTRY POINTS
  run(check)   called by bench/smoke.py::main after the kernel groups

INVARIANTS
  - Nothing here spawns: FakeClient answers the scripted reply, FakeKernel answers the
    scripted Cell, so each check is deterministic and offline.
  - The pairing checks compare tool-call ids with tool-message ids: an orphaned call or a
    duplicated result is the failure mode the protocol has to prevent.
  - `staged_code` and `_run_loop` come from smoke_support; keep one scripted reply per turn.

WHERE TO CHANGE
  A stop reason or the finish protocol ..... bench/agent/tool_loop.py (this file scores it)
  The staging notice text .................. tool_loop's notice strings
  Budget behaviour ......................... agent/loop.py::Budget
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import bench.agent.llm as llm
import bench.agent.loop as loop
import bench.agent.state_loop as state_loop
import bench.agent.tool_loop as tool_loop
import bench.agent.trace as trace
from bench.agent.kernel import Cell
from bench.smoke_support import FakeClient, FakeKernel, _call, _cell, _paired_messages, _reply, _run_loop


def run(check):
    """The turn machine offline: stop vocabulary, staged finishes, goal rejection, call pairing."""
    # --- the turn machine: finish protocol, stop vocabulary and call pairing --------------
    check("turn: the stop vocabulary is fixed",
          set(tool_loop.STOPS) == {"DONE", "VERIFIED", "STEP_LIMIT", "WALL_LIMIT", "STAGED",
                                   "GOAL_UNMET", "TRUNCATED", "BAD_ARGS", "LLM_FAILED",
                                   "TOKEN_LIMIT", "SKEPTIC_UNMET", "NEEDS_INPUT"},
          str(tool_loop.STOPS))
    check("turn: a turn outcome defaults to continuing",
          not tool_loop.TurnOutcome().end and tool_loop.TurnOutcome().stop is None)

    staged_code = "print('evidence'); final_answer('answer A')"
    result, client, kernel = _run_loop(
        [_reply(calls=[_call("c1", staged_code)]), _reply(content="ok", finish="stop")],
        {staged_code: _cell(final="answer A", stdout="evidence\n")})
    check("turn: a staged answer is accepted by a no-tool reply",
          result.final == "answer A" and result.stop == tool_loop.DONE
          and result.turns[0]["stop"] == tool_loop.STAGED, f"{result.stop} {result.final!r}")
    check("turn: the staging notice reaches the model as the tool result",
          "answer staged" in (result.turns[0]["calls"][0]["notice"] or "")
          and "answer staged" in client.requests[1][-1]["content"], str(result.turns[0])[:200])

    typed_read = Cell(stdout="     1| alpha\n", stderr="",
                      result='{"ok": true, "kind": "file"}', final=None, duration_ms=1,
                      tool="read", reads={"a.txt": {"ranges": [[1, 1]], "total": 2}})
    result, client, kernel = _run_loop(
        [_reply(calls=[_call("r1", None, name="read", arguments='{"path": "a.txt"}')]),
         _reply(content="done", finish="stop")],
        {}, typed={"read": typed_read})
    check("turn: a typed tool dispatches to Kernel.call",
          kernel.calls == [("read", {"path": "a.txt"})]
          and result.turns[0]["calls"][0]["name"] == "read", str(kernel.calls))
    check("turn: a typed call keeps its args and tool in the record",
          result.turns[0]["calls"][0]["args"] == {"path": "a.txt"}
          and result.turns[0]["calls"][0]["observation"]["tool"] == "read",
          str(result.turns[0]["calls"][0])[:240])

    result, client, kernel = _run_loop(
        [_reply(calls=[_call("c1", staged_code), _call("c2", "print('more')")]),
         _reply(content="", finish="stop")],
        {staged_code: _cell(final="answer A", stdout="evidence\n"),
         "print('more')": _cell(stdout="more\n")})
    check("turn: staging survives a later call in the same turn",
          result.final == "answer A" and len(result.turns[0]["calls"]) == 2
          and result.turns[0]["calls"][0]["stop"] == tool_loop.STAGED
          and result.turns[0]["calls"][1]["stop"] is None, str(result.turns[0]["calls"])[:300])

    result, client, kernel = _run_loop(
        [_reply(calls=[_call("c1", staged_code)]),
         _reply(calls=[_call("c2", "print('more')")]),
         _reply(content="a later answer", finish="stop")],
        {staged_code: _cell(final="answer A", stdout="evidence\n"),
         "print('more')": _cell(stdout="more\n")})
    check("turn: a non-final cell in a later turn clears the staged answer",
          result.turns[0]["stop"] == tool_loop.STAGED and result.final == "a later answer",
          f"{result.stop} {result.final!r}")

    late = "print('must not run')"
    result, client, kernel = _run_loop(
        [_reply(calls=[_call("c1", "final_answer('now')"), _call("c2", late)]),
         _reply(content="the real handoff", finish="stop")],
        {"final_answer('now')": _cell(final="now")})
    check("turn: a silent final_answer stages and keeps later calls from running",
          late not in kernel.codes and result.final == "the real handoff"
          and result.stop == tool_loop.DONE, str(kernel.codes))
    late_result = result
    result, client, kernel = _run_loop(
        [_reply(calls=[_call("c1", "final_answer('short')")]),
         _reply(content="the full report", finish="stop")],
        {"final_answer('short')": _cell(final="short")})
    check("turn: the next reply is the handoff, not the tool's short value",
          result.final == "the full report" and result.stop == tool_loop.DONE,
          f"{result.stop} {result.final!r}")
    check("turn: a skipped call survives in the turn record",
          len(late_result.turns[0]["calls"]) == 2
          and late_result.turns[0]["calls"][1].get("skipped") is True,
          str(late_result.turns[0]["calls"])[:300])

    probe = tool_loop.ToolLoop(FakeClient([]), FakeKernel({}), loop.Budget())
    accepted = tool_loop.FinishState()
    accepted.accept("done")
    probe_messages: list[dict] = [{"role": "assistant", "content": "", "tool_calls": [
        _call("a", "print(1)"), _call("b", None, name="other")]}]
    probe._answer_calls(probe_messages, {"runs": []}, probe_messages[0]["tool_calls"], "stop",
                        accepted, tool_loop.Tally(), set(), 1)
    ids, paired = _paired_messages(probe_messages)
    check("turn: calls after an accepted finish are still paired with tool messages",
          ids == paired == ["a", "b"] and "skipped" in probe_messages[-1]["content"], f"{ids} {paired}")

    raise_code = "raise_in_kernel()"
    result, client, kernel = _run_loop(
        [_reply(calls=[_call("a", None, name="write"),
                       _call("b", None, arguments="{not json"),
                       _call("c", raise_code), _call("d", "print('kept')")]),
         _reply(content="all done", finish="stop")],
        {"print('kept')": _cell(stdout="kept\n")}, raises={raise_code})
    ids, paired = _paired_messages(client.requests[1])
    check("turn: every tool call in a turn is paired with exactly one tool message",
          ids == paired == ["a", "b", "c", "d"], f"{ids} {paired}")
    tool_messages = {m["tool_call_id"]: m["content"] for m in client.requests[1] if m.get("role") == "tool"}
    check("turn: a raising dispatch is answered, not orphaned",
          "kernel exploded" in tool_messages.get("c", ""), str(tool_messages)[:300])
    check("turn: every call in a multi-call turn survives in the record",
          [c["stop"] for c in result.turns[0]["calls"]] == [None, tool_loop.BAD_ARGS, None, None]
          and result.turns[0]["calls"][3]["code"] == "print('kept')"
          and result.turns[0]["stop"] == tool_loop.BAD_ARGS,
          str(result.turns[0]["calls"])[:300])

    result, client, kernel = _run_loop(
        [_reply(calls=[_call("x", "boom()"), _call("y", "print('after')")]),
         _reply(content="done", finish="stop")],
        {"print('after')": _cell(stdout="after\n")}, raises={"boom()"})
    ids, paired = _paired_messages(client.requests[1])
    check("turn: an unexpected Kernel.run exception still pairs the turn",
          ids == paired == ["x", "y"] and "kernel exploded" in client.requests[1][-2]["content"],
          f"{ids} {paired}")

    fail_code = "print('first')"
    fail_client = FakeClient([_reply(calls=[_call("c1", fail_code)]),
                              llm.LLMError("upstream 500")])
    fail_kernel = FakeKernel({fail_code: _cell(stdout="first\n")})
    fail_trace = Path(tempfile.mkdtemp(prefix="cact-smoke-trace-")) / "trace.jsonl"
    fail_result = tool_loop.ToolLoop(fail_client, fail_kernel, loop.Budget()).run(
        "smoke", "task", trace.Trace(str(fail_trace)))
    fail_records = [json.loads(line) for line in fail_trace.read_text().splitlines()]
    fail_summary = fail_records[-1]
    check("turn: a primary LLMError stops with LLM_FAILED and a summary record",
          fail_result.stop == tool_loop.LLM_FAILED
          and fail_summary.get("summary") is True
          and fail_summary["stop"] == tool_loop.LLM_FAILED
          and fail_summary["llm_calls"] == 2
          and "upstream 500" in fail_result.error
          and "upstream 500" in fail_summary.get("error", ""),
          f"{fail_result.stop} {fail_summary} {fail_result.error!r}")

    push_client = FakeClient([_reply(content="budget summary", finish="stop")])
    push_result = tool_loop.ToolLoop(push_client, FakeKernel({}), loop.Budget(max_steps=0)).run(
        "smoke", "task")
    check("turn: the budget-forced _push counts as an llm call",
          push_result.steps == 0 and push_result.llm_calls == 1
          and push_result.stop == tool_loop.STEP_LIMIT,
          f"steps={push_result.steps} llm_calls={push_result.llm_calls} stop={push_result.stop}")

    nudge_client = FakeClient(
        [_reply(calls=[_call(f"n{i}", "print(1)")]) for i in range(5)]
        + [_reply(content="budget summary", finish="stop")])
    nudge_result = tool_loop.ToolLoop(
        nudge_client, FakeKernel({}), loop.Budget(max_steps=5), wrap_up_at=0.4,
    ).run("smoke", "task")

    def _runtime(request: list[dict]) -> list[str]:
        return [m.get("content") or "" for m in request
                if m.get("role") == "user" and (m.get("content") or "").startswith("[runtime]")]
    requests = nudge_client.requests
    check("turn: the wrap-up nudge arrives once before the last-turn nudge",
          nudge_result.stop == tool_loop.STEP_LIMIT
          and not any("steps left" in text for text in _runtime(requests[1]))
          and sum("steps left" in text for text in _runtime(requests[2])) == 1
          and sum("steps left" in text for text in _runtime(requests[3])) == 1
          and sum("This is your last turn" in text for text in _runtime(requests[4])) == 1,
          f"{nudge_result.stop} {[_runtime(r) for r in requests[1:5]]}")

    restarted = Cell(stdout="", stderr="kernel died; restarted with an empty namespace",
                     result=None, final=None, duration_ms=1, restarted=True)
    restart_client = FakeClient([_reply(calls=[_call("r1", "print(1)")]),
                                 _reply(content="done", finish="stop")])
    restart_loop = state_loop.StateLoop(restart_client, FakeKernel({"print(1)": restarted}),
                                        loop.Budget())
    restart_loop.run("smoke", "task")
    restart_card = restart_client.requests[1][-1]["content"]
    check("state: a restarted cell's card names the restart",
          "[state] kernel restarted" in restart_card, restart_card[:300])

    goals = []

    def flaky_goal() -> bool:
        """Fails the first check, passes the second: the re-prompt path."""
        goals.append(1)
        return len(goals) > 1

    result, client, kernel = _run_loop(
        [_reply(content="I am done", finish="stop"), _reply(content="now really", finish="stop")],
        {}, goal=flaky_goal)
    check("turn: a failing goal re-prompts instead of finishing",
          result.turns[0]["stop"] == tool_loop.GOAL_UNMET and result.stop == tool_loop.DONE
          and result.final == "now really", f"{result.stop} {result.final!r} {goals}")
    check("turn: the rejection notice names the check",
          "You finished without a passing check" in client.requests[1][-1]["content"],
          str(client.requests[1][-1])[:200])

    goal_checks = []

    def goal_eventually_passes() -> bool:
        """Fails once, then passes: a staged answer must survive the rejection."""
        goal_checks.append(1)
        return len(goal_checks) > 1

    result, client, kernel = _run_loop(
        [_reply(calls=[_call("c1", staged_code)]), _reply(content="", finish="stop"),
         _reply(content="now passing", finish="stop")],
        {staged_code: _cell(final="answer A", stdout="evidence\n")},
        goal=goal_eventually_passes)
    check("turn: a staged answer is still rejected until the goal passes",
          result.turns[1]["stop"] == tool_loop.GOAL_UNMET and result.final == "now passing"
          and result.stop == tool_loop.DONE, f"{result.stop} {result.final!r} {goal_checks}")
