"""plans: the check plan, the Verifier, read coverage and the goal check.
WHAT IT IS
  Checks-engine contract: scope filters the cheap run, plan short-circuits on first failure,
  goal re-runs the whole plan, cache drops on mutation, --health off disables the cheap subset;
  then green gate, coverage arithmetic and check_run.run_check's passed/failed/unrunnable.
ENTRY POINTS
  run(check) -> Path   called by bench/smoke.py::main; returns the shared temp workspace
INVARIANTS
  owns the workspace fixture (smoke_support.workspace); kernel groups must run after it and
  must not create their own root, or journal state diverges.
  every command is true/false/touch/printf in the temp dir: seconds, no network.
  coverage checks never open a file: they feed delivered_ranges the exact digested text.
WHERE TO CHANGE
  plan/verifier ....... bench/agent/check_plan.py (this file only scores it)
  coverage arithmetic . state_loop.merge_ranges / gaps / delivered_ranges
  check states ........ check.run_check
"""
from __future__ import annotations

import bench.agent.check_plan as check_plan
import bench.agent.check_run as check_mod
import bench.agent.green_gate as green_gate
import bench.agent.state_loop as state_loop
from bench.agent.stops import GREEN
from bench.smoke_support import workspace


def run(check):
    """The check plan and the Verifier, read coverage, and check_run's three states."""
    # --- plans and coverage --------------------------------------------------------------
    root = workspace()
    specs = check_plan.CheckSpec.parse([
        {"name": "bench", "cmd": "true", "cheap": True, "scope": ["bench"]},
        {"name": "tui", "cmd": "true", "cheap": True, "scope": ["tui"]},
    ])
    check("checks: scope filters by changed path",
          [spec.name for spec in specs if spec.applies(["tui/src/x.ts"])] == ["tui"])
    plan, results = check_plan.run_plan(specs, str(root), ["tui/src/x.ts"])
    check("checks: plan runs only the scoped spec",
          plan is not None and plan.cmd == "tui" and list(results) == ["true"], str(plan))
    verifier = check_plan.Verifier(specs, str(root))
    check("verifier: the plan is its goal", verifier.has_goal)
    check("verifier: cheap subset is the cheap specs",
          [spec.name for spec in verifier.cheap_specs] == ["bench", "tui"],
          str(verifier.cheap_specs))
    check("verifier: scope decides whether the cheap run applies",
          verifier.should_run(["tui/src/x.ts"], 0.0) is True
          and verifier.should_run(["tui/src/x.ts"], 19.9) is False
          and verifier.should_run(["tui/src/x.ts"], check_plan.HEALTH_WINDOW) is True,
          "throttle window")
    check("verifier: a docs-only change runs no scoped cheap spec",
          check_plan.Verifier(specs, str(root)).should_run(["README.md"], 0.0) is False)
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
    short = check_plan.CheckSpec.parse([
        {"name": "first", "cmd": "false", "cheap": True},
        {"name": "second", "cmd": f"touch {marker}", "cheap": True},
    ])
    short_verifier = check_plan.Verifier(short, str(root))
    short_results = short_verifier.run()
    check("verifier: the first failure short-circuits the plan",
          list(short_results) == ["false"] and not marker.exists()
          and short_verifier.last.state == "failed", str(list(short_results)))

    counter = root / "goal-runs"
    goal_specs = check_plan.CheckSpec.parse([{"name": "count", "cmd": f"printf x >> {counter}"}])
    goal_verifier = check_plan.Verifier(goal_specs, str(root))
    first_ok, first_check = goal_verifier.authoritative()
    second_ok, _ = goal_verifier.authoritative()
    check("verifier: the goal runs the whole plan on every attempt",
          first_ok and second_ok and counter.read_text() == "xx"
          and first_check.state == "passed", counter.read_text())
    passing = check_plan.CheckSpec.parse([{"name": "ok", "cmd": "true"}])
    failing = check_plan.CheckSpec.parse([{"name": "bad", "cmd": "false"}])
    check("checks: run_each agrees with run_specs on a passing spec",
          check_plan.run_each(passing, str(root))[0]["ok"] is True
          and check_plan.run_specs(passing, str(root)).ok is True)
    failing_row = check_plan.run_each(failing, str(root))[0]
    check("checks: run_each agrees with run_specs on a failing spec",
          failing_row["ok"] is False and failing_row["exit"] == 1
          and check_plan.run_specs(failing, str(root)).ok is False, str(failing_row))
    cache_marker = root / "cache-shortcut"
    cached_specs = check_plan.CheckSpec.parse([
        {"name": "touch", "cmd": f"touch {cache_marker}"}])
    rows = check_plan.run_each(cached_specs, str(root),
                               cache={f"touch {cache_marker}": {"ok": True}})
    check("checks: a cached green spec is not re-run",
          rows[0]["ok"] is True and not cache_marker.exists(), str(rows))
    legacy_ok, legacy_check = check_plan.Verifier(goal_specs, str(root), legacy=True).authoritative()
    check("verifier: the legacy goal answers a bare bool",
          legacy_ok is True and legacy_check is None, str(legacy_ok))
    oracle = check_plan.Verifier(check=lambda: check_mod.Check(cmd="oracle", state="passed"),
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

    # --- the post-plan green gate: one finish cell, no foreign gate ------------------------
    class _Checks:
        """The one field note_checks reads off a `checks` cell."""

        def __init__(self, result):
            self.result = result

    gate = green_gate.GreenGate()
    check("green: starts closed, so nothing is refused before a plan runs",
          not gate.passed and gate.refusal("exec") is None
          and gate.refusal("checks") is None and gate.suffix("checks") == "")
    gate.note_checks(_Checks('{"ok": true, "rows": []}'))
    check("green: a passing checks cell with no rows never opens the gate", not gate.passed)
    gate.note_checks(_Checks('{"ok": true, "rows": [{"name": "lint", "ok": true}]}'))
    check("green: a passing plan with a row opens the gate",
          gate.passed and gate.cells == 0)
    check("green: after the pass, exec and checks are GREEN refusals with the same text",
          gate.refusal("exec") == (GREEN, green_gate.EXEC_REFUSAL)
          and gate.refusal("checks") == (GREEN, green_gate.EXEC_REFUSAL))
    check("green: a checks observation carries the pass line only after the pass",
          gate.suffix("checks") == "\n" + green_gate.PASS_NOTE
          and gate.suffix("exec") == "")
    first = gate.refusal("code")
    second = gate.refusal("code")
    check("green: code cells stay open after the pass",
          first is None and second is None and gate.cells == 2)
    gate.note_checks(_Checks('{"ok": true, "rows": [{"name": "test"}]}'))
    check("green: a fresh passing checks resets the cell count",
          gate.cells == 0 and gate.refusal("code") is None)
    gate.invalidate()
    check("green: a mutation closes the gate again", not gate.passed and gate.refusal("exec") is None)
    gate.note_checks(_Checks("not json"))
    check("green: an unparseable checks payload never opens the gate", not gate.passed)

    bun = check_mod.Check(cmd="bun test", state="passed", exit_code=0, duration_ms=7,
                          stdout=" 7 pass\n 0 fail\nRan 7 tests across 5 files. [2.3s]\n")
    check("checks: a passed row carries the last count line, so the model need not re-run it",
          bun.summary() == "Ran 7 tests across 5 files. [2.3s]"
          and "7 pass" not in bun.text(), bun.summary())

    lint = check_plan.CheckSpec.parse([{"name": "lint", "cmd": "python3 -m ruff check ."}])
    check("green: an exec argv is matched to the one plan spec it runs",
          green_gate.plan_command({"argv": ["ruff", "check"]}, lint) is None
          and green_gate.plan_command(
              {"argv": ["python3", "-m", "ruff", "check", "."]}, lint) is lint[0])
    check("green: the plan's own command is never foreign, even when it names ruff",
          not green_gate.is_plan_command({"argv": ["ruff", "check"]}, lint)
          and green_gate.is_plan_command({"argv": ["python3", "-m", "ruff", "check", "."]}, lint)
          and not green_gate.is_foreign_gate({"shell": "python3 -m ruff check ."}, lint))
    check("green: a lint/typecheck gate absent from the plan is foreign",
          green_gate.is_foreign_gate({"argv": ["ruff", "check", "."]}, lint)
          and green_gate.is_foreign_gate({"argv": ["bun", "run", "typecheck"]}, [])
          and green_gate.is_foreign_gate({"commands": [["bun", "test"],
                                                       ["npm", "run", "lint"]]}, lint)
          and green_gate.is_foreign_gate({"shell": "cd tui && bun run typecheck"}, lint))
    check("green: a non-gate command is not foreign",
          not green_gate.is_foreign_gate({"argv": ["pytest", "-q"]}, lint)
          and not green_gate.is_foreign_gate({"shell": "git status"}, lint))
    check("green: exec_commands reads argv, every commands row and the shell field",
          green_gate.exec_commands({"argv": ["a", "b"], "commands": [["c", "d"]],
                                   "shell": "e f"}) == ["a b", "c d", "e f"]
          and green_gate.exec_commands({"argv": "not-a-list"}) == [])

    # --- goal check ----------------------------------------------------------------------
    passed = check_mod.run_check("true", str(root))
    failed = check_mod.run_check("false", str(root))
    slow = check_mod.run_check("sleep 3", str(root), timeout=0.2)
    check("check: passed", passed.ok and passed.state == "passed")
    check("check: failed", not failed.ok and failed.state == "failed")
    check("check: timeout is unrunnable, not failed", slow.state == "unrunnable", slow.state)
    return root
