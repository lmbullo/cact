"""worker_events: the @@TUI@@ wire: emit(), payload builders, streaming trace.
WHAT IT IS
  The only protocol channel: PREFIX ("@@TUI@@"), emit() and every payload server.ts reads;
  worker.py owns argv, rules, verifier and run.

ENTRY POINTS
  emit(payload)  one `@@TUI@@{json}` line on stdout, flushed
  cell_end_payload(cell, number, tool) -> dict  compact cell.end (counts and codes only)
  check_payload(check, verifier) -> dict  `check` event: ok/name/first failing line
  final_payloads(result) -> (status, [dict])  closing `error`/`final` events + status
  StreamingKernel(kernel); EventTrace(emit_event, verifier)  the two streaming adapters

INVARIANTS
  kinds read by server.ts::handleWorkerLine: cell.start {cell, tool, code|args} | cell.end {cell,
  tool, ok, verb_kind, code, page, chars, duration_ms, notes} | turn {turn, data} | check {ok,
  name, line} | log/error {text} | final {status, final, stop, steps, calls, llmCalls, errors,
  usage, economy, wallMs}; a new kind = emit() here + src/types.ts + a tui/tests case.
  one `check` per turn carrying `check` (verdict/name/first line from the Verifier's last Check); stdout is the wire, every payload one line.
  status: DONE|VERIFIED -> done; STEP_LIMIT|WALL_LIMIT with non-empty summary -> partial; else failed; exit 0 only done.

WHERE TO CHANGE
  payload fields .................... builder here + src/types.ts
  status mapping .................... final_payloads
  streaming / turn projection ....... StreamingKernel.run / .call; EventTrace.write
"""
from __future__ import annotations

import json
import sys
from typing import Any

from bench.agent import config
from bench.agent.kernel import Kernel

PREFIX = "@@TUI@@"


