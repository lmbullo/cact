"""custom: per-run experiment tools from a JSON manifest; never a second tool catalog.

WHAT IT IS
  Manifest = data: a list of {name, provider summary, JSON schema, python source defining the
  function}.  load() validates; verbs() -> registry shape of verbs.VERBS; specs() -> provider
  specs; prelude() -> source the kernel execs.  Kernel(extra_verbs=, extra_prelude=) ships all
  three to the child; ToolLoop merges the specs, so a run tests an action without editing bench.

ENTRY POINTS
  load(path) -> list[CustomTool]  parse + validate a manifest file
  verbs(tools) -> dict            {name: {function, summary, schema, mutates}}
  specs(tools) -> list[dict]      provider tool specs
  prelude(tools) -> str   source the child execs;  CustomTool   .name .summary .schema .source

INVARIANTS
  a name collision rejects before kernel start: built-in verb, python, delegate, final_answer,
  help, skill.  Experiments add actions; never weaken a gate or replace the journal.
  source runs in the kernel namespace: may use _walk, read, _commit and the rest of the prelude,
  must define exactly the named callable; a missing callable is a load error.
  mutates is declared, not inferred: rendering only; the journal still decides what changed.

WHERE TO CHANGE
  manifest fields .. CustomTool, load;  reserved names .. RESERVED;  child payload .. verbs, specs, prelude, then kernel/process.py env block
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

RESERVED = frozenset({
    "python", "delegate", "final_answer", "help", "skill", "verbs",
    "map", "read", "session_diff", "checks", "status", "search", "recall", "outline",
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
