"""Offline self-check for the agent and the benchmark: no network, no API key, no fixtures
touched.

WHAT IT IS
  The one command that tells a coding agent "the code still works" without spending tokens.
  It imports every module, drives the kernel prelude end to end (map/read/search/outline/write/
  edit/splice/apply/commit/changes/diff/undo, batched read and exec, exec/run allowing reads
  and refusing mutation, a bad typed call answered as `bad_args`, the HTML outline and the
  regex search default) and checks the pure helpers (reply parsing, state digests, read
  coverage, the spawn gate, check plans), plus the header index (`bench/agent/headers.py`)
  and the contract CLI (`bench/map.py`).

  python3 bench/smoke.py        exits 0 when every check passes, 1 otherwise

ENTRY POINTS
  python3 bench/smoke.py   the only entry point; `main()` returns the exit code
  check(name, ok, detail)  prints `ok`/`FAIL` and records a failure

INVARIANTS
  - Runs in a temporary directory; nothing under the repo is written, no task is graded, no
    trace is recorded.
  - It is a *contract* test: a failure here means the interface an agent relies on changed
    (kernel prelude, Cell fields, envelopes), not that a model misbehaved.
  - Keep it fast (a few seconds). Network or model calls belong in bench/live.py.

WHERE TO CHANGE
  A new kernel helper or Cell field ... add a check in the kernel section below
  A new suite or task count ........... the suite section at the top
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bench.agent.check as check_mod
import bench.agent.config as config
import bench.agent.llm as llm
import bench.agent.loop as loop
import bench.agent.reply as reply
import bench.agent.repl as repl
import bench.agent.state_loop as state_loop
import bench.agent.tool_loop as tool_loop
import bench.agent.tools as tools
import bench.agent.verbs as verbs
import bench.agent.checks as checks
import bench.agent.headers as headers
import bench.agent.trace as trace
import bench.map as map_cli
import bench.edge as edge
import bench.harness as harness
import bench.runs as runs
import bench.tasks as tasks
import bench.ws as ws
from bench.agent.kernel import Cell, Kernel, PROTOCOL

FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILED.append(name)


# --- a scripted client and kernel, so the turn machine is testable offline -----------------
def _reply(content: str = "", calls: list | None = None, finish: str = "tool_calls") -> dict:
    return {"content": content, "tool_calls": calls or [], "finish": finish}


def _call(cid: str, code: str | None, name: str = "python", arguments: str | None = None) -> dict:
    return {"id": cid,
            "function": {"name": name,
                         "arguments": arguments if arguments is not None else json.dumps({"code": code})}}


def _cell(final: str | None = None, stdout: str = "", stderr: str = "") -> Cell:
    return Cell(stdout=stdout, stderr=stderr, result=None, final=final, duration_ms=1)


class FakeClient:
    """Answers with a scripted reply per turn and keeps a copy of every request."""

    def __init__(self, replies: list[dict]) -> None:
        self.replies = list(replies)
        self.requests: list[list[dict]] = []

    def complete(self, messages: list[dict], tools: list[dict] | None = None, max_tokens: int = 0) -> dict:
        self.requests.append(json.loads(json.dumps(messages)))
        if not self.replies:
            raise AssertionError("the loop asked for a reply after the script ran out")
        item = self.replies.pop(0)
        if isinstance(item, BaseException):
            raise item
        return {"content": item.get("content") or "", "reasoning": "",
                "tool_calls": item.get("tool_calls") or [],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                "latency_ms": 0.0, "finish": item.get("finish") or "tool_calls"}


class FakeKernel:
    """Maps a cell's code to a scripted Cell; `raises` makes dispatch blow up."""

    def __init__(self, cells: dict, raises: set[str] | None = None,
                 typed: dict | None = None) -> None:
        self.cells = dict(cells)
        self.raises = set(raises or ())
        self.typed = dict(typed or {})
        self.codes: list[str] = []
        self.calls: list[tuple[str, dict]] = []

    def run(self, code: str, check_cache: dict | None = None) -> Cell:
        self.codes.append(code)
        if code in self.raises:
            raise RuntimeError("kernel exploded")
        return self.cells.get(code) or _cell()

    def call(self, tool: str, args: dict | None = None, check_cache: dict | None = None) -> Cell:
        self.calls.append((tool, dict(args or {})))
        if tool in self.raises:
            raise RuntimeError("kernel exploded")
        cell = self.typed.get(tool)
        if cell is None:
            cell = _cell()
        if cell.tool is None:
            cell.tool = tool
        return cell


def _run_loop(replies: list[dict], cells: dict, goal=None, raises: set[str] | None = None,
              typed: dict | None = None):
    client = FakeClient(replies)
    kernel = FakeKernel(cells, raises, typed)
    loop_ = tool_loop.ToolLoop(client, kernel, loop.Budget(), goal=goal)
    return loop_.run("smoke", "do the task"), client, kernel


def _paired_messages(messages: list[dict]) -> tuple[list[str], list[str]]:
    assistant = [m for m in messages if m.get("role") == "assistant"][-1]
    ids = [c.get("id") for c in assistant.get("tool_calls") or []]
    paired = [m.get("tool_call_id") for m in messages[messages.index(assistant) + 1:]
              if m.get("role") == "tool"]
    return ids, paired