def emit(payload: dict[str, Any]) -> None:
    """Write one `@@TUI@@` JSON line to stdout: the only channel server.ts reads."""
    sys.stdout.write(PREFIX + json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def check_name(cmd: str, specs: Any) -> str:
    """The plan entry's name for a `Check.cmd`, so a header reads `typecheck`, not its shell."""
    for spec in specs or ():
        name = str(getattr(spec, "name", "") or "")
        if name and (cmd == name or cmd.startswith(name + ": ")):
            return name
    return cmd.split(": ", 1)[0] if ": " in cmd else cmd


def first_failure(check: Any) -> str:
    """The first failing line a `Check` parsed, else the first non-empty line of its output."""
    method = getattr(check, "first_failure", None)
    return method() if callable(method) else ""


def check_payload(check: dict, verifier: Any) -> dict[str, Any]:
    """One `check` event: the verdict, the check's name and the first failing line.

    The turn record only says pass/fail (`{ok, cmd}`); the failing line is what the log header
    needs at a glance, so it is taken from the Verifier's own last `Check` and never re-run.
    """
    ok = bool(check.get("ok"))
    cmd = str(check.get("cmd") or "")
    return {
        "kind": "check",
        "ok": ok,
        "name": check_name(cmd, getattr(verifier, "specs", ())),
        "line": "" if ok else (first_failure(getattr(verifier, "last", None)) or "failed"),
    }


def cell_end_payload(cell, number: int, tool: str) -> dict[str, Any]:
    """One compact `cell.end` record: counts and codes only, never a payload body."""
    env = getattr(cell, "envelope", None)
    envelope = env if isinstance(env, dict) else {}
    error = envelope.get("error") if isinstance(envelope.get("error"), dict) else {}
    page = envelope.get("page")
    page_bits = None
    if isinstance(page, dict):
        page_bits = {key: page.get(key) for key in
                     ("shown", "total", "unit", "has_more", "truncated", "complete")}
    ok = bool(envelope.get("ok")) if env is not None else not (cell.failed or cell.rejected)
    notes = [" ".join(str(note).split())[:300] for note in (envelope.get("notes") or [])]
    return {
        "kind": "cell.end",
        "cell": number,
        "tool": tool,
        "ok": ok,
        "verb_kind": envelope.get("kind"),
        "code": error.get("code"),
        "page": page_bits,
        "chars": len(cell.stdout or "") + len(cell.result or ""),
        "duration_ms": int(cell.duration_ms or 0),
        "notes": notes,
    }


def final_payloads(result) -> tuple[str, list[dict[str, Any]]]:
    """The closing event(s) for one finished run, plus its status word (done/partial/failed)."""
    payloads: list[dict[str, Any]] = []
    if result.error:
        payloads.append({"kind": "error", "text": result.error})
    if result.stop in ("DONE", "VERIFIED"):
        status = "done"
    elif result.stop in ("STEP_LIMIT", "WALL_LIMIT") and (result.final or "").strip():
        status = "partial"
    else:
        status = "failed"
    payloads.append(
        {
            "kind": "final",
            "status": status,
            "final": result.final,
            "stop": result.stop,
            "steps": result.steps,
            "calls": result.calls,
            "llmCalls": result.llm_calls,
            "errors": result.errors,
            "usage": result.usage,
            "economy": result.economy,
            "attestation": result.attestation,
            "wallMs": round(result.wall_ms),
            "error": result.error,
        }
    )
    return status, payloads


class StreamingKernel(Kernel):
    """A Kernel that emits `cell.start` before each cell and one compact `cell.end` after it."""

    _cell_label = "python"

    def __init__(self, *args: Any, emit_event, cell_label: str = "python", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._emit_event = emit_event
        self._cell_label = cell_label
        self._cell_no = 0

    def run(self, code: str, check_cache: dict | None = None,
            extra_obs: list | None = None):
        """One python cell: `cell.start` before it runs, `cell.end` after it answers."""
        self._cell_no += 1
        number = self._cell_no
        self._emit_event({"kind": "cell.start", "cell": number, "tool": self._cell_label, "code": code})
        cell = super().run(code, check_cache=check_cache, extra_obs=extra_obs)
        self._emit_event(cell_end_payload(cell, number, self._cell_label))
        return cell

    def call(self, tool: str, args: dict | None = None, check_cache: dict | None = None,
             extra_obs: list | None = None):
        """One typed verb call: `cell.start` with its tool/args, then `cell.end` with its counts."""
        self._cell_no += 1
        number = self._cell_no
        self._emit_event({"kind": "cell.start", "cell": number, "tool": tool, "args": args or {}})
        cell = super().call(tool, args, check_cache=check_cache, extra_obs=extra_obs)
        self._emit_event(cell_end_payload(cell, number, tool))
        return cell


class EventTrace:
    """The Trace duck type that streams each turn event instead of writing a file; drops summaries
    (the final event carries those)."""

    def __init__(self, emit_event, verifier: Any = None) -> None:
        self._emit_event = emit_event
        self._verifier = verifier

    def write(self, record: dict) -> None:
        """Stream one turn record (the fields the TUI renders); the summary record is dropped."""
        if record.get("summary"):
            return
        data = {
            "turn": record.get("turn"),
            "stop": record.get("stop"),
            "content": record.get("content") or "",
            "reasoning": (record.get("reasoning") or "")[:1500],
            "code": record.get("code"),
            "tool_calls": record.get("tool_calls"),
            "finish": record.get("finish"),
            "observation": record.get("observation"),
            "calls": record.get("calls") or [],
            "obs": record.get("obs"),
            "card_chars": int(record.get("card_chars") or 0),
            "card_budget": int(record.get("card_budget") or 0),
            "reads": record.get("reads") or {},
            "runs": record.get("runs") or [],
            "cache": record.get("cache") or {},
            # per-turn provider usage -> TUI live counters: in/out tokens, prompt-cache
            # split, context window.
            "usage": record.get("usage") or {},
            "window": config.CONTEXT_WINDOW,
            "check": record.get("check"),
            "notice": record.get("notice"),
        }
        self._emit_event({"kind": "turn", "turn": int(record.get("turn") or 0), "data": data})
        # verdict = its own event; header reads the last one, so a later pass clears an
        # earlier failure.
        check = record.get("check")
        if isinstance(check, dict):
            self._emit_event(check_payload(check, self._verifier))

    def close(self) -> None:
        """No-op: there is no file to close, the events were streamed as they happened."""
        pass
