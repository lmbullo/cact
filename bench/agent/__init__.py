"""The agent package: kernel, loops, model client, checks, traces.

WHAT IT IS
  The importable agent. `kernel.py` is the action space and the child process; the loops
  turn model replies into cells and cells into observations; `config.py` is what production
  builds. Read in this order: kernel.py -> tool_loop.py -> state_loop.py -> config.py.

ENTRY POINTS
  Kernel / Cell .................... kernel.py
  ToolLoop / StateLoop / ReplLoop .. the loop modules; config.build() picks one
  Client, LLMError ................. llm.py
  Check, run_check, evaluate ....... check.py; plans in checks.py
  Budget, RunResult, _usage_add .... loop.py
  Trace, render .................... trace.py

INVARIANTS
  - No import side effects: nothing here touches the network or the filesystem at import
    time. `bench/agent/config.py` reads env vars only when imported.
  - `loop.py` holds shared dataclasses only; it must not grow a loop again.

WHERE TO CHANGE
  Action space or a helper ......... kernel.py (PRELUDE / RUN_PRELUDE)
  Finish protocol or stop reason ... tool_loop.py (and repl.py)
  Production defaults .............. config.py

"""