def main() -> int:
    # --- suites are importable, their ground truth is intact -----------------------------
    check("tasks: read suite", len(tasks.TASKS) == 5, str(len(tasks.TASKS)))
    check("tasks: mutation suite", len(tasks.MUTATION_TASKS) == 3, str(len(tasks.MUTATION_TASKS)))
    check("tasks: by_id", tasks.by_id("T1_count").id == "T1_count")
    check("edge: edge suite", len(edge.EDGE_TASKS) == 10, str(len(edge.EDGE_TASKS)))
    check("harness: loop suite", len(harness.HARNESS_TASKS) == 7, str(len(harness.HARNESS_TASKS)))
    check("edge: by_id", edge.by_id("E1_tests").id == "E1_tests")
    check("edge: ground truth computed", edge.TOTAL_CENTS > 0 and edge.RECORDS > 0)

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
    check("runs: verbs", "final_answer" in runs.VERBS)
    check("ws: workspace helpers", callable(ws.manifest) and callable(ws.clone) and callable(ws.changed))
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

    # --- the contract index: every module explains itself --------------------------------
    rows = headers.index(ROOT)
    check("headers: the index reaches the agent", any(r["path"] == "bench/agent/kernel.py" for r in rows),
          str([r["path"] for r in rows[:3]]))
    gaps = headers.problems(ROOT)
    check("headers: every module header is complete", not gaps, str(gaps[:3]))
    hit = headers.guide(ROOT, query="check plan", limit=3)
    check("headers: a query names the owning module",
          bool(hit["modules"]) and hit["modules"][0]["path"] == "bench/agent/checks.py",
          str([r["path"] for r in hit["modules"]]))
    check("headers: an unknown module is a miss, not an exception",
          headers.guide(ROOT, path="no/such.py")["modules"] == [])
    check("headers: one module reads back whole", "ENTRY POINTS" in headers.guide(ROOT, path="bench/map.py")["text"])
    with contextlib.redirect_stdout(io.StringIO()):
        quiet = map_cli.main(["--check", "--json"])
    check("map: --check is clean", quiet == 0)

    # --- plans and coverage --------------------------------------------------------------
    root = Path(tempfile.mkdtemp(prefix="cact-smoke-"))
    (root / "a.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    specs = checks.CheckSpec.parse([
        {"name": "bench", "cmd": "true", "cheap": True, "scope": ["bench"]},
        {"name": "tui", "cmd": "true", "cheap": True, "scope": ["tui"]},
    ])
    check("checks: scope filters by changed path",
          [spec.name for spec in specs if spec.applies(["tui/src/x.ts"])] == ["tui"])
    plan, results = checks.run_plan(specs, str(root), ["tui/src/x.ts"])
    check("checks: plan runs only the scoped spec",
          plan is not None and plan.cmd == "tui" and list(results) == ["true"], str(plan))
    verifier = checks.Verifier(specs, str(root))
    check("verifier: the plan is its goal", verifier.has_goal)
    check("verifier: cheap subset is the cheap specs",
          [spec.name for spec in verifier.cheap_specs] == ["bench", "tui"],
          str(verifier.cheap_specs))
    check("verifier: scope decides whether the cheap run applies",
          verifier.should_run(["tui/src/x.ts"], 0.0) is True
          and verifier.should_run(["tui/src/x.ts"], 19.9) is False
          and verifier.should_run(["tui/src/x.ts"], checks.HEALTH_WINDOW) is True,
          "throttle window")
    check("verifier: a docs-only change runs no scoped cheap spec",
          checks.Verifier(specs, str(root)).should_run(["README.md"], 0.0) is False)
    results = verifier.run(["tui/src/x.ts"])
    check("verifier: run returns per-command results",
          list(results) == ["true"] and results["true"].ok, str(list(results)))
    check("verifier: run fills the kernel cache",
          list(verifier.cache()) == ["true"] and verifier.cache()["true"]["ok"] is True,
          str(verifier.cache()))
    verifier.invalidate()
    check("verifier: a mutation drops the cache and the last check",
          verifier.cache() == {} and verifier.last is None)

    marker = root / "second-ran"
    short = checks.CheckSpec.parse([
        {"name": "first", "cmd": "false", "cheap": True},
        {"name": "second", "cmd": f"touch {marker}", "cheap": True},
    ])
    short_verifier = checks.Verifier(short, str(root))
    short_results = short_verifier.run()
    check("verifier: the first failure short-circuits the plan",
          list(short_results) == ["false"] and not marker.exists()
          and short_verifier.last.state == "failed", str(list(short_results)))

    counter = root / "goal-runs"
    goal_specs = checks.CheckSpec.parse([{"name": "count", "cmd": f"printf x >> {counter}"}])
    goal_verifier = checks.Verifier(goal_specs, str(root))
    first_ok, first_check = goal_verifier.authoritative()
    second_ok, _ = goal_verifier.authoritative()
    check("verifier: the goal runs the whole plan on every attempt",
          first_ok and second_ok and counter.read_text() == "xx"
          and first_check.state == "passed", counter.read_text())
    legacy_ok, legacy_check = checks.Verifier(goal_specs, str(root), legacy=True).authoritative()
    check("verifier: the legacy goal answers a bare bool",
          legacy_ok is True and legacy_check is None, str(legacy_ok))
    oracle = checks.Verifier(check=lambda: check_mod.Check(cmd="oracle", state="passed"),
                             cwd=str(root), cheap_enabled=False)
    check("verifier: a bare check is the goal",
          oracle.has_goal and oracle.authoritative()[0] is True)
    check("verifier: --health off disables the cheap run",
          oracle.should_run([], 0.0) is False)
    check("state: coverage merge",
          state_loop.merge_ranges([(5, 9), (1, 4), (20, 20), (7, 12)]) == [[1, 12], [20, 20]],
          str(state_loop.merge_ranges([(5, 9), (1, 4), (20, 20), (7, 12)])))
    check("state: coverage gaps", state_loop.gaps([(1, 10), (30, 40)], 50) == [[11, 29], [41, 50]])
    check("state: coverage count", state_loop.covered([(1, 10), (5, 20)]) == 20)
    check("state: no ranges means no gaps",
          state_loop.gaps([], 4) == [[1, 4]] and state_loop.gaps([(1, 4)], 4) == [])
    check("state: an undigested read is exactly what read() delivered",
          state_loop.delivered_ranges({"a.txt": {"ranges": [[1, 2], [2, 5]], "total": 9}}, "  1| x")
          == {"a.txt": [[1, 5]]})
    check("state: a digested raw read is not guessed",
          state_loop.delivered_ranges({"a.txt": {"ranges": [[1, 2]], "total": 2}}, "alpha\nbeta", True) == {})
    check("state: a digested numbered read keeps only the numbers it shows",
          state_loop.delivered_ranges({"a.txt": {"ranges": [[1, 9]], "total": 9}},
                                      "     1| a\n     2| b\n     9| i", True) == {"a.txt": [[1, 2], [9, 9]]},
          str(state_loop.delivered_ranges({"a.txt": {"ranges": [[1, 9]], "total": 9}},
                                          "     1| a\n     2| b\n     9| i", True)))
    batch_reads = {"a.txt": {"ranges": [[1, 3]], "total": 3},
                   "b.txt": {"ranges": [[1, 3]], "total": 3}}
    batch_body = "# a.txt\n     1| a1\n     2| a2\n# b.txt\n     1| b1"
    check("state: a digested batch read splits coverage by path header",
          state_loop.delivered_ranges(batch_reads, batch_body, True)
          == {"a.txt": [[1, 2]], "b.txt": [[1, 1]]},
          str(state_loop.delivered_ranges(batch_reads, batch_body, True)))
    check("state: a digested batch with no headers claims nothing",
          state_loop.delivered_ranges(batch_reads, "     1| a1\n     2| a2", True) == {},
          str(state_loop.delivered_ranges(batch_reads, "     1| a1\n     2| a2", True)))

    # --- goal check ----------------------------------------------------------------------
    passed = check_mod.run_check("true", str(root))
    failed = check_mod.run_check("false", str(root))
    slow = check_mod.run_check("sleep 3", str(root), timeout=0.2)
    check("check: passed", passed.ok and passed.state == "passed")
    check("check: failed", not failed.ok and failed.state == "failed")
    check("check: timeout is unrunnable, not failed", slow.state == "unrunnable", slow.state)

    # --- protocol: a stale child is rejected, the current one is accepted -----------------
    def _fake_kernel(protocol):
        script = (
            "import json, sys\n"
            "for line in sys.stdin:\n"
            "    json.loads(line)\n"
            "    sys.__stdout__.write('@@K@@' + json.dumps({'protocol': __P__, 'stdout': 'fake'}) + '\\n')\n"
            "    sys.__stdout__.flush()\n"
        ).replace("__P__", str(protocol))
        fake = Kernel(root, timeout=10)
        fake._argv = lambda: [sys.executable, "-u", "-c", script]
        return fake

    stale = _fake_kernel(PROTOCOL - 1)
    try:
        cell = stale.run("pass")
        check("kernel: a stale protocol is rejected and restarted",
              cell.restarted and "protocol mismatch" in cell.stderr and not cell.stdout,
              cell.stderr[:200])
    finally:
        stale.stop()
    current = _fake_kernel(PROTOCOL)
    try:
        cell = current.run("pass")
        check("kernel: the current protocol is accepted",
              cell.stdout == "fake" and not cell.restarted, f"{cell.stdout!r} {cell.stderr!r}")
    finally:
        current.stop()

    # --- kernel: the action space an agent actually gets ---------------------------------
    typed_kernel = Kernel(root, timeout=20, writable=True, prelude="all", allow_run=True,
                          allow_shell=False)
    try:
        cell = typed_kernel.run("print('run' in globals())")
        check("kernel: typed mode removes the python run() helper",
              cell.stdout.strip() == "False", cell.stdout[:120])
    finally:
        typed_kernel.stop()

    kernel = Kernel(root, timeout=20, writable=True, prelude="all", allow_run=True)
    try:
        cell = kernel.run("print(read('a.txt')['total_lines'])")
        check("kernel: read", "2" in cell.stdout, cell.stdout[:120])

        cell = kernel.run("write('b.txt', 'one\\ntwo\\n')")
        check("kernel: write is journalled", cell.changed == ["b.txt"], str(cell.changed))

        cell = kernel.run("open('c.txt', 'w').write('written\\n')")
        check("kernel: an unclosed open() write is journalled",
              (root / "c.txt").exists() and "c.txt" in cell.changed,
              f"{cell.changed} {cell.result}")

        cell = kernel.run("edit('b.txt', 'one', 'uno')")
        check("kernel: edit reports the file", cell.changed == ["b.txt"], str(cell.changed))
        check("kernel: diff shows the hunk", "-one" in cell.delta and "+uno" in cell.delta, cell.delta[:120])
        check("kernel: deltas carries the per-file hunk",
              "+uno" in cell.deltas.get("b.txt", "") and "b.txt" in cell.changed, str(cell.deltas)[:120])

        kernel.run("write('gone.txt', 'x')")
        cell = kernel.run("import os; os.remove('gone.txt')")
        check("kernel: an untyped deletion is a change",
              cell.changed == ["gone.txt"], str(cell.changed))

        cell = kernel.run("write('ghost.txt', 'x'); import os; os.remove('ghost.txt')")
        check("kernel: a created-then-deleted file is not a change",
              cell.changed == [] and not (root / "ghost.txt").exists(), str(cell.changed))

        cell = kernel.run("print(type(diff()).__name__); print(diff()[:20])")
        check("kernel: diff() returns text, not a dict",
              "str" in cell.stdout and "---" in cell.stdout, cell.stdout[:160])

        cell = kernel.run("print(changes())")
        check("kernel: changes lists the file", "b.txt" in cell.stdout, cell.stdout[:120])

        cell = kernel.run("undo('b.txt')")
        check("kernel: undo drops a file created this session", not (root / "b.txt").exists(), cell.stdout[:120])

        kernel.run("edit('a.txt', 'beta', 'gamma')")
        check("kernel: edit applied", "gamma" in (root / "a.txt").read_text())
        kernel.run("undo('a.txt')")
        check("kernel: undo restores session-start text",
              (root / "a.txt").read_text() == "alpha\nbeta\n", (root / "a.txt").read_text()[:80])

        kernel.run("write('batch_a.txt', 'one\\ntwo\\n'); write('batch_b.txt', 'red\\nblue\\n')")
        cell = kernel.call("edit", {"edits": [
            {"path": "batch_a.txt", "old": "two", "new": "TWO"},
            {"path": "batch_b.txt", "old": "blue", "new": "BLUE"},
        ]})
        check("kernel: call(edit) applies a batch in one cell",
              cell.tool == "edit" and set(cell.changed) == {"batch_a.txt", "batch_b.txt"}
              and "TWO" in (root / "batch_a.txt").read_text()
              and "BLUE" in (root / "batch_b.txt").read_text(), str(cell.changed))
        cell = kernel.call("edit", {"path": "batch_a.txt",
                                        "edits": [{"old": "TWO", "new": "two"}]})
        check("kernel: call(edit) accepts a default path",
              "two" in (root / "batch_a.txt").read_text(), (cell.result or "")[:200])
        cell = kernel.call("edit", {"edits": [
            {"path": "batch_a.txt", "old": "one", "new": "ONE"},
            {"path": "batch_b.txt", "old": "missing", "new": "x"},
        ]})
        check("kernel: call(edit) is atomic on a bad old string",
              "not_found" in (cell.result or "") and cell.rejected and not cell.failed
              and (root / "batch_a.txt").read_text().startswith("one"),
              (cell.result or "")[:240])

        cell = kernel.run("grep('beta', '.')")
        check("kernel: grep", "beta" in str(cell.result), str(cell.result)[:120])

        cell = kernel.run("map('.')")
        check("kernel: map", "a.txt" in str(cell.result), str(cell.result)[:120])

        (root / "demo.py").write_text(
            '"""A demo module for the index.\n\nWHAT IT IS\n  demo only\n\nENTRY POINTS\n'
            '  demo()\n\nINVARIANTS\n  none\n\nWHERE TO CHANGE\n  here\n"""\n', encoding="utf-8")
        cell = kernel.run("r = guide('demo'); print(r['kind'], [m['path'] for m in r['modules']])")
        check("kernel: guide() finds a documented module",
              "demo.py" in cell.stdout, (cell.stdout + cell.stderr)[:200])
        cell = kernel.run("print(guide('nothing-matches-this')['text'])")
        check("kernel: guide() reports a miss as text",
              "no documented module" in cell.stdout, (cell.stdout + cell.stderr)[:200])

        cell = kernel.run("run('cat a.txt')")
        allowed = str(cell.result)
        check("kernel: run() allows a read-only command",
              "command" in allowed and "alpha" in allowed, allowed[:200])

        cell = kernel.run("run('python3 -c \"print(1)\"')")
        allowed = str(cell.result)
        check("kernel: run() allows -c scripting",
              "command" in allowed or "exit_code" in allowed, allowed[:200])

        cell = kernel.run("r = run(['echo one', 'echo two']); print(r['kind'], r['count'], 'one' in r['stdout'], 'two' in r['stdout'])")
        check("kernel: run() accepts a list and concatenates its output",
              "commands 2 True True" in cell.stdout, (cell.stdout + cell.stderr)[:200])
        cell = kernel.run("r = run(['echo ok', 'rm -rf a.txt']); print(r['ok'], [c.get('kind') for c in r['commands']], read('a.txt')['total_lines'])")
        check("kernel: every command in a list is gated",
              "False" in cell.stdout and "wrong_call" in cell.stdout and " 2" in cell.stdout,
              (cell.stdout + cell.stderr)[:200])

        cell = kernel.run("run('sed -i s/alpha/omega/ a.txt')")
        refusal = str(cell.result)
        check("kernel: run() refuses a mutating shell verb",
              "wrong_call" in refusal and "edit(path" in refusal, refusal[:200])
        check("kernel: the refused mutation did not run",
              "alpha" in (root / "a.txt").read_text(), (root / "a.txt").read_text()[:80])

        cell = kernel.run("run('git add a.txt')")
        allowed = str(cell.result)
        check("kernel: run() allows git add/commit",
              "wrong_call" not in allowed and "command" in allowed, allowed[:200])

        cell = kernel.run("run('git checkout -- a.txt')")
        refusal = str(cell.result)
        check("kernel: run() refuses a working-tree overwrite",
              "wrong_call" in refusal and "git checkout" in refusal, refusal[:200])

        kernel.run("run('git init -q && git config user.email a@b && git config user.name a && git add a.txt && git commit -qm init')")
        kernel.run("write('git_new.txt', 'x')")
        cell = kernel.call("git", {"action": "status"})
        check("kernel: call(git) status parses the working tree",
              cell.tool == "git" and "git_new.txt" in cell.stdout and "??" in cell.stdout,
              (cell.stdout + cell.stderr)[:240])
        cell = kernel.call("git", {"action": "log", "limit": 1})
        check("kernel: call(git) log returns commits",
              "init" in cell.stdout and '"sha"' in (cell.result or ""),
              (cell.stdout + (cell.result or ""))[:240])
        cell = kernel.call("git", {"action": "files"})
        check("kernel: call(git) files lists tracked paths",
              "a.txt" in cell.stdout and "git_new.txt" not in cell.stdout,
              (cell.stdout + cell.stderr)[:240])
        cell = kernel.call("git", {"action": "branch"})
        check("kernel: call(git) branch names the current branch",
              bool(cell.stdout.strip()), (cell.stdout + cell.stderr)[:200])

        cell = kernel.run("r=run('echo shape'); print(r.stdout.strip(), r['stdout'].strip())")
        check("kernel: run() answers both r.stdout and r['stdout']",
              "shape shape" in cell.stdout, (cell.stdout + cell.stderr)[:200])
        cell = kernel.run("print(read('a.txt', raw=True)[:3])")
        check("kernel: read(raw=True) can be sliced", "alp" in cell.stdout, (cell.stdout + cell.stderr)[:200])
        cell = kernel.run("print(read('a.txt')['content'][:20])")
        check("kernel: read()['content'] is the delivered text", "alpha" in cell.stdout, (cell.stdout + cell.stderr)[:200])

        kernel.run("write('call_a.txt', 'one\\ntwo\\n'); write('call_b.txt', 'red\\nblue\\n')")
        cell = kernel.call("map", {"path": "."})
        check("kernel: call(map) lists files without a shell",
              cell.tool == "map" and "call_a.txt" in cell.stdout,
              (cell.stdout + cell.stderr)[:240])
        cell = kernel.call("read", {"path": ["call_a.txt", "call_b.txt"]})
        check("kernel: call(read) runs a typed verb through the same pipeline",
              cell.tool == "read" and "call_a.txt" in cell.stdout
              and cell.reads.get("call_a.txt", {}).get("total") == 2, (cell.stdout + cell.stderr)[:200])
        cell = kernel.call("read", {"path": "call_a.txt", "start": 1, "end": 1})
        check("kernel: call(read) accepts start/end line ranges",
              "one" in cell.stdout and "two" not in cell.stdout, (cell.stdout + cell.stderr)[:200])
        cell = kernel.call("search", {"pattern": "red", "path": ["call_a.txt", "call_b.txt"]})
        check("kernel: call(search) searches a path list",
              "call_b.txt:1" in cell.stdout, (cell.stdout + cell.stderr)[:200])
        cell = kernel.call("search", {"pattern": "RED", "path": ".", "include": "*.txt",
                                      "context": 1, "ignore_case": True})
        check("kernel: call(search) filters include and adds context",
              "call_b.txt:1:" in cell.stdout and "call_b.txt:2-" in cell.stdout
              and "call_b.txt:2- blue" in cell.stdout, (cell.stdout + cell.stderr)[:300])
        cell = kernel.call("splice", {"path": "call_a.txt", "start": 2, "end": 2, "text": "TWO"})
        check("kernel: call(splice) journals through the typed path",
              cell.tool == "splice" and "call_a.txt" in cell.changed
              and "TWO" in (root / "call_a.txt").read_text(), (cell.stdout + cell.stderr)[:200])
        cell = kernel.call("exec", {"argv": ["echo", "one"]})
        check("kernel: call(exec) runs a program by argv",
              cell.tool == "exec" and "one" in cell.stdout and not cell.failed,
              (cell.stdout + cell.stderr)[:200])
        cell = kernel.call("exec", {"commands": [["echo", "one"], ["echo", "two"]]})
        check("kernel: call(exec) runs several argv commands",
              cell.tool == "exec" and "one" in cell.stdout and "two" in cell.stdout,
              (cell.stdout + cell.stderr)[:200])
        cell = kernel.call("exec", {"argv": "echo one"})
        check("kernel: call(exec) refuses a shell string",
              "shell_string" in (cell.result or ""), (cell.result or "")[:200])
        cell = kernel.call("exec", {"code": "print(1)"})
        check("kernel: call(exec) turns a bad keyword into bad_args",
              "bad_arguments" in (cell.result or "") and not cell.failed,
              (cell.result or "")[:240])
        cell = kernel.call("exec", {"argv": ["bash", "-lc", "echo one"]})
        check("kernel: call(exec) points a shell -c string at the shell field",
              "shell_via_exec" in (cell.result or "") and "exec(shell=" in (cell.result or "")
              and not cell.stdout, (cell.result or "")[:260])
        cell = kernel.call("exec", {"shell": "echo one"})
        check("kernel: call(exec) runs an explicit shell string",
              "one" in cell.stdout and cell.tool == "exec", (cell.stdout + cell.stderr)[:200])
        cell = kernel.call("exec", {"argv": ["sed", "-i", "s/one/1/", "call_a.txt"]})
        check("kernel: call(exec) refuses a mutating program",
              "mutation_via_exec" in (cell.result or "") and "one" in (root / "call_a.txt").read_text(),
              (cell.result or "")[:200])
        (root / "outline.md").write_text("# Title\n\n## Section\ntext\n", encoding="utf-8")
        (root / "outline.py").write_text("class A:\n    def m(self):\n        pass\n\ndef top():\n    pass\n", encoding="utf-8")
        cell = kernel.call("outline", {"path": "outline.md"})
        check("kernel: call(outline) maps markdown headings",
              "1: h1 Title" in cell.stdout and "3: h2 Section" in cell.stdout,
              (cell.stdout + cell.stderr)[:220])
        cell = kernel.call("outline", {"path": "outline.py"})
        check("kernel: call(outline) maps python defs and methods",
              "1: class A" in cell.stdout and "2: method A.m" in cell.stdout
              and "5: function top" in cell.stdout, (cell.stdout + cell.stderr)[:220])
        cell = kernel.call("outline", {"path": "outline.md", "max_entries": 1})
        check("kernel: call(outline) respects max_entries",
              "Title" in cell.stdout and "Section" not in cell.stdout, cell.stdout[:160])
        (root / "outline.html").write_text(
            '<header id="top">\n  <h1>Title</h1>\n</header>\n'
            '<section class="card" id="hero">\n  <h2>Question</h2>\n</section>\n',
            encoding="utf-8")
        cell = kernel.call("outline", {"path": "outline.html"})
        check("kernel: call(outline) maps HTML sections and headings",
              "section#hero" in cell.stdout and "heading h2: Question" in cell.stdout
              and "header#top" in cell.stdout, (cell.stdout + cell.stderr)[:240])
        (root / "outline.js").write_text(
            'const view = `\n<section id="main">\n  <h2>Inside JS</h2>\n</section>\n`\n',
            encoding="utf-8")
        cell = kernel.call("outline", {"path": "outline.js"})
        check("kernel: call(outline) sees HTML landmarks in a JS template",
              "section#main" in cell.stdout and "heading h2: Inside JS" in cell.stdout,
              (cell.stdout + cell.stderr)[:240])
        cell = kernel.run("print(help()['text'])")
        check("kernel: help() documents the typed tools",
              "exec(" in cell.stdout and "edit(" in cell.stdout,
              (cell.stdout + cell.stderr)[:260])
        cell = kernel.run("print(help('read')['text'])")
        check("kernel: help(topic) returns live signatures",
              "read(" in cell.stdout, (cell.stdout + cell.stderr)[:200])
        kernel.run("undo('call_a.txt'); undo('call_b.txt')")

        kernel.run("write('multi_a.txt', 'one\\ntwo\\nthree\\n'); write('multi_b.txt', 'red\\nblue\\n')")
        cell = kernel.run("r = read(['multi_a.txt', 'multi_b.txt']); print(r['kind'], r['count'], 'multi_a' in r['text'], 'multi_b' in r['text'])")
        check("kernel: read() accepts a list and concatenates the delivery",
              "files 2 True True" in cell.stdout, (cell.stdout + cell.stderr)[:200])
        check("kernel: a batched read records every path's ranges",
              {"multi_a.txt", "multi_b.txt"} <= set(cell.reads)
              and cell.reads["multi_a.txt"]["total"] == 3, str(cell.reads)[:200])

        cell = kernel.run("print(splice('multi_a.txt', 2, 2, 'TWO')['kind'], read('multi_a.txt')['text'])")
        check("kernel: splice replaces a 1-based inclusive line range",
              "splice" in cell.stdout and "TWO" in cell.stdout
              and (root / "multi_a.txt").read_text() == "one\nTWO\nthree\n", cell.stdout[:200])
        cell = kernel.run("print(splice('multi_a.txt', 1, 0, 'zero')['kind'])")
        check("kernel: splice inserts when end < start",
              "splice" in cell.stdout
              and (root / "multi_a.txt").read_text() == "zero\none\nTWO\nthree\n", cell.stdout[:200])
        cell = kernel.run("print(splice('multi_a.txt', 1, 1, '')['dels'])")
        check("kernel: splice deletes a range",
              "1" in cell.stdout
              and (root / "multi_a.txt").read_text() == "one\nTWO\nthree\n", cell.stdout[:200])
        cell = kernel.run("print(splice('multi_a.txt', 99, 99, 'x')['ok'])")
        check("kernel: splice refuses a range past the end",
              "False" in cell.stdout, cell.stdout[:200])
        kernel.run("undo('multi_a.txt')")
        check("kernel: undo covers a splice",
              not (root / "multi_a.txt").exists(), str((root / "multi_a.txt").exists()))

        cell = kernel.run("print(run('echo \"a > b\"')['stdout'].strip())")
        check("kernel: a quoted > is an argument, not a redirect",
              "a > b" in cell.stdout and "wrong_call" not in str(cell.result), (cell.stdout + cell.stderr)[:200])

        cell = kernel.run("run('echo x > z.txt')")
        refusal = str(cell.result)
        check("kernel: run() refuses redirection into a file",
              "wrong_call" in refusal and not (root / "z.txt").exists(), refusal[:200])

        cell = kernel.run("run('rm -rf /tmp/cact-smoke-outside')")
        allowed = str(cell.result)
        check("kernel: run() allows a mutation outside the workspace",
              "wrong_call" not in allowed and "'ok': True" in allowed, allowed[:200])

        cell = kernel.run("run('rm -rf /home')")
        refusal = str(cell.result)
        check("kernel: run() refuses a mutation outside the scratch roots",
              "wrong_call" in refusal, refusal[:200])

        cell = kernel.run("run('rm -rf a.txt')")
        refusal = str(cell.result)
        check("kernel: run() still refuses a workspace mutation",
              "wrong_call" in refusal and (root / "a.txt").exists(), refusal[:200])
        check("kernel: a refused run() still carries exit_code",
              "'exit_code': None" in refusal, refusal[:200])

        cell = kernel.run("run('cp a.txt /tmp/cact-smoke-copy')")
        allowed = str(cell.result)
        check("kernel: run() allows copying out of the workspace",
              "wrong_call" not in allowed and (root / "a.txt").exists(), allowed[:200])

        kernel.run("print('needle-42')")
        cell = kernel.run("print(recall('needle-42'))")
        check("kernel: recall finds earlier cell output with its handle",
              "needle-42" in cell.stdout and "handle" in cell.stdout, cell.stdout[:200])

        cell = kernel.run("print(run('sleep 300 >/dev/null 2>&1 & echo $!')['stdout'].strip())")
        orphan = int(cell.stdout.strip().splitlines()[-1])
        alive = Path(f"/proc/{orphan}").exists()
        kernel._restart()
        gone = False
        for _ in range(50):
            if not Path(f"/proc/{orphan}").exists():
                gone = True
                break
            time.sleep(0.02)
        check("kernel: restart reaps an orphaned run() process group",
              alive and gone, f"pid={orphan} alive_before={alive} gone_after={gone}")
        cell = kernel.run("print(recall('needle-42'))")
        check("kernel: recall survives a kernel restart",
              "needle-42" in cell.stdout and "handle" in cell.stdout, cell.stdout[:200])

        cell = kernel.run("apply('--- a/a.txt\\n+++ b/a.txt\\n@@ -1,2 +1,2 @@\\n alpha\\n-beta\\n+delta\\n')")
        check("kernel: apply a unified diff",
              "apply" in str(cell.result) and "delta" in (root / "a.txt").read_text(), str(cell.result)[:160])
        kernel.run("undo('a.txt')")

        cell = kernel.run("commit({'x.txt': 'x\\n', 'y.txt': 'y\\n'})")
        check("kernel: commit writes several files",
              (root / "x.txt").exists() and (root / "y.txt").exists(), str(cell.result)[:160])
        kernel.run("undo()")

        big = root / "big.txt"
        big.write_text("\n".join("line %d" % i for i in range(900)), encoding="utf-8")
        cell = kernel.run("r=read('big.txt'); print(r['shown'], r['truncated'])")
        check("kernel: read caps delivery and reports next",
              "True" in cell.stdout and cell.reads.get("big.txt", {}).get("total") == 900, cell.stdout[:120])
        cell = kernel.run("import subprocess; subprocess.run(['rm', 'a.txt'], capture_output=True)")
        gate = str(cell.result)
        check("kernel: spawn gate blocks a mutating process",
              "wrong_call" in gate and "os.remove(path)" in gate, gate[:200])
        check("kernel: the blocked spawn did not run", (root / "a.txt").exists(), str((root / "a.txt").exists()))
        cell = kernel.run("import subprocess; print(subprocess.run(['echo', 'ok'], capture_output=True, text=True).stdout.strip())")
        check("kernel: spawn gate allows programs", "ok" in cell.stdout, cell.stdout[:120])
        cell = kernel.run("import subprocess, sys; r=subprocess.run([sys.executable, '-c', \"print('a > b')\"], capture_output=True, text=True); print(r.returncode, r.stdout.strip())")
        check("kernel: a list argv does not treat > as a shell redirect",
              "0 a > b" in cell.stdout, (cell.stdout + cell.stderr)[:200])
        cell = kernel.run("import subprocess; subprocess.run(['bash', '-c', 'echo x > q.txt'])")
        check("kernel: bash -c redirection is still refused",
              "changes files through a process" in cell.stderr and not (root / "q.txt").exists(),
              (cell.stderr + cell.stdout)[:200])
        cell = kernel.run("run('echo cached-check')", check_cache={"echo cached-check": "PASSED"})
        check("kernel: a plan check answers run() from the cache",
              "check_cached" in str(cell.result) and "PASSED" in str(cell.result), str(cell.result)[:160])
        probe = state_loop.StateLoop(object(), kernel, loop.Budget(), keep_messages=4)
        shown = Cell(stdout="[stdout]\nalpha\nbeta\n", stderr="", result=None, final=None,
                     duration_ms=1, reads={"a.txt": {"ranges": [[1, 2]], "total": 2}})
        probe._observe(shown, 1)
        check("state: a printed read counts as delivered",
              probe.coverage.get("a.txt", {}).get("ranges") == [[1, 2]], str(probe.coverage))
        # A card the output budget actually digests claims only the numbered lines it kept:
        # head, error lines and tail -- no file is opened to work that out.
        numbered = "\n".join(f"{n:>6}| " + ("error: boom" if n == 300 else f"line {n}")
                             for n in range(1, 901))
        huge = Cell(stdout=numbered, stderr="", result=None, final=None, duration_ms=1,
                    reads={"huge.txt": {"ranges": [[1, 900]], "total": 900}})
        card, visible = state_loop.render_observation(huge, 1, 0, None, 800, {})
        ranges = visible.get("huge.txt")
        check("state: a digested card claims only the lines it kept",
              bool(ranges) and 0 < state_loop.covered(ranges) < 900
              and ranges[0][0] == 1 and ranges[-1][1] == 900 and [300, 300] in ranges,
              f"{ranges} covered={state_loop.covered(ranges or [])}")
        check("state: the digested card still names the ranges and the handle",
              "[read] huge.txt" in card and "next " in card, card[:160])
        raw_text = "\n".join(f"row {n}" for n in range(1, 401))
        raw_cell = Cell(stdout=raw_text, stderr="", result=None, final=None, duration_ms=1,
                        reads={"raw.txt": {"ranges": [[1, 2]], "total": 2}})
        check("state: a digested raw read claims nothing",
              state_loop.render_observation(raw_cell, 2, 0, None, 800, {})[1] == {},
              raw_text[:40])
        (root / "blank.txt").write_text("a\n\nb\n", encoding="utf-8")
        blank = Cell(stdout="\n".join(f"{n:>6}| " + ("" if n == 2 else f"line {n}") for n in range(1, 901)),
                     stderr="", result=None, final=None, duration_ms=1,
                     reads={"blank.txt": {"ranges": [[1, 3]], "total": 3}})
        _, blank_visible = state_loop.render_observation(blank, 3, 0, None, 800, {})
        check("state: a blank numbered line still counts as delivered",
              blank_visible.get("blank.txt") == [[1, 3]], str(blank_visible.get("blank.txt"))[:120])
        messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "p"}]
        for i in range(6):
            messages.append({"role": "assistant", "content": "", "tool_calls": [{"id": str(i)}]})
            messages.append({"role": "tool", "tool_call_id": str(i), "content": "x"})
        probe.last_prompt_tokens = 10 ** 9
        probe.changed = {"a.txt"}
        probe.deltas = {"a.txt": "@@ -1 +1 @@\n-a\n+b"}
        probe._compact(messages)
        folded = "\n".join(str(item.get("content") or "") for item in messages)
        check("state: fold regenerates session state from the journal",
              "[session state]" in folded and "[diff]" in folded and probe.compactions == 1, folded[:160])

        cell = kernel.run("final_answer('done')")
        check("kernel: final_answer", cell.final == "done" and not cell.stdout.strip(), str(cell.final))

        cell = kernel.run("print('noise'); final_answer('x')")
        check("kernel: a printing cell still reports final (the loop rejects it)",
              cell.final == "x" and "noise" in cell.stdout, str(cell.final))
    finally:
        kernel.stop()

    # --- the turn machine: finish protocol, stop vocabulary and call pairing --------------
    check("turn: the stop vocabulary is fixed",
          set(tool_loop.STOPS) == {"DONE", "VERIFIED", "STEP_LIMIT", "WALL_LIMIT", "STAGED",
                                   "GOAL_UNMET", "TRUNCATED", "BAD_ARGS", "LLM_FAILED"},
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
        [_reply(calls=[_call("c1", "final_answer('now')"), _call("c2", late)])],
        {"final_answer('now')": _cell(final="now")})
    check("turn: an accepted final_answer stops the later calls in that turn",
          late not in kernel.codes and result.final == "now" and result.stop == tool_loop.DONE,
          str(kernel.codes))
    check("turn: a skipped call survives in the turn record",
          len(result.turns[0]["calls"]) == 2 and result.turns[0]["calls"][1].get("skipped") is True,
          str(result.turns[0]["calls"])[:300])

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
          and fail_summary["llm_calls"] == 2,
          f"{fail_result.stop} {fail_summary}")

    push_client = FakeClient([_reply(content="budget summary", finish="stop")])
    push_result = tool_loop.ToolLoop(push_client, FakeKernel({}), loop.Budget(max_steps=0)).run(
        "smoke", "task")
    check("turn: the budget-forced _push counts as an llm call",
          push_result.steps == 0 and push_result.llm_calls == 1
          and push_result.stop == tool_loop.STEP_LIMIT,
          f"steps={push_result.steps} llm_calls={push_result.llm_calls} stop={push_result.stop}")

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

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed: " + ", ".join(FAILED))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
