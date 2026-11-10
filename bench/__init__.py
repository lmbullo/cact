"""The evaluation half of cact: the agent, its failure-mode evals, and archived evidence.
WHAT IT IS
  `bench/agent/` is the agent; `bench/evals/` the current suite (loop contracts, model-backed
  failure cases, fixtures, hidden oracles); `bench/archive/` retired suites and recorded results
  as read-only evidence, not a gate. `bench/live.py` is the end-to-end TUI harness; snapshot.py
  and task_journal.py are its workspace tools.
ENTRY POINTS
  python3 bench/eval.py [--live]       the evals: loop contracts offline, failures live
  python3 bench/live.py ...            one end-to-end task through tui/python/worker.py
  python3 bench/smoke.py               offline contract check
  bench/evals/runner.py                registry, execution modes, result rows; loops.py / live.py
                                       the two case lists
  bench/guide.py                       the contract index: which module owns what
INVARIANTS
  `bench/` is a package only so `python3 -m bench.eval` works; `bench/agent/` must stay importable
  without a key, a network or a workspace.
  ground truth belongs to `bench/evals/oracle.py`, input to `fixtures.py`; neither may import the
  other's answer, and a visible test is never the oracle.
  results/, bench/.tmp and bench/.cache are generated evidence: read, never hand-edit;
  bench/archive/legacy/ is retained input, excluded from lint and the header index.
WHERE TO CHANGE
  a new loop failure ........ bench/evals/loops.py + runner.REGISTRY;  live failure ... live.py + oracle
  a new capability .......... the agent module, then config.build and the worker env flags
  workspace freezing ........ bench/snapshot.py
"""
