"""loops: deterministic, scripted evals for the turn machine and the kernel contract.

WHAT IT IS
  Every case here runs without a key or a network: a `ScriptedClient` plays the model, and
  the assertion is about the loop's behavior, not a model's judgment. The cases cover the
  failure modes that are cheap to miss in a live run: a staged finish, a rejected goal, bad
  arguments, a repeated read, a journalled mutation, compaction, per-run custom tools,
  on-demand skills, delegation, a skeptical finish, a token cap, paired skipped calls and an
  unknown tool.

ENTRY POINTS
  LOOP_CASES   the list `runner` registers; each is a `types.Case`
  run_loop(ws, client, **kwargs) -> (RunResult, ToolLoop, Kernel)   the shared constructor

INVARIANTS
  - Each case gets a fresh workspace and one kernel child, stopped in a `finally`; a case
    that leaks a child fails the suite's process check.
  - A scripted reply list is the whole script: no case depends on the real provider, the
    clock, or another case's files. `ScriptedClient` repeats its last reply, so a repair path
    can be shorter than the loop's maximum.
  - The oracle runs on the workspace after the loop stops. A case passes only when the stop,
    the observation and the oracle agree; a model-shaped final answer alone is not enough.
  - The `failure` tag is the diagnostic axis: `context`, `tooling`, `mutation`, `finish`,
    `verification`, `cost`. A failing case names the mechanism, not just the task.

WHERE TO CHANGE
  A new loop idea ......... add a case here, then register it in runner.REGISTRY
  The shared constructor ... run_loop
  A new scripted shape .... model.py (tool/say/responder)
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..agent import custom
from ..agent.docs import DocsClient
from ..agent.kernel import Kernel
from ..agent.loop import Budget
from ..agent.skeptic import Verdict
from ..agent.state_loop import StateLoop
from ..agent.subagent import Subagent, SubagentResult
from ..agent.tool_loop import ToolLoop
from .fixtures import long_tree, mutation_tree, skill_tree
from .model import ScriptedClient, say, tool
from .oracle import calc_add, has_text
from .types import Case, CaseResult, Context


def run_loop(ws: Path, client, *, rules: str = "", tools=None, extra_tools=None,
             extra_verbs=None, extra_prelude: str = "", subagent=None, skeptic=None, docs=None,
             goal=None, goal_stops: bool = False, budget: Budget | None = None,
             state: bool = False, context_budget: int = 50, keep_messages: int = 2,
             autonomy: bool = False,
             prelude: str = "all", writable: bool = True, allow_run: bool = True):
    """Build one kernel + loop for a case, with every experiment lever available."""
    kernel = Kernel(ws, writable=writable, prelude=prelude, allow_run=allow_run,
                    extra_verbs=extra_verbs, extra_prelude=extra_prelude)
    kwargs = dict(rules=rules, tools=tools, goal=goal, goal_stops=goal_stops,
                  extra_tools=extra_tools, subagent=subagent, skeptic=skeptic,
                  autonomy=autonomy, docs=docs)
    loop = (StateLoop(client, kernel, budget or Budget(max_steps=20), context_budget=context_budget,
                      keep_messages=keep_messages, **kwargs)
            if state else ToolLoop(client, kernel, budget or Budget(max_steps=20), **kwargs))
    return loop, kernel


def _run(ws: Path, client: ScriptedClient, **kwargs) -> tuple:
    loop, kernel = run_loop(ws, client, **kwargs)
    try:
        result = loop.run("case", "do the task", None)
    finally:
        kernel.stop()
    return result, loop


def _ok(ok: bool, detail: str, result=None, loop=None, meta=None) -> CaseResult:
    return CaseResult(
        ok=ok, detail=detail,
        stop=getattr(result, "stop", ""), steps=getattr(result, "steps", 0),
        usage=getattr(result, "usage", {}) or {}, final=getattr(result, "final", "") or "",
        errors=getattr(result, "errors", 0),
        meta={**(meta or {}), "compactions": getattr(loop, "compactions", 0)},
    )


# --- finish protocol ----------------------------------------------------------------------


def case_staged_finish(ctx: Context) -> CaseResult:
    """A silent final_answer is held; the next no-tool report wins and is accepted."""
    ws = mutation_tree(ctx.workspace)
    client = ScriptedClient([
        tool("python", code="final_answer('short')"),
        say("the real handoff"),
    ])
    result, _ = _run(ws, client)
    ok = result.stop == "DONE" and result.final == "the real handoff"
    return _ok(ok, f"stop={result.stop} final={result.final!r}", result)


def case_goal_reject(ctx: Context) -> CaseResult:
    """A finish is rejected until the deterministic oracle passes; the repair then passes."""
    ws = mutation_tree(ctx.workspace)
    client = ScriptedClient([
        tool("python", code="final_answer('done')"),
        say("done"),
        tool("edit", edits=[{"path": "calc.py", "old": "return a - b", "new": "return a + b"}]),
        say("fixed"),
    ])
    result, _ = _run(ws, client, goal=lambda: calc_add(ws)[0], budget=Budget(max_steps=10))
    passed, detail = calc_add(ws)
    ok = result.stop == "DONE" and passed and any(
        turn.get("stop") == "GOAL_UNMET" for turn in result.turns)
    return _ok(ok, f"stop={result.stop} oracle={detail}", result)


def case_skipped_pair(ctx: Context) -> CaseResult:
    """A staged final_answer makes the rest of its turn skipped but still paired."""
    ws = mutation_tree(ctx.workspace)
    client = ScriptedClient([
        {"tool_calls": [tool("python", code="final_answer('ok')"),
                        tool("read", path="calc.py")]},
        say("handoff"),
    ])
    result, _ = _run(ws, client)
    calls = (result.turns[0].get("calls") if result.turns else []) or []
    ok = (result.stop == "DONE" and len(calls) == 2 and calls[0].get("stop") == "STAGED"
          and calls[1].get("skipped") is True)
    return _ok(ok, f"stop={result.stop} calls={[(c.get('name'), c.get('skipped')) for c in calls]}", result)


# --- arguments and observations -----------------------------------------------------------


def case_bad_args(ctx: Context) -> CaseResult:
    """A wrong keyword is structured bad_args, the next repair runs, and the error is counted."""
    ws = mutation_tree(ctx.workspace)
    client = ScriptedClient([
        tool("read", path="calc.py", nope=1),
        tool("read", path="calc.py"),
        say("ok"),
    ])
    result, _ = _run(ws, client)
    obs = (result.turns[0]["calls"][0].get("observation") or {}) if result.turns else {}
    rendered = json.dumps(obs)
    ok = (result.errors >= 1 and obs.get("rejected") is True
          and "bad_arguments" in rendered and result.stop == "DONE")
    return _ok(ok, f"errors={result.errors} rejected={obs.get('rejected')} stop={result.stop}", result)


def case_repeat_read(ctx: Context) -> CaseResult:
    """A repeated contained range is a pointer, not a second delivery."""
    ws = mutation_tree(ctx.workspace)
    client = ScriptedClient([
        tool("read", path="calc.py"),
        tool("read", path="calc.py"),
        say("ok"),
    ])
    result, _ = _run(ws, client)
    second = result.turns[1]["calls"][0].get("observation") if len(result.turns) > 1 else {}
    rendered = json.dumps(second or {})
    ok = result.stop == "DONE" and ("already_read" in rendered or "already delivered" in rendered)
    return _ok(ok, f"stop={result.stop} second={rendered[:200]}", result)


def case_unknown_tool(ctx: Context) -> CaseResult:
    """An unknown tool gets a structured notice and a paired result, never an orphaned call."""
    ws = mutation_tree(ctx.workspace)
    client = ScriptedClient([tool("nope", x=1), say("ok")])
    result, _ = _run(ws, client)
    call = (result.turns[0]["calls"][0] if result.turns else {}) or {}
    ok = result.stop == "DONE" and "unknown tool" in str(call.get("notice") or "")
    return _ok(ok, f"stop={result.stop} notice={str(call.get('notice'))[:120]}", result)


# --- mutation and journal -----------------------------------------------------------------


def case_python_write_journal(ctx: Context) -> CaseResult:
    """A raw `open(..., 'w')` is journalled; the next diff names the change."""
    ws = mutation_tree(ctx.workspace)
    client = ScriptedClient([
        tool("python", code="open('calc.py','w').write(\"def add(a, b):\\n    return a + b\\n\")"),
        tool("session_diff"),
        say("changed"),
    ])
    result, loop = _run(ws, client)
    diff = str(result.turns[1]["calls"][0].get("observation") or {}) if len(result.turns) > 1 else ""
    ok = (result.stop == "DONE" and "calc.py" in getattr(loop, "changed", set())
          and "return a + b" in diff)
    return _ok(ok, f"changed={sorted(getattr(loop, 'changed', set()))} diff={diff[:120]}", result, loop)


# --- context ------------------------------------------------------------------------------


def case_compaction(ctx: Context) -> CaseResult:
    """A small context budget folds old turns into one session-state message."""
    ws = long_tree(ctx.workspace, files=4)
    client = ScriptedClient(
        [tool("python", code="print('x' * 2000)") for _ in range(6)] + [say("done")],
        usage={"prompt_tokens": 100, "prompt_cache_miss_tokens": 100, "total_tokens": 101},
    )
    result, loop = _run(ws, client, state=True, context_budget=50, keep_messages=2,
                        budget=Budget(max_steps=12))
    folded = any(
        "session state" in str(message.get("content"))
        for request in client.requests for message in request["messages"]
    )
    ok = result.stop == "DONE" and loop.compactions > 0 and folded
    return _ok(ok, f"compactions={loop.compactions} folded={folded}", result, loop)


# --- experiment tools ---------------------------------------------------------------------


def case_custom_tool(ctx: Context) -> CaseResult:
    """A per-run manifest adds one action to the provider and the kernel."""
    ws = mutation_tree(ctx.workspace)
    (ws / "data.txt").write_text("a\nb\nc\n", encoding="utf-8")
    manifest = ws / "tools.json"
    manifest.write_text(json.dumps({"tools": [{
        "name": "count_rows",
        "summary": "Count non-empty rows in a text file.",
        "schema": {"type": "object", "properties": {"path": {"type": "string"}},
                   "required": ["path"], "additionalProperties": False},
        "source": (
            "def count_rows(path):\n"
            "    with _open(path, encoding='utf-8') as fh:\n"
            "        n = sum(1 for line in fh if line.strip())\n"
            "    return {'ok': True, 'kind': 'count_rows', 'count': n, 'text': str(n)}\n"
        ),
    }]}), encoding="utf-8")
    tools = custom.load(manifest)
    client = ScriptedClient([tool("count_rows", path="data.txt"), say("3")])
    result, _ = _run(ws, client, extra_tools=custom.specs(tools),
                     extra_verbs=custom.verbs(tools), extra_prelude=custom.prelude(tools))
    obs = str(result.turns[0]["calls"][0].get("observation") or {}) if result.turns else ""
    ok = result.stop == "DONE" and "count" in obs and result.final == "3"
    return _ok(ok, f"stop={result.stop} obs={obs[:160]}", result)


def case_skill(ctx: Context) -> CaseResult:
    """A project procedure is loaded on demand and then followed."""
    ws = skill_tree(ctx.workspace)
    client = ScriptedClient([
        tool("skill", name="release"),
        tool("write", path="CHANGELOG.md", content="# Changelog\n\n## 2.4.1\n"),
        say("released"),
    ])
    result, _ = _run(ws, client)
    obs = str(result.turns[0]["calls"][0].get("observation") or {}) if result.turns else ""
    passed, detail = has_text(ws, "CHANGELOG.md", "2.4.1")
    ok = result.stop == "DONE" and "Append the version" in obs and passed
    return _ok(ok, f"stop={result.stop} oracle={detail}", result)


def case_docs(ctx: Context) -> CaseResult:
    """docs(url) fetches a page, strips navigation/scripts, and returns the code block."""
    page = (b"<html><head><title>Acme SDK</title></head><body>"
            b"<nav>navigation junk</nav><h1>Install</h1>"
            b"<p>Use npm install acme-sdk.</p>"
            b"<pre>import acme from 'acme-sdk'</pre>"
            b"<script>evil()</script></body></html>")

    class Handler(BaseHTTPRequestHandler):
        """Serve one documentation page for the duration of the case."""

        def do_GET(self):
            """Return the fixed HTML page with a text/html content type."""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)

        def log_message(self, *args):
            """Silence the per-request stderr line."""
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/sdk"
    ws = mutation_tree(ctx.workspace)
    client = ScriptedClient([tool("docs", url=url), say("read")])
    try:
        result, _ = _run(ws, client, docs=DocsClient(cache=False))
    finally:
        server.shutdown()
        server.server_close()
    call = (result.turns[0]["calls"][0] if result.turns else {}) or {}
    obs = str(call.get("notice") or "") + str(call.get("obs") or "")
    ok = (result.stop == "DONE" and "Use npm install acme-sdk." in obs
          and "import acme from 'acme-sdk'" in obs and "evil()" not in obs
          and "navigation junk" not in obs)
    return _ok(ok, f"stop={result.stop} obs={obs[:220]}", result)


# --- delegation and skepticism ------------------------------------------------------------


class FakeSubagent:
    """A delegate target that records its prompt and returns a fixed child result."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def run(self, task: str, prompt: str) -> SubagentResult:
        """Answer with a deterministic child result and remember the question."""
        self.calls.append((task, prompt))
        return SubagentResult(answer="found it in calc.py", stop="DONE", steps=2,
                              usage={"total_tokens": 7}, task=task)


