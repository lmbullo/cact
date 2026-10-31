"""The model client: one chat-completions POST, retries, and an on-disk reply cache.

WHAT IT IS
  `Client.complete()` posts the OpenAI-shaped body to `{base}/chat/completions` (DeepSeek by
  default) and returns a flat dict. No streaming: a turn is one blocking call.

ENTRY POINTS
  LLMError
  Client(api_key, model="deepseek-flash", effort="low", base=..., timeout=180, temperature,
         seed, cache=True, cache_dir=CACHE_DIR)
    .complete(messages, tools=None, max_tokens=8192) -> dict
      {content, reasoning, tool_calls, usage, latency_ms, finish, local_cache}

INVARIANTS
  - The cache key is sha256(request body + model); `bench/.cache/llm/<key>.json`. Hits come
    back with `local_cache=True` and `latency_ms=0`, which is what the traces report as a
    local hit. `BENCH_LLM_CACHE` moves the directory; `cache=False` disables it (the TUI).
  - Retries: 4 attempts, exponential-ish sleep, only for 429/5xx and network errors. Anything
    else raises `LLMError` with the response detail; callers turn that into a stop reason.
  - `tools` is passed through verbatim; the loops own the specs.
  - `finish == "length"` means the reply was truncated — the loops treat that as TRUNCATED,
    so do not swallow it.

WHERE TO CHANGE
  Provider quirks (headers, body fields) ... Client.complete
  Retry policy ............................. the loop inside Client.complete
  Cache semantics .......................... the key computation + CACHE_DIR
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
    pass

CACHE_DIR = os.environ.get("BENCH_LLM_CACHE", str(Path(__file__).resolve().parent.parent / ".cache" / "llm"))


class Client:
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
    ) -> dict:
        body: dict = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if self.effort:
            body["reasoning_effort"] = self.effort
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
