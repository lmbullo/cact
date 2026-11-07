"""The evaluation half of cact: the agent, its failure-mode evals, and archived evidence.

WHAT IT IS
  `bench/agent/` is the agent. `bench/evals/` is the current evaluation suite: scripted loop
  contracts, model-backed failure cases, deterministic fixtures and hidden oracles.
  `bench/archive/` keeps the retired suites and the recorded results as read-only evidence;
  it is not the primary gate. `bench/live.py` is the end-to-end TUI harness, and
  `bench/snapshot.py` / `bench/task_journal.py` are its workspace tools.

ENTRY POINTS
  python3 bench/eval.py [--live]       the evals: loop contracts offline, failures live
  python3 bench/live.py ...            one end-to-end task through tui/python/worker.py
  python3 bench/smoke.py               offline contract check
  bench/evals/runner.py                the registry, execution modes and result rows
  bench/evals/loops.py, live.py       the two case lists
  bench/guide.py                       the contract index: which module owns what

INVARIANTS
  - `bench/` is a package only so `python3 -m bench.eval` works; `bench/agent/` must stay
    importable without a key, a network or a workspace.
  - Ground truth belongs to `bench/evals/oracle.py`; input belongs to `fixtures.py`. Neither
    may import the other's answer, and a visible test is never the oracle.
  - `bench/evals/results/`, `bench/archive/results/`, `bench/.tmp/` and `bench/.cache/` are
    generated evidence: read them, never hand-edit them. `bench/archive/legacy/` is retained
    input and is excluded from lint and the header index.

WHERE TO CHANGE
  A new loop failure ........ bench/evals/loops.py + runner.REGISTRY
  A new live failure ........ bench/evals/live.py + an oracle
  A new capability .......... the agent module, then config.build and the worker env flags
  Workspace freezing ........ bench/snapshot.py
"""
