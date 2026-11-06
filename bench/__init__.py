"""The benchmark half of cact: agent, suites, fixtures, traces.

WHAT IT IS
  `bench/agent/` is the agent; every other module here is a suite, a grader, a workspace
  tool, or recorded evidence. `bench/run.py` scores the agent on read, mutation, edge and
  hard tasks and writes `bench/traces/`.

ENTRY POINTS
  python3 bench/run.py --suite read|mutation|all|edge|hard [--mode ...]
  python3 bench/live.py ...       one end-to-end task through tui/python/worker.py
  python3 bench/smoke.py          offline contract check
  bench/tasks.py, bench/edge.py, bench/hard.py  task definitions and graders
  bench/snapshot.py, bench/task_journal.py  workspace manifests / journal summaries
  bench/map.py                   the contract index: which module owns what

INVARIANTS
  - `bench/` is a package only so `python3 -m bench.run` works; `bench/agent/` must stay
    importable without a key, a network or a workspace. Importing `bench.edge` or
    `bench.hard` is the exception: each calls `build()` at import, which recreates a missing
    fixture tree (the committed `edge_fixtures/`, the generated H3 CSV).
  - Ground truth belongs to the suite modules (`tasks.py`, `edge.py`, `hard.py`) and is
    computed from the fixtures at import; nothing in `bench/` may cache an expected answer.
  - `bench/traces/`, `bench/ws/`, `bench/.tmp/` and `bench/.cache/` are generated evidence:
    read them, never hand-edit them.

WHERE TO CHANGE
  A new suite ............... its module (`tasks.py`, `edge.py`, `hard.py`) + `run.py` pool
  A new task in a suite .... that module's TASKS list and its `grade_*`
  Workspace freezing ....... bench/snapshot.py
"""
