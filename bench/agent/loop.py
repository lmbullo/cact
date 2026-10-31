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

def _summary(env: dict) -> dict:

    if not env.get("ok"):
        e = env.get("error") or {}
        return {"ok": False, "code": e.get("code"), "message": e.get("message"), "hint": e.get("hint")}
    return {
        "ok": True,
        "kind": env.get("kind"),
        "path": env.get("path"),
        "shown": env.get("shown"),
        "total_lines": env.get("total_lines"),
        "count": env.get("count"),
        "lines_matched": env.get("lines_matched"),
        "occurrences": env.get("occurrences"),
        "expression": env.get("expression"),
        "value": env.get("value"),
        "truncated": env.get("truncated"),
        "note": env.get("note"),
        "chars": len(json.dumps(env, ensure_ascii=False)),
    }

def _join_note(existing: str | None, extra: str) -> str:
    return f"{existing} {extra}".strip() if existing else extra

class AgentLoop:
    def __init__(self, client: Client, registry: Registry, budget: Budget | None = None, rules: str = RULES) -> None:
        self.client = client
        self.registry = registry
        self.budget = budget or Budget()
        self.rules = rules

    def _final_push(self, messages: list[dict], usage: dict | None = None) -> str:

        msgs = messages + [
            {
                "role": "user",
                "content": (
                    "[runtime] Tool execution has stopped for this task. Emit the final answer "
                    "using only tool output already returned. End with a line: ANSWER: <value>"
                ),
            }
        ]
        try:
            resp = self.client.complete(msgs, None, self.budget.max_output_tokens)
        except LLMError:
            return ""
        if usage is not None:
            _usage_add(usage, resp["usage"])
        return (resp.get("content") or "").strip()

    def run(self, task_id: str, prompt: str, trace: Any = None) -> RunResult:
        b = self.budget
        t0 = time.perf_counter()
        messages: list[dict] = [
            {"role": "system", "content": self.rules},
            {"role": "user", "content": prompt},
        ]
        cache: dict[str, dict] = {}
        counts: dict[str, int] = {}
        info: set[str] = set()

        steps = calls = errors = misses = repeats = 0
        llm_calls = 0
        usage: dict = {}
        turns: list[dict] = []
        final = ""
        stop = "STEP_LIMIT"
        dead = 0

        def push() -> str:
            nonlocal llm_calls
            llm_calls += 1
            return self._final_push(messages, usage)

        while True:
            if steps >= b.max_steps:
                final, stop = push(), "STEP_LIMIT"
                break
            if (time.perf_counter() - t0) > b.max_wall_s:
                final, stop = push(), "WALL_LIMIT"
                break

            steps += 1
            llm_calls += 1
            resp = self.client.complete(messages, self.registry.specs(), b.max_output_tokens)
            _usage_add(usage, resp["usage"])
            wire = _wire_calls(resp["tool_calls"])
            rec: dict = {
                "task": task_id,
                "turn": steps,
                "latency_ms": round(resp["latency_ms"]),
                "finish": resp["finish"],
                "content": resp["content"],
                "reasoning": resp["reasoning"],
                "tool_calls": wire,
                "results": [],
            }

            if not wire:
                final = (resp["content"] or "").strip()
                if final:
                    stop = "DONE"
                else:
                    final, stop = push(), "EMPTY_FINAL"
                rec["stop"] = stop
                turns.append(rec)
                if trace:
                    trace.write(rec)
                break

            messages.append(
                {"role": "assistant", "content": resp["content"] or "", "tool_calls": wire}
            )

            new_info = False
            hit_tool_limit = False
            for call in wire:
                if calls >= b.max_tool_calls:
                    hit_tool_limit = True
                    break
                calls += 1
                name = call["function"]["name"]
                raw_args = call["function"]["arguments"]
                tool, args, problem = self.registry.prepare(name, raw_args)

                if problem is None:
                    sig = f"{name}|{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
                else:
                    sig = f"{name}|!invalid|{str(raw_args)[:160]}"

                if tool is not None and sig in cache:
                    repeats += 1
                    counts[sig] = counts.get(sig, 1) + 1
                    env = copy.deepcopy(cache[sig])
                    env["note"] = _join_note(
                        env.get("note"),
                        f"identical call repeated {counts[sig] - 1}x; cached result returned, nothing changed.",
                    )
                elif problem is not None:
                    env = problem
                else:
                    env = self.registry.execute(tool, args)
                    if tool.mutates and env.get("kind") in ("write", "edit"):
                        cache.clear()
                        info.clear()
                    cache[sig] = copy.deepcopy(env)
                    counts[sig] = 1
                    if env.get("ok") and sig not in info:
                        info.add(sig)
                        new_info = True

                if not env.get("ok"):
                    errors += 1
                elif env.get("kind") == "miss":
                    misses += 1

                rec["results"].append(
                    {"name": name, "args": args, "summary": _summary(env), "env": env}
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "name": name,
                        "content": json.dumps(env, ensure_ascii=False),
                    }
                )

            if hit_tool_limit:
                turns.append(rec)
                if trace:
                    trace.write(rec)
                final, stop = push(), "TOOL_LIMIT"
                break

            if new_info:
                dead = 0
            else:
                dead += 1
                if dead >= 2 and dead < b.no_progress_limit:
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "[runtime] The last turns returned no new information. The task is "
                                "not complete. Make a call that returns content not yet seen."
                            ),
                        }
                    )

            turns.append(rec)
            if trace:
                trace.write(rec)

            if dead >= b.no_progress_limit:
                final, stop = push(), "NO_PROGRESS"
                break

        wall_ms = (time.perf_counter() - t0) * 1000.0
        if trace:
            trace.write(
                {
                    "task": task_id,
                    "summary": True,
                    "stop": stop,
                    "steps": steps,
                    "calls": calls,
                    "llm_calls": llm_calls,
                    "errors": errors,
                    "misses": misses,
                    "repeats": repeats,
                    "usage": usage,
                    "wall_ms": round(wall_ms),
                    "final": final,
                }
            )
            trace.close()

        return RunResult(
            task=task_id,
            stop=stop,
            final=final,
            steps=steps,
            calls=calls,
            errors=errors,
            misses=misses,
            repeats=repeats,
            usage=usage,
            wall_ms=wall_ms,
            trace=getattr(trace, "path", None),
            turns=turns,
            llm_calls=llm_calls,
        )
