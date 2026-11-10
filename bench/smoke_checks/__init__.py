"""smoke_checks: one check group per area, driven by bench/smoke.py.

WHAT IT IS
  The body of the offline contract check, split by area: each module exposes one run(check, ...)
  and prints through bench.smoke_support.check; failures accumulate in smoke_support.FAILED.
    evals.py, helpers.py, index.py, docs.py                       run(check)
    plans.py run(check) -> root;  protocol.py                     run(check, root)
    files.py, verbs.py, gates.py                                  run(check, root, kernel)
    revert.py, serve.py, handoff.py, turns.py, repl.py, style.py  run(check)

ENTRY POINTS
  python3 bench/smoke.py  the only entry, it decides the order;  <module>.run(check, ...)  one
  group; `check` is bench.smoke_support.check

INVARIANTS
  modules import the harness from bench.smoke_support, never from bench.smoke (a cycle) or
  from each other.
  plans owns the shared temp workspace and returns it; files, verbs and gates reuse the same
  started Kernel in that order, because each reuses files the last created.
  a module must run on its own (given a root/kernel) for single-area debugging.
WHERE TO CHANGE
  a new area .. <area>.py with one run(), called from bench/smoke.py::main;  harness ..
  smoke_support.py;  the pass/fail line and exit code .. summary()
"""
