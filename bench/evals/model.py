"""model: a scripted client for loop evals that must run with no key and no network.

WHAT IT IS
  ScriptedClient implements the same complete(messages, tools, max_tokens) contract as
  agent.llm.Client but returns pre-written replies, so the turn machine, kernel, journal and
  finish protocol are testable deterministically; tool()/say() build the two reply shapes and
  ScriptedClient(responder=...) computes a reply from the messages.

ENTRY POINTS
  ScriptedClient(replies, *, responder=None, usage=None)
    .complete(messages, tools=None, max_tokens=8192, effort=None) -> provider-shaped dict
    .requests     every (messages, tools) the loop sent, for assertions
  tool(name, arguments=None, **kwargs), say(text)   one tool_calls entry / one no-tool reply

INVARIANTS
  reply shape is the provider's: content, reasoning, tool_calls, usage, latency_ms, finish,
  local_cache; the loop never learns it is scripted.
  a script that runs out of replies repeats the last one, never raises.
  usage is additive: one prompt and one completion token per reply unless overridden;
  stdlib-only, no bench imports.

WHERE TO CHANGE
  a new reply shape .. tool, say, ScriptedClient.complete;  dynamic script .. responder;  usage .. _reply
"""
from __future__ import annotations

import json
from typing import Any, Callable


def tool(tool_name: str, arguments: dict | None = None, *, call_id: str | None = None,
         **kwargs: Any) -> dict:
    """One provider tool call; keyword args are merged into the JSON arguments."""
    args = dict(arguments or {})
    args.update(kwargs)
    return {
        "id": call_id or f"call_{tool_name}",
        "type": "function",
        "function": {"name": tool_name, "arguments": json.dumps(args)},
    }


def say(text: str) -> dict:
    """One assistant reply with no tool call (the finish/accept path)."""
    return {"content": text}


class ScriptedClient:
    """A provider-shaped client that replays `replies` and records every request."""

    def __init__(self, replies: list[dict] | None = None, *,
                 responder: Callable[[list[dict], list[dict] | None], dict] | None = None,
                 usage: dict | None = None, model: str = "scripted") -> None:
        self.replies = list(replies or [])
        self.responder = responder
        self.usage = dict(usage or {})
        self.model = model
        self.requests: list[dict] = []
        self.index = 0

    def complete(self, messages: list[dict], tools: list[dict] | None = None,
                 max_tokens: int = 8192, effort=None) -> dict:
        """Return the next scripted reply (or the responder's), in the provider's shape."""
        self.requests.append({"messages": [dict(m) for m in messages],
                              "tools": tools, "max_tokens": max_tokens, "effort": effort})
        if self.responder is not None:
            reply = self.responder(messages, tools)
        elif self.replies:
            reply = self.replies[min(self.index, len(self.replies) - 1)]
            self.index += 1
        else:
            reply = say("")
        return self._reply(reply)

    def _reply(self, reply: dict) -> dict:
        if "tool_calls" not in reply and reply.get("function"):
            reply = {"tool_calls": [reply]}
        usage = {
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "total_tokens": 2,
            "prompt_cache_hit_tokens": 0,
            "prompt_cache_miss_tokens": 1,
            "completion_tokens_details": {"reasoning_tokens": 0},
            **self.usage,
            **(reply.get("usage") or {}),
        }
        return {
            "content": reply.get("content") or "",
            "reasoning": reply.get("reasoning") or "",
            "tool_calls": reply.get("tool_calls") or [],
            "usage": usage,
            "latency_ms": reply.get("latency_ms", 0.0),
            "finish": reply.get("finish", "tool_calls" if reply.get("tool_calls") else "stop"),
            "local_cache": False,
        }