def case_delegate(ctx: Context) -> CaseResult:
    """The parent exposes delegate, receives one observation, and counts the child's tokens."""
    ws = mutation_tree(ctx.workspace)
    sub = FakeSubagent()
    client = ScriptedClient([tool("delegate", task="find", prompt="where is add?"), say("done")])
    result, _ = _run(ws, client, subagent=sub)
    call = (result.turns[0]["calls"][0] if result.turns else {}) or {}
    text = str(call.get("notice") or "") + str(call.get("obs") or "")
    ok = (result.stop == "DONE" and sub.calls == [("find", "where is add?")]
          and "found it" in text and int(result.usage.get("total_tokens") or 0) >= 9)
    return _ok(ok, f"stop={result.stop} calls={sub.calls} text={text[:120]}", result)


def case_subagent_real(ctx: Context) -> CaseResult:
    """The real Subagent runs a read-only child and returns its answer."""
    ws = long_tree(ctx.workspace, files=3)
    child = ScriptedClient([tool("read", path="fact.txt"), say("ORBIT-7419")])
    sub = Subagent(child, ws)
    result = sub.run("find", "What is DEPLOY_CODE in fact.txt?")
    names = [spec["function"]["name"] for spec in sub._child_tools()]
    ok = (result.ok and "ORBIT-7419" in result.answer
          and not ({"write", "edit", "exec", "delegate"} & set(names)))
    return CaseResult(ok=ok, detail=f"stop={result.stop} answer={result.answer!r} tools={names}",
                      stop=result.stop, steps=result.steps, usage=result.usage,
                      final=result.answer)


