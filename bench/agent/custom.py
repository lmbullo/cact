"""custom: per-run experiment tools from a JSON manifest, never a second tool catalog.

WHAT IT IS
  A manifest is data: a list of tools, each with a name, a provider summary, a JSON schema
  and python source that defines the function. `load()` validates the manifest; `verbs()`
  turns it into the same registry shape `verbs.VERBS` uses; `specs()` turns it into provider
  tool specs; `prelude()` concatenates the source the kernel execs into its namespace.
  `Kernel(extra_verbs=..., extra_prelude=...)` ships all three to the child, and `ToolLoop`
  merges the specs into its tool list, so a run can test an action without editing bench.

ENTRY POINTS
  load(path) -> list[CustomTool]     parse and validate a manifest file
  verbs(tools) -> dict               {name: {function, summary, schema, mutates}}
  specs(tools) -> list[dict]         provider tool specs
  prelude(tools) -> str              the source the child execs
  CustomTool                         .name .summary .schema .source

INVARIANTS
  - A custom name may not shadow a built-in verb, `python`, `delegate`, `final_answer`,
    `help` or `skill`; a manifest that tries is rejected before a kernel starts. Experiments
    add actions, they never weaken a gate or replace the journal.
  - The source is executed in the kernel namespace, so it can use `_walk`, `read`, `_commit`
    and the rest of the prelude. It must define exactly the named callable. A manifest that
    does not is a load error, not a late `NameError` in a run.
  - `mutates` is declared data, not inferred: the loop uses it only for rendering, while the
    journal still decides what changed. A custom tool that writes should call `_commit`.
  - Stdlib-only and no bench imports: this module is read by the parent and its source is
    shipped to a child that cannot import `bench`.

WHERE TO CHANGE
  Manifest fields ............... CustomTool, load
  The reserved-name set ......... RESERVED
  What the child receives ....... verbs, specs, prelude (then kernel/process.py's env block)
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

RESERVED = frozenset({
    "python", "delegate", "final_answer", "help", "skill", "verbs",
    "map", "read", "session_diff", "search", "recall", "outline",
    "edit", "splice", "write", "delete", "mkdir", "apply", "exec", "git",
    "run", "commit", "diff", "changes", "undo", "grep", "guide",
})


@dataclass(frozen=True)
class CustomTool:
    """One manifest entry: the callable name, its provider description, schema and source."""

    name: str
    summary: str
    schema: dict
    source: str


def load(path: str | Path) -> list[CustomTool]:
    """Parse a manifest into tools, rejecting a reserved name or a source without the callable."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    entries = data.get("tools") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise ValueError("tool manifest must be a list or {'tools': [...]}")
    out: list[CustomTool] = []
    seen: set[str] = set()
    for item in entries:
        if not isinstance(item, dict):
            raise ValueError("each tool manifest entry must be an object")
        name = str(item.get("name") or "").strip()
        summary = str(item.get("summary") or "").strip()
        schema = item.get("schema") or {"type": "object", "properties": {},
                                        "additionalProperties": False}
        source = str(item.get("source") or "")
        if not name or not name.isidentifier():
            raise ValueError(f"invalid tool name {name!r}")
        if name in RESERVED or name in seen:
            raise ValueError(f"tool name {name!r} is reserved or duplicated")
        if not isinstance(schema, dict) or schema.get("type") != "object":
            raise ValueError(f"tool {name!r}: schema must be an object schema")
        if f"def {name}(" not in source:
            raise ValueError(f"tool {name!r}: source must define def {name}(...)")
        seen.add(name)
        out.append(CustomTool(name=name, summary=summary or name, schema=schema,
                              source=source))
    return out


def verbs(tools: list[CustomTool]) -> dict[str, dict]:
    """The manifest in `verbs.VERBS` shape; the function is the tool's own name."""
    return {
        tool.name: {
            "function": tool.name,
            "summary": tool.summary,
            "schema": tool.schema,
            "mutates": False,
        }
        for tool in tools
    }


def specs(tools: list[CustomTool]) -> list[dict]:
    """The manifest as OpenAI-shaped provider tool specs, in manifest order."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.summary,
                "parameters": tool.schema,
            },
        }
        for tool in tools
    ]


def prelude(tools: list[CustomTool]) -> str:
    """The source block the child execs after PRELUDE, one blank line between tools."""
    return "\n\n".join(tool.source.rstrip() for tool in tools)
