"""gates: process gates, recall, restart reaping, apply/commit and the state card.

WHAT IT IS
  The checks that watch the boundaries: splice's typed path and undo, a quoted `>` treated as
  an argument, redirection/sed/git-checkout/mutating-spawn refusals and the one refusal that
  still carries `exit_code`, mutations allowed in scratch and refused in the workspace, a
  plan check answered from `check_cache`, recall() with its handle and across a restart, the
  orphan group reaped on restart, apply/commit, the read cap, and the state card's coverage
  claims (digested, raw, blank line, fold) plus final_answer in both shapes.

ENTRY POINTS
  run(check, root, kernel)   called by bench/smoke.py::main after verbs

INVARIANTS
  - The refusal checks assert that the mutation did NOT happen, not only that it was refused.
  - The orphan check starts a real `sleep` in its own session and expects the restart to
    reap it; that is the one place a failing check can leave a process behind, so it is
    reaped by the kernel, never by this process.
  - The state-card checks build Cells by hand: no kernel call, no file open.

WHERE TO CHANGE
  A gate or hint ......................... kernel/run_prelude.py `_mutating_command` / `_HINT`
  Orphan reaping ......................... kernel/process.py `_reap_orphans`
  The state card ......................... bench/agent/state_loop.py `_observe` / `_compact`
  final_answer ........................... kernel/driver.py
"""
from __future__ import annotations

import time
from pathlib import Path

import bench.agent.loop as loop
import bench.agent.state_loop as state_loop
from bench.agent.kernel import Cell


def run(check, root, kernel):
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
          "wrong_call" in gate and "delete(path)" in gate, gate[:200])
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
    # head and tail (stdout error prose is not promoted); no file is opened to work that out.
    numbered = "\n".join(f"{n:>6}| " + ("error: boom" if n == 300 else f"line {n}")
                         for n in range(1, 901))
    huge = Cell(stdout=numbered, stderr="", result=None, final=None, duration_ms=1,
                reads={"huge.txt": {"ranges": [[1, 900]], "total": 900}})
    card, visible = state_loop.render_observation(huge, 1, 0, None, 800, {})
    ranges = visible.get("huge.txt")
    check("state: a digested card claims only the lines it kept",
          bool(ranges) and 0 < state_loop.covered(ranges) < 900
          and ranges[0][0] == 1 and ranges[-1][1] == 900,
          f"{ranges} covered={state_loop.covered(ranges or [])}")
    check("state: the digested card still names the ranges and the handle",
          "[read] huge.txt" in card and "unread:" in card, card[:160])
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