class FakeSkeptic:
    """A reviewer that fails the first finish and passes the next candidate."""

    def __init__(self) -> None:
        self.calls = 0

    def review(self, prompt: str, final: str, diff: str = "", check_text: str = "",
               changed=()) -> Verdict:
        """Reject once with a concrete gap, then accept."""
        self.calls += 1
        if self.calls == 1:
            return Verdict(False, "calc.py still subtracts; the spec says sum")
        return Verdict(True, "PASS", {"total_tokens": 3})


def case_skeptic_reject(ctx: Context) -> CaseResult:
    """A finish is rejected by the skeptic, repaired, then accepted."""
    ws = mutation_tree(ctx.workspace)
    skeptic = FakeSkeptic()
    client = ScriptedClient([
        tool("python", code="final_answer('done')"),
        say("done"),
        tool("edit", edits=[{"path": "calc.py", "old": "return a - b", "new": "return a + b"}]),
        say("fixed"),
    ])
    result, _ = _run(ws, client, skeptic=skeptic)
    ok = (result.stop == "DONE" and skeptic.calls == 2
          and any(t.get("stop") == "SKEPTIC_UNMET" for t in result.turns))
    return _ok(ok, f"stop={result.stop} skeptic_calls={skeptic.calls}", result)


# --- fire-and-forget autonomy -------------------------------------------------------------


