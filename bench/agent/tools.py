"""tools: provider-facing tool specs: one typed tool per kernel verb + the python cell.
WHAT IT IS
  TYPE_TOOLS built from the typed verbs.VERBS entries: OpenAI-shaped function tools whose
  parameters = registry schema and description = registry summary. Python-only helpers
  (typed=False, currently run) stay in help() but never become provider tools. Unknown tool
  names are refused.
ENTRY POINTS
  TYPE_TOOLS   typed specs, registry order
  TOOL_NAMES   typed names (no "python")
INVARIANTS
  generated, never hand-written: a verb added to verbs.py appears here with no further edit.
  code/python not generated. PYTHON_TOOL = default persistent-kernel cell; CODE_TOOL = same
  schema under the name `code`, enabled by CODE_TUI_CODE_MODE.
  schemas passed through verbatim; wrong/missing keyword -> bad_args/bad_arguments, never a
  traceback; the loop still pairs the tool message.
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
