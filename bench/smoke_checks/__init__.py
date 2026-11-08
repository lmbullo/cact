"""smoke_checks: one check group per area, driven by bench/smoke.py.

WHAT IT IS
  The body of the offline contract check, split by area so an agent adds a check next to the
  thing it changed instead of scrolling one 1100-line `main()`. Each module exposes one
  `run(check, ...)` and prints through the shared `check()`; failures accumulate in
  `bench.smoke_support.FAILED` and the runner reports the exit code.

    evals.py     the eval registry is intact and a fast loop subset passes   run(check)
    helpers.py   pure helpers, card digests, generated verb specs          run(check)
    index.py     the header index + map CLI                               run(check)
    docs.py      every path a document names exists                        run(check)
    plans.py     check plan, Verifier, coverage, run_check                 run(check) -> root
    protocol.py  stale/current PROTOCOL                                   run(check, root)
    files.py     journalled file action space                             run(check, root, kernel)
    verbs.py     typed verbs through Kernel.call                          run(check, root, kernel)
    gates.py     process gates, recall, reaping, state card, finish        run(check, root, kernel)
    revert.py    revert_check: HEAD swap and its restore proof                run(check)
    serve.py     the TUI-server verb: state dir, fake /health, logs, refusals  run(check)
    handoff.py   a structured finish reaches the next task's prefix            run(check)
    turns.py     the turn machine, offline                                run(check)
    repl.py      the fenced-block dialect                                 run(check)
    style.py     the tree is ruff-clean and every public name documented   run(check)

ENTRY POINTS
  python3 bench/smoke.py    the only entry point; it decides the order
  <module>.run(check, ...)  one group; `check` is bench.smoke_support.check

INVARIANTS
  - modules import the harness from `bench.smoke_support`, never from `bench.smoke` (a cycle)
    and never from each other.
  - `plans` owns the shared temp workspace and returns it; `files`, `verbs` and `gates` take
    the same started Kernel and run in that order, because each reuses files the last created.
  - A module must be runnable on its own (given a root/kernel) so a single area can be debugged
    with `python3 -c "from bench.smoke_checks import files; ..."`.

WHERE TO CHANGE
  A new area ............................. add <area>.py with one run(), then call it in
                                          bench/smoke.py::main
  The harness, fakes or temp workspace ... bench/smoke_support.py
  The pass/fail line and exit code ....... smoke_support.summary()
"""