def case_autonomy(ctx: Context) -> CaseResult:
    """An unattended finish that asks a human is rejected; the agent must choose and act."""
    ws = mutation_tree(ctx.workspace)
    client = ScriptedClient([
        say("Should I use a + b?"),
        tool("edit", edits=[{"path": "calc.py", "old": "return a - b", "new": "return a + b"}]),
        say("fixed; chose a + b per SPEC"),
    ])
    result, _ = _run(ws, client, goal=lambda: calc_add(ws)[0], autonomy=True,
                     budget=Budget(max_steps=8))
    passed, detail = calc_add(ws)
    ok = (result.stop == "DONE" and passed
          and any(t.get("stop") == "NEEDS_INPUT" for t in result.turns))
    return _ok(ok, f"stop={result.stop} oracle={detail}", result)


# --- cost and budgets ---------------------------------------------------------------------


def case_token_limit(ctx: Context) -> CaseResult:
    """A total-token cap stops the run with TOKEN_LIMIT, not a wall or step stop."""
    ws = mutation_tree(ctx.workspace)
    client = ScriptedClient([tool("python", code="print('x' * 50)") for _ in range(8)],
                            usage={"prompt_tokens": 10, "total_tokens": 11})
    result, _ = _run(ws, client, budget=Budget(max_steps=20, max_total_tokens=15))
    ok = result.stop == "TOKEN_LIMIT" and result.steps <= 3
    return _ok(ok, f"stop={result.stop} steps={result.steps} tokens={result.usage.get('total_tokens')}", result)


