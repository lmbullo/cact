"""Offline self-check for the agent and the benchmark: no network, no API key, no fixtures
touched.

WHAT IT IS
  The one command that tells a coding agent "the code still works" without spending tokens.
  It imports every module, drives the kernel prelude end to end (read/write/edit/changes/
  diff/undo, run() allowing reads and refusing mutation) and checks the pure helpers (reply
  parsing, state digests, read coverage, the spawn gate, check plans).

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
import bench.agent.checks as checks
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
    check("state: coverage gaps", state_loop.gaps([(1, 10), (30, 40)], 50) == [[11, 29], [41, 50]])
    check("state: coverage count", state_loop.covered([(1, 10), (5, 20)]) == 20)

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

        cell = kernel.run("open('c.txt', 'w').write('written\\n')")
        check("kernel: an unclosed open() write is journalled",
              (root / "c.txt").exists() and "c.txt" in cell.changed,
              f"{cell.changed} {cell.result}")

        cell = kernel.run("edit('b.txt', 'one', 'uno')")
        check("kernel: edit reports the file", cell.changed == ["b.txt"], str(cell.changed))
        check("kernel: diff shows the hunk", "-one" in cell.delta and "+uno" in cell.delta, cell.delta[:120])

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

        cell = kernel.run("grep('beta', '.')")
        check("kernel: grep", "beta" in str(cell.result), str(cell.result)[:120])

        cell = kernel.run("map('.')")
        check("kernel: map", "a.txt" in str(cell.result), str(cell.result)[:120])

        cell = kernel.run("run('cat a.txt')")
        allowed = str(cell.result)
        check("kernel: run() allows a read-only command",
              "command" in allowed and "alpha" in allowed, allowed[:200])

        cell = kernel.run("run('python3 -c \"print(1)\"')")
        allowed = str(cell.result)
        check("kernel: run() allows -c scripting",
              "command" in allowed or "exit_code" in allowed, allowed[:200])

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

        cell = kernel.run("run('echo x > z.txt')")
        refusal = str(cell.result)
        check("kernel: run() refuses redirection into a file",
              "wrong_call" in refusal and not (root / "z.txt").exists(), refusal[:200])

        cell = kernel.run("run('rm -rf /tmp/cact-smoke-outside')")
        allowed = str(cell.result)
        check("kernel: run() allows a mutation outside the workspace",
              "wrong_call" not in allowed and "'ok': True" in allowed, allowed[:200])

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
        cell = kernel.run("run('echo cached-check')", check_cache={"echo cached-check": "PASSED"})
        check("kernel: a plan check answers run() from the cache",
              "check_cached" in str(cell.result) and "PASSED" in str(cell.result), str(cell.result)[:160])
        from bench.agent.kernel import Cell
        probe = state_loop.StateLoop(object(), kernel, loop.Budget(), read_gate=True, keep_messages=4)
        shown = Cell("[stdout]\nalpha\nbeta\n", "", None, None, 1, reads={"a.txt": {"ranges": [[1, 2]], "total": 2}})
        probe._observe(shown, 1)
        check("state: a printed read counts as delivered",
              probe.coverage.get("a.txt", {}).get("ranges") == [[1, 2]], str(probe.coverage))
        clipped = Cell("[stdout]\nalpha\n...[2 lines]...\n", "", None, None, 1, reads={"b.txt": {"ranges": [[1, 2]], "total": 2}})
        (root / "b.txt").write_text("alpha\nbeta\n", encoding="utf-8")
        probe._observe(clipped, 2)
        hole = probe._read_rejection("see `b.txt`")
        check("state: a clipped read leaves a hole", hole is not None and "unread 2-2" in hole, str(hole)[:120])
        (root / "blank.txt").write_text("a\n\nb\n", encoding="utf-8")
        blank = Cell("[stdout]\n     1| a\n     2| \n     3| b\n", "", None, None, 1,
                     reads={"blank.txt": {"ranges": [[1, 3]], "total": 3}})
        probe._observe(blank, 3)
        hole = probe._read_rejection("see `blank.txt`")
        check("state: a blank delivered line is not a hole",
              hole is None and probe.coverage.get("blank.txt", {}).get("ranges") == [[1, 3]],
              str(hole or probe.coverage.get("blank.txt"))[:120])
        probe.coverage = {"big.txt": {"ranges": [[1, 10]], "total": 900}}
        message = probe._read_rejection("see `big.txt`")
        check("state: read gate rejects a cited file with holes",
              message is not None and "unread 11-900" in message, str(message)[:160])
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

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed: " + ", ".join(FAILED))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
