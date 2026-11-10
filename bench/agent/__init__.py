"""agent: package map: kernel, loops, model client, checks, traces.
WHAT IT IS
  Import root. kernel/ = action space + child process; loops = reply->cell->observation;
  config.py = production construction. Read order: kernel/ -> tool_loop.py -> state_loop.py -> config.py.
ENTRY POINTS
  kernel/ ................ Kernel/Cell (`run` python cell, `call` typed verb); protocol.py = wire
                           shapes, process.py = supervisor
  verbs.py / tools.py ... verb registry (schema + summary + function) / provider specs
  headers.py ............ guide() + header index; shipped to a kernel cell as a verb
  ToolLoop/StateLoop/ReplLoop  loop modules; config.build() selects one
  Client, LLMError ...... llm.py;  Check, run_check, evaluate = check_run.py (plans in check_plan.py)
  Budget, RunResult, _usage_add = loop.py;  Trace, render = trace.py
INVARIANTS
  import side effects = none: no network, no filesystem; config.py reads env vars on import.
  loop.py = shared dataclasses only; never a loop.
  headers.py = stdlib-only, __file__-free, read-only: kernel/prelude.py ships its source to the
  child as KERNEL_HEADERS; it must run in bare `python3 -c` with cwd=task workspace.
WHERE TO CHANGE
  action space / helper ......... kernel/prelude.py (PRELUDE) or kernel/run_prelude.py (RUN_PRELUDE)
  finish protocol / stop reason . finish_flow.py / stops.py (tool_loop.py re-exports them)
  production defaults ........... config.py
  module header / section ....... that module's docstring; gates: `python3 bench/guide.py --check`,
                                  `python3 bench/smoke.py`
"""