LOOP_CASES = [
    Case("L01_staged_finish", "finish", "a silent final_answer keeps the next report", "loop", case_staged_finish),
    Case("L02_goal_reject", "verification", "a finish is rejected until the oracle passes", "loop", case_goal_reject),
    Case("L03_skipped_pair", "finish", "calls after a staged finish are skipped but paired", "loop", case_skipped_pair),
    Case("L04_bad_args", "tooling", "a wrong keyword is structured bad_args", "loop", case_bad_args),
    Case("L05_repeat_read", "context", "a repeated range is a pointer", "loop", case_repeat_read),
    Case("L06_unknown_tool", "tooling", "an unknown tool is a paired notice", "loop", case_unknown_tool),
    Case("L07_python_write", "mutation", "a raw open() write is journalled", "loop", case_python_write_journal),
    Case("L08_compaction", "context", "a small budget folds old turns", "loop", case_compaction),
    Case("L09_custom_tool", "tooling", "a manifest adds one action", "loop", case_custom_tool),
    Case("L10_skill", "tooling", "a skill is loaded on demand and followed", "loop", case_skill),
    Case("L11_delegate", "context", "delegation returns one observation", "loop", case_delegate),
    Case("L12_subagent", "context", "the real subagent is read-only and answers", "loop", case_subagent_real),
    Case("L13_skeptic", "verification", "a skeptic rejects then accepts a finish", "loop", case_skeptic_reject),
    Case("L14_token_limit", "cost", "a total-token cap stops the run", "loop", case_token_limit),
    Case("L15_autonomy", "autonomy", "an unattended finish cannot ask a human", "loop", case_autonomy),
    Case("L16_docs", "tooling", "docs() returns clean, bounded documentation", "loop", case_docs),
]
