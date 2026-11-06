"""The provider-facing tool specs: one typed tool per kernel verb, plus the python cell.

WHAT IT IS
  `TYPE_TOOLS` is built from the typed entries in `verbs.VERBS`: each becomes an
  OpenAI-shaped function tool whose `parameters` is the registry schema and whose description
  is the registry summary. Python-only helpers (`typed=False`, currently `run`) stay in the
  registry for `help()` but never become provider tools. `ToolLoop` sends
  `[PYTHON_TOOL, *TYPE_TOOLS]` by default. `TOOL_NAMES` is the closed set of typed names the
  loop accepts; anything else is rejected as an unknown tool.

ENTRY POINTS
  TYPE_TOOLS   the typed specs, in registry order
  TOOL_NAMES   the typed names (no "python")

INVARIANTS
  - Generated, never hand-written: a verb added to `verbs.py` appears here with no further
    edit. That is what keeps the tool descriptions and the kernel implementations from
    drifting.
  - `python` is not generated. It is the persistent-kernel escape hatch and is defined in
    `tool_loop.PYTHON_TOOL` so the benchmark's `--mode bare` can send it alone.
  - Schemas are passed through verbatim. The provider validates shape; the kernel binds the
    arguments to the verb function's live signature before dispatch, so a wrong or missing
    keyword comes back as `kind: "bad_args"` / `error.code: "bad_arguments"` (never a
    traceback) and the loop still pairs the tool message.

WHERE TO CHANGE
  A typed tool ................ verbs.VERBS (this module follows)
  The python tool ............. tool_loop.PYTHON_TOOL
"""
from __future__ import annotations

from .verbs import VERBS

TYPE_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": name,
            "description": meta["summary"],
            "parameters": meta["schema"],
        },
    }
    for name, meta in VERBS.items()
    if meta.get("typed", True)
]

TOOL_NAMES = tuple(tool["function"]["name"] for tool in TYPE_TOOLS)
