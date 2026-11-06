"""plans: the check plan, the Verifier, read coverage and the goal check.

WHAT IT IS
  The checks-engine contract: a spec's `scope` filters the cheap run, the plan short-circuits
  on the first failure, the goal re-runs the whole plan every attempt, the cache fills from a
  run and drops on a mutation, and `--health off` disables the cheap subset. Then the read
  coverage arithmetic (merge/gaps/covered/delivered_ranges) and `check_run.run_check`'s three
  states: passed, failed, unrunnable.

ENTRY POINTS
  run(check) -> Path   called by bench/smoke.py::main; returns the temp workspace the kernel
                       groups then reuse, so every later group shares one fixture tree

INVARIANTS
  - It owns the workspace fixture (smoke_support.workspace): the kernel groups must run after
    it and must not create their own root, or their journal state diverges.
  - Every command here is `true`/`false`/`touch`/`printf` in the temp dir: seconds, no network.
  - The coverage checks never open a file: they feed `delivered_ranges` the exact text a
    digested card shows, which is the whole point of that function.

WHERE TO CHANGE
  A plan/verifier behaviour .............. bench/agent/check_plan.py (this file only scores it)
  Coverage arithmetic .................... state_loop.merge_ranges / gaps / delivered_ranges
  The check states ....................... check.run_check
"""
from __future__ import annotations

import bench.agent.check_plan as check_plan
import bench.agent.check_run as check_mod
import bench.agent.state_loop as state_loop
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

    # --- goal check ----------------------------------------------------------------------
    passed = check_mod.run_check("true", str(root))
    failed = check_mod.run_check("false", str(root))
    slow = check_mod.run_check("sleep 3", str(root), timeout=0.2)
    check("check: passed", passed.ok and passed.state == "passed")
    check("check: failed", not failed.ok and failed.state == "failed")
    check("check: timeout is unrunnable, not failed", slow.state == "unrunnable", slow.state)
    return root
