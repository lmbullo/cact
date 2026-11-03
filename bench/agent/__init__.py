"""The agent package: kernel, loops, model client, checks, traces.

WHAT IT IS
  The importable agent. `kernel.py` is the action space and the child process; the loops
  turn model replies into cells and cells into observations; `config.py` is what production
  builds. Read in this order: kernel.py -> tool_loop.py -> state_loop.py -> config.py.

ENTRY POINTS
  Kernel / Cell .................... kernel.py (`run` for a python cell, `call` for a typed verb)
  Verb registry / tool specs ....... verbs.py (schema + summary + function), tools.py (specs)
  guide() / the header index ....... headers.py (also shipped to a kernel cell as a verb)
  ToolLoop / StateLoop / ReplLoop .. the loop modules; config.build() picks one
  Client, LLMError ................. llm.py
  Check, run_check, evaluate ....... check.py; plans in checks.py
  Budget, RunResult, _usage_add .... loop.py
  Trace, render .................... trace.py

INVARIANTS
  - No import side effects: nothing here touches the network or the filesystem at import
    time. `bench/agent/config.py` reads env vars only when imported.
  - `loop.py` holds shared dataclasses only; it must not grow a loop again.
  - `headers.py` is stdlib-only, `__file__`-free and read-only, because `kernel.py` ships its
    *source* to the child interpreter as KERNEL_HEADERS. Anything it imports or touches must
    exist in a bare `python3 -c` with cwd set to a task workspace.

WHERE TO CHANGE
  Action space or a helper ......... kernel.py (PRELUDE / RUN_PRELUDE)
  Finish protocol or stop reason ... tool_loop.py (a reply dialect overrides `_reply`, see repl.py)
  Production defaults .............. config.py
  A new module header or section ... that module's docstring; `python3 bench/map.py --check`
                                     and bench/smoke.py fail when it is incomplete

"""
