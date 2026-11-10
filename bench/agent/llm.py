"""llm: the model client: one chat-completions POST, retries, an on-disk reply cache.

WHAT IT IS
  Client.complete() posts the OpenAI-shaped body to {base}/chat/completions (DeepSeek by default)
  and returns a flat dict.  No streaming: one turn = one blocking call.

ENTRY POINTS
  LLMError
  Client(api_key, model="deepseek-flash", effort="low", base=..., timeout=180, temperature,
         seed, cache=True, cache_dir=CACHE_DIR)
    .complete(messages, tools=None, max_tokens=8192, effort=_UNSET) -> dict
      effort overrides the client default for one call; None omits the field entirely.
      -> {content, reasoning, tool_calls, usage, latency_ms, finish, local_cache}

INVARIANTS
  cache key = sha256(request body + model); file bench/.cache/llm/<key>.json.  A hit returns
  local_cache=True and latency_ms=0.  BENCH_LLM_CACHE moves the directory; cache=False disables it.
  retries: 4 attempts, linear sleep 1.5s x attempt, only for 429/5xx and network errors; anything
  else raises LLMError with response detail, which callers turn into a stop reason.
  tools pass through verbatim (the loops own the specs); finish == "length" means truncated and the loops treat it as TRUNCATED.

WHERE TO CHANGE
  provider quirks (headers, body fields) .. Client.complete;  retry policy .. its loop;  cache semantics .. key + CACHE_DIR
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
import urllib.error
import urllib.request


class LLMError(RuntimeError):
    """A primary model call failed; the loop turns it into stop="LLM_FAILED", not a crash."""


CACHE_DIR = os.environ.get("BENCH_LLM_CACHE", str(Path(__file__).resolve().parent.parent / ".cache" / "llm"))
_UNSET = object()


class Client:
    """One chat-completions endpoint (DeepSeek by default) with retries and a disk reply cache.

    `complete(messages, tools=None, max_tokens=8192)` returns the provider's message dict; an
    identical request replays from `cache_dir` with local_cache=True, so offline reruns are free.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-flash",
        effort: str | None = "low",
        base: str = "https://api.deepseek.com",
        timeout: float = 180.0,
        temperature: float | None = None,
        seed: int | None = None,
        cache: bool = True,
        cache_dir: str = CACHE_DIR,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.effort = effort
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.temperature = temperature
        self.seed = seed
        self.cache = cache
        self.cache_dir = cache_dir
        if self.cache:
            os.makedirs(self.cache_dir, exist_ok=True)

    def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int = 8192,
        effort: str | None | object = _UNSET,
    ) -> dict:
        """One model reply, from the cache when the exact request was seen before; never streams."""
        level = self.effort if effort is _UNSET else effort
        body: dict = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if level:
            body["reasoning_effort"] = level
        if self.temperature is not None:
            body["temperature"] = self.temperature
        if self.seed is not None:
            body["seed"] = self.seed
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        data = json.dumps(body).encode("utf-8")
        key = hashlib.sha256(data + self.model.encode()).hexdigest()
        cached_path = os.path.join(self.cache_dir, key + ".json")
        if self.cache and os.path.exists(cached_path):
            try:
                with open(cached_path, encoding="utf-8") as fh:
                    hit = json.load(fh)
                hit["local_cache"] = True
                hit["latency_ms"] = 0.0
                return hit
            except (OSError, json.JSONDecodeError):
                pass

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        payload = None
        t0 = time.perf_counter()
        for attempt in range(4):
            try:
                req = urllib.request.Request(
                    f"{self.base}/chat/completions", data=data, headers=headers
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    payload = json.loads(resp.read())
                break
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:400]
                if exc.code in (429, 500, 502, 503, 504) and attempt < 3:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise LLMError(f"HTTP {exc.code}: {detail}") from None
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt < 3:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise LLMError(f"network: {exc}") from None
        if payload is None:
            raise LLMError("no response after retries")

        latency_ms = (time.perf_counter() - t0) * 1000.0
        choice = payload["choices"][0]
        msg = choice.get("message") or {}
        result = {
            "content": msg.get("content") or "",
            "reasoning": msg.get("reasoning_content") or "",
            "tool_calls": msg.get("tool_calls") or [],
            "usage": payload.get("usage") or {},
            "latency_ms": latency_ms,
            "finish": choice.get("finish_reason"),
            "local_cache": False,
        }
        if self.cache:
            try:
                with open(cached_path, "w", encoding="utf-8") as fh:
                    json.dump(result, fh)
            except OSError:
                pass
        return result
