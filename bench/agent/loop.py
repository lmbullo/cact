from __future__ import annotations

import copy
import json
import time
from dataclasses import dataclass, field
from typing import Any

from .llm import Client, LLMError
from .tools import Registry

RULES = """RUNTIME CONTRACT
1. Each turn: emit tool calls, or emit a final answer with no tool calls. No tool calls ends the task.
2. The final answer contains only facts present in tool output. Its last line is exactly "ANSWER: <value>" and nothing else.
3. A tool envelope field "note" is an instruction from the runtime. Apply it before the next call.
4. ok:false envelopes carry error.hint, the call most likely to succeed next.
5. An identical call is not re-executed; its cached envelope is returned.
"""

@dataclass
class Budget:
    max_steps: int = 200
    max_tool_calls: int = 500
    max_output_tokens: int = 393216
    max_wall_s: float = 3600.0
    no_progress_limit: int = 3

@dataclass
class RunResult:
    task: str
    stop: str
    final: str
    steps: int
    calls: int
    errors: int
    misses: int
    repeats: int
    usage: dict
    wall_ms: float
    trace: str | None
    turns: list[dict] = field(default_factory=list)
    workspace: str = ""
    llm_calls: int = 0

def _usage_add(acc: dict, usage: dict) -> None:
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        acc[key] = acc.get(key, 0) + int(usage.get(key) or 0)
    details = usage.get("completion_tokens_details") or {}
    acc["reasoning_tokens"] = acc.get("reasoning_tokens", 0) + int(details.get("reasoning_tokens") or 0)
    for key in ("prompt_cache_hit_tokens", "prompt_cache_miss_tokens"):
        acc[key] = acc.get(key, 0) + int(usage.get(key) or 0)
    acc["local_hits"] = acc.get("local_hits", 0) + (1 if usage.get("local_cache") else 0)

def _wire_calls(raw: list[dict]) -> list[dict]:
    out = []
    for i, call in enumerate(raw):
        fn = call.get("function") or {}
        out.append(
            {
                "id": call.get("id") or f"call_{i}",
                "type": "function",
                "function": {
                    "name": fn.get("name") or "",
                    "arguments": fn.get("arguments") or "{}",
                },
            }
        )
    return out

