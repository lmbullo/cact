"""Offline self-check for the agent and the benchmark: no network, no API key, no fixtures
touched.

WHAT IT IS
  The one command that tells a coding agent "the code still works" without spending tokens.
  It imports every module, drives the kernel prelude end to end (read/write/edit/changes/
  diff/undo, run()'s redirect and refusal) and checks the pure helpers (reply parsing, state
  digests, check parsing, tools registry).

  python3 bench/smoke.py        exits 0 when every check passes, 1 otherwise

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

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bench.agent.check as check_mod
import bench.agent.config as config
import bench.agent.llm as llm
import bench.agent.loop as loop
import bench.agent.reply as reply
import bench.agent.state_loop as state_loop
import bench.agent.tools as tools
import bench.agent.trace as trace
import bench.edge as edge
import bench.runs as runs
import bench.tasks as tasks
import bench.ws as ws
from bench.agent.kernel import Kernel

FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILED.append(name)


def main() -> int:
    # --- suites are importable, their ground truth is intact -----------------------------
    check("tasks: read suite", len(tasks.TASKS) == 5, str(len(tasks.TASKS)))
    check("tasks: mutation suite", len(tasks.MUTATION_TASKS) == 3, str(len(tasks.MUTATION_TASKS)))
    check("tasks: by_id", tasks.by_id("T1_count").id == "T1_count")
    check("edge: edge suite", len(edge.EDGE_TASKS) == 10, str(len(edge.EDGE_TASKS)))
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
    card = state_loop.digest("\n".join(f"line {i}" for i in range(400)), 800, "_OBS[0]")
    check("state: digest names the handle", "_OBS[0]" in card and len(card) < 4000)

    check("loop: budget defaults", loop.Budget().max_steps == 200)
    check("config: mode is a loop name", config.MODE in ("state", "tool", "repl"))
    check("llm: error type", issubclass(llm.LLMError, RuntimeError))
    check("trace: render is callable", callable(trace.render))
    check("runs: verbs", "final_answer" in runs.VERBS)
    check("ws: workspace helpers", callable(ws.manifest) and callable(ws.clone) and callable(ws.changed))

    # --- tools registry (classic tools mode) ---------------------------------------------
    root = Path(tempfile.mkdtemp(prefix="cact-smoke-"))
    (root / "a.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    registry = tools.Registry()
    for factory in (tools.make_read(root), tools.make_find(root), tools.make_calc(), tools.make_map(root)):
        registry.add(factory)
    tool, argv, problem = registry.prepare("read", {"path": "a.txt"})
    check("tools: prepare", problem is None and tool is not None)
    env = registry.execute(tool, argv)
    check("tools: read envelope", bool(env.get("ok")) and env.get("total_lines") == 2, str(env)[:120])

    # --- goal check ----------------------------------------------------------------------
    passed = check_mod.run_check("true", str(root))
    failed = check_mod.run_check("false", str(root))
    slow = check_mod.run_check("sleep 3", str(root), timeout=0.2)
    check("check: passed", passed.ok and passed.state == "passed")
    check("check: failed", not failed.ok and failed.state == "failed")
    check("check: timeout is unrunnable, not failed", slow.state == "unrunnable", slow.state)

    # --- kernel: the action space an agent actually gets ---------------------------------
    kernel = Kernel(root, timeout=20, writable=True, prelude="all", allow_run=True)
    try:
        cell = kernel.run("print(read('a.txt')['total_lines'])")
        check("kernel: read", "2" in cell.stdout, cell.stdout[:120])

        cell = kernel.run("write('b.txt', 'one\\ntwo\\n')")
        check("kernel: write is journalled", cell.changed == ["b.txt"], str(cell.changed))

        cell = kernel.run("edit('b.txt', 'one', 'uno')")
        check("kernel: edit reports the file", cell.changed == ["b.txt"], str(cell.changed))
        check("kernel: diff shows the hunk", "-one" in cell.delta and "+uno" in cell.delta, cell.delta[:120])

        cell = kernel.run("print(changes())")
        check("kernel: changes lists the file", "b.txt" in cell.stdout, cell.stdout[:120])

        cell = kernel.run("undo('b.txt')")
        check("kernel: undo drops a file created this session", not (root / "b.txt").exists(), cell.stdout[:120])

        kernel.run("edit('a.txt', 'beta', 'gamma')")
        check("kernel: edit applied", "gamma" in (root / "a.txt").read_text())
        kernel.run("undo('a.txt')")
        check("kernel: undo restores session-start text",
              (root / "a.txt").read_text() == "alpha\nbeta\n", (root / "a.txt").read_text()[:80])

        cell = kernel.run("grep('beta', '.')")
        check("kernel: grep", "beta" in str(cell.result), str(cell.result)[:120])

        cell = kernel.run("map('.')")
        check("kernel: map", "a.txt" in str(cell.result), str(cell.result)[:120])

        cell = kernel.run("run('cat a.txt')")
        redirected = str(cell.result)
        check("kernel: run() redirects simple file reads",
              "redirected" in redirected and "read('a.txt')" in redirected, redirected[:200])

        cell = kernel.run("run('python3 -c \"print(1)\"')")
        refusal = str(cell.result)
        check("kernel: run() refuses file inspection via -c",
              "wrong_call" in refusal, refusal[:200])

        cell = kernel.run("run('python3 -c \"import json\"')")
        allowed = str(cell.result)
        check("kernel: run() allows import-only -c",
              "command" in allowed or "exit_code" in allowed, allowed[:200])

        cell = kernel.run("apply('--- a/a.txt\\n+++ b/a.txt\\n@@ -1,2 +1,2 @@\\n alpha\\n-beta\\n+delta\\n')")
        check("kernel: apply a unified diff",
              "apply" in str(cell.result) and "delta" in (root / "a.txt").read_text(), str(cell.result)[:160])
        kernel.run("undo('a.txt')")

        cell = kernel.run("commit({'x.txt': 'x\\n', 'y.txt': 'y\\n'})")
        check("kernel: commit writes several files",
              (root / "x.txt").exists() and (root / "y.txt").exists(), str(cell.result)[:160])
        kernel.run("undo()")

        cell = kernel.run("final_answer('done')")
        check("kernel: final_answer", cell.final == "done" and not cell.stdout.strip(), str(cell.final))

        cell = kernel.run("print('noise'); final_answer('x')")
        check("kernel: a printing cell still reports final (the loop rejects it)",
              cell.final == "x" and "noise" in cell.stdout, str(cell.final))
    finally:
        kernel.stop()

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed: " + ", ".join(FAILED))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
