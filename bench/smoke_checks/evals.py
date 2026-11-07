"""evals: the registry is intact and a fast loop-contract subset still passes.

WHAT IT IS
  The replacement for the old suite-count check. It asserts the eval registry's shape (ids,
  kinds, failure tags), then runs four cheap scripted cases through the real runner: a staged
  finish, structured bad arguments, a per-run custom tool, and the unattended-ask gate. Those
  four cover the turn machine, the kernel binding, the experiment-tool path and the
  fire-and-forget contract without a key or a network.

ENTRY POINTS
  run(check)   called by bench/smoke.py::main

INVARIANTS
  - The subset is deliberately small: smoke must stay a few seconds. The full loop suite is
    `python3 bench/eval.py`; this only checks that the registry and the executor still work.
  - A registry id or count change is a contract change: update the expected numbers here in
    the same commit, or the change is not finished.
  - No result file is written; `runner.run` is used, not the CLI.

WHERE TO CHANGE
  The registry shape ....... the counts and ids below
  The fast subset ........... `SUBSET`
"""
from __future__ import annotations

from bench.evals.runner import REGISTRY, run as run_evals

SUBSET = ("L01_staged_finish", "L04_bad_args", "L09_custom_tool", "L15_autonomy")


def run(check):
    """The eval registry is intact and the fast loop subset passes end to end."""
    loop = [case for case in REGISTRY.values() if case.kind == "loop"]
    live = [case for case in REGISTRY.values() if case.kind == "live"]
    check("evals: loop cases registered", len(loop) == 16, str(len(loop)))
    check("evals: live cases registered", len(live) == 14, str(len(live)))
    check("evals: ids are unique", len(REGISTRY) == len(loop) + len(live),
          f"{len(REGISTRY)} registry vs {len(loop) + len(live)}")
    tags = {case.failure for case in REGISTRY.values()}
    check("evals: failure tags cover the loop",
          {"context", "tooling", "mutation", "finish", "verification", "cost", "autonomy"} <= tags,
          ", ".join(sorted(tags)))
    report = run_evals(list(SUBSET), live=False)
    failed = [f"{case.id}: {result.detail}" for case, result in report.rows if not result.ok]
    check("evals: fast loop subset passes", not failed, "; ".join(failed)[:400])
