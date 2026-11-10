"""tool_specs: the model-facing tool specs, the system rules, and one run's tool set.
WHAT IT IS
  The data half of the tool loop: PYTHON_TOOL/CODE_TOOL (kernel cell), PLAN_TOOL/TASK_TOOL
  (durable plan/task), TOOL_RULES (system prompt); model_tools() assembles one run's specs.

ENTRY POINTS
  model_tools(scope, kernel, tools=None, *, code_mode, extra_tools, subagent, docs)
                                    -> (Scope, list[dict]) the scope and specs one run offers
  TOOL_RULES  system prompt: one call that does the job, a typed tool for one action
  PYTHON_TOOL, CODE_TOOL  the kernel cell, under the names `python` and `code`
  PLAN_TOOL, TASK_TOOL  the durable plan and the durable task/phase view

INVARIANTS
  typed specs come from tools.TYPE_TOOLS (verbs.VERBS): a new verb is a verbs row, and this
  module adds only the cell, the plan/task pair and the rules.
  a spec whose verb needs run() is dropped when kernel.allow_run is false.
  model_tools returns the scope it used (widened by any extra tool name it allowed) and
  Scope.filter_tools decides what the provider sees.
  a description is the model's whole manual: a wording change is a measured behaviour change.

WHERE TO CHANGE
  system prompt / rules ............ TOOL_RULES
  cell / plan / task descriptions .. PYTHON_TOOL, CODE_TOOL, PLAN_TOOL, TASK_TOOL
  which tools a run offers ......... model_tools
"""

from __future__ import annotations

from dataclasses import replace

from .docs import DOCS_TOOL
from .subagent import DELEGATE_TOOL
from .tools import TYPE_TOOLS
from .verbs import VERBS


PYTHON_TOOL = {
    "type": "function",
    "function": {
        "name": "python",
        "description": (
            "Execute Python in a persistent kernel. Variables, imports and data persist between calls. "
            "The value of the last expression and anything printed are returned as the tool result. "
            "Use the typed tools for reading, searching, editing and running programs; use python "
            "for computation and for anything the typed tools do not cover. In typed modes the "
            "process tool is exec; run() is available only in a python-only repl/bare run. "
            "help() lists the verbs with their live signatures."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python source to execute in the kernel."},
            },
            "required": ["code"],
            "additionalProperties": False,
        },
    },
}

CODE_TOOL = {
    "type": "function",
    "function": {
        "name": "code",
        "description": (
            "Run one Python cell in the persistent kernel. This is the orchestration surface: "
            "the typed verbs are already callable in the cell (read, search, map, outline, "
            "edit, splice, write, delete, mkdir, apply, checks, status, git, exec, ...). Use a "
            "cell for any multi-step, batch, filter or dependent sequence: keep intermediate "
            "data in the cell and print only the result you need. Variables and imports persist "
            "between cells. A typed tool is for one action; a cell is for a pipeline. Call help() "
            "in the cell for live verb signatures."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python source to execute in the kernel."},
            },
            "required": ["code"],
            "additionalProperties": False,
        },
    },
}

PLAN_TOOL = {
    "type": "function",
    "function": {
        "name": "plan",
        "description": (
            "Set or update the durable plan for this task. plan(items=['read the contract', "
            "'change the symbol', 'run the checks']) replaces the plan; plan(done=[1, 2]) marks "
            "1-based items done without replacing the text. The [plan] lines are rendered in "
            "every state card, so the plan survives a long transcript."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "items": {"type": "array", "items": {"type": "string"}},
                "done": {"type": "array", "items": {"type": "integer", "minimum": 1}},
            },
            "additionalProperties": False,
        },
    },
}

TASK_TOOL = {
    "type": "function",
    "function": {
        "name": "task",
        "description": (
            "Return the durable task state: phase, plan, changed paths, last checks and open "
            "items. This is the principal, not a transcript; call it when you need to know "
            "where the run stands."
        ),
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
}

TOOL_RULES = """You are an autonomous coding agent. The task is a contract.

ACT: typed tools for workspace I/O, mutation, process and git; the python cell for computation or a pipeline the typed tools cannot express. One call that does the job. Read before you write.
OBSERVE: typed results are structured. Read [page] before [data]; follow next/has_more/complete. Every refusal carries error.use. Large output is on the _OBS handle; page it. A repeat read is a pointer unless repeat=true.
CHANGE: tracked edits go through write, edit, splice, delete, mkdir or apply. Keep the diff small: one-line docstring on every public name, no unrelated edits. Read [diff] and [check] after a mutation. A failed check is a repair instruction; never weaken it.
VERIFY: a check you wrote can repeat your own assumption. For a named shape or format, read the code that produces it and test a real value, not only your fixture. A timestamp/id is often base36, not decimal; read the producer before writing its regex.
FINISH: only when the plan passes; then reply with the report, not another tool call. Report changed paths, exact commands and results, and what is unverified. Facts, not narration; no headings, no restating the task. No human answers: choose and report a defensible default."""


def model_tools(scope, kernel, tools=None, *, code_mode=False, extra_tools=None,
                subagent=None, docs=None):
    """(scope, specs) for one run: the cell tool, the served typed set, then the extras."""
    if tools is None:
        allow_run = bool(getattr(kernel, "allow_run", True))
        cell_tool = CODE_TOOL if code_mode else PYTHON_TOOL
        tools = [cell_tool, *[spec for spec in TYPE_TOOLS
                              if allow_run or not VERBS[spec["function"]["name"]].get("requires_run")]]
    tools = list(tools)
    if extra_tools:
        tools.extend(extra_tools)
    if subagent is not None:
        tools.append(DELEGATE_TOOL)
    if docs is not None:
        tools.append(DOCS_TOOL)
    if scope.allows("plan"):
        tools.append(PLAN_TOOL)
    if scope.allows("task"):
        tools.append(TASK_TOOL)
    extra_names = {spec["function"]["name"] for spec in (extra_tools or [])}
    if extra_names - scope.tools:
        scope = replace(scope, tools=scope.tools | extra_names)
    return scope, scope.filter_tools(tools)
