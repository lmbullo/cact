"""tools: provider-facing tool specs: one typed tool per kernel verb + the python cell.

WHAT IT IS
  TYPE_TOOLS built from the typed entries in verbs.VERBS: each becomes an OpenAI-shaped function
  tool whose parameters = registry schema and whose description = registry summary. Python-only
  helpers (typed=False, currently run) stay in the registry for help() but never become provider
  tools. ToolLoop sends [PYTHON_TOOL, *TYPE_TOOLS] by default; CODE_TOOL is the opt-in
  orchestration surface. TOOL_NAMES = the closed set of typed names the loop accepts; anything
  else is an unknown tool.

ENTRY POINTS
  TYPE_TOOLS   typed specs, registry order
  TOOL_NAMES   typed names (no "python")

INVARIANTS
  generated, never hand-written: a verb added to verbs.py appears here with no further edit;
  descriptions and implementations cannot drift.
  code/python not generated. PYTHON_TOOL = default persistent-kernel cell; CODE_TOOL = same
  schema under the name `code`, enabled by CODE_TUI_CODE_MODE.
  schemas passed through verbatim. Provider validates shape; the kernel binds arguments to the
  verb function's live signature before dispatch: a wrong/missing keyword -> kind:"bad_args" /
  error.code:"bad_arguments" (never a traceback); the loop still pairs the tool message.

WHERE TO CHANGE
  typed tool ...... verbs.VERBS (this module follows)
  code cell ....... tool_loop.CODE_TOOL, tool_loop.PYTHON_TOOL
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
