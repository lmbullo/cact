from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bench.agent import config  # noqa: E402
from bench.agent.check import run_check  # noqa: E402
from bench.agent.kernel import Kernel  # noqa: E402
from bench.agent.llm import Client, LLMError  # noqa: E402
from bench.agent.repl import RUN_NOTE  # noqa: E402
from bench.agent.tool_loop import TOOL_RULES  # noqa: E402

PREFIX = "@@TUI@@"


def emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(PREFIX + json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


AUTONOMOUS = """

AUTONOMOUS TASK MODE
You are running unattended. No human will answer questions or approve actions.
- Inspect the workspace, make the changes, and verify them with real commands.
- Keep working until the task is complete or you are genuinely blocked.
- Finish with final_answer(<short summary>) in a cell that prints nothing.
- The summary should say what changed, what you verified, and any blockers.
"""


class StreamingKernel(Kernel):
    def __init__(self, *args: Any, emit_event, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._emit_event = emit_event
        self._cell_no = 0

    def run(self, code: str):
        self._cell_no += 1
        self._emit_event({"kind": "cell.start", "cell": self._cell_no, "code": code})
        return super().run(code)


class EventTrace:
    def __init__(self, emit_event) -> None:
        self._emit_event = emit_event

    def write(self, record: dict) -> None:
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
            "runs": record.get("runs") or [],
            "cache": record.get("cache") or {},
        }
        self._emit_event({"kind": "turn", "turn": int(record.get("turn") or 0), "data": data})

    def close(self) -> None:
        pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--prompt-file", required=True)
    parser.add_argument("--model", default="deepseek-flash")
    parser.add_argument("--effort", default="low")
    parser.add_argument("--max-steps", type=int, default=120)
    parser.add_argument("--max-wall", type=float, default=1800.0)
    parser.add_argument("--cell-timeout", type=float, default=120.0)
    parser.add_argument("--max-output", type=int, default=config.MAX_OUTPUT)
    parser.add_argument("--allow-run", default="1")
    parser.add_argument("--check", default="")
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve()
    prompt = Path(args.prompt_file).read_text(encoding="utf-8").strip()
    if not prompt:
        emit({"kind": "error", "text": "empty prompt"})
        emit({"kind": "final", "status": "failed", "stop": "EMPTY_PROMPT"})
        return 2

    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        emit(
            {
                "kind": "error",
                "text": "DEEPSEEK_API_KEY is not set. Put it in ~/.config/code-tui/env or export it before launching code.",
            }
        )
        emit({"kind": "final", "status": "failed", "stop": "NO_API_KEY"})
        return 2

    allow_run = args.allow_run != "0"
    client = Client(
        api_key=key,
        model=args.model,
        effort=None if args.effort == "none" else args.effort,
        base=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        cache=False,
    )
    budget = config.budget(
        max(1, args.max_steps),
        max(10.0, args.max_wall),
        args.max_output,
    )
    rules = TOOL_RULES + AUTONOMOUS
    if allow_run:
        rules += RUN_NOTE
    check = args.check.strip()
    goal = None
    goal_label = "verified"
    legacy_check = os.environ.get("CODE_TUI_LEGACY_CHECK") == "1"
    if check:
        goal = (lambda: run_check(check, workspace).ok) if legacy_check else (lambda: run_check(check, workspace))
        goal_label = f"verified: {check} passed"

    kernel: StreamingKernel | None = None

    def stop_kernel(_signum, _frame) -> None:
        if kernel is not None:
            kernel.stop()
        raise SystemExit(143)

    signal.signal(signal.SIGTERM, stop_kernel)
    signal.signal(signal.SIGINT, stop_kernel)

    result = None
    try:
        kernel = StreamingKernel(
            workspace,
            timeout=max(5.0, args.cell_timeout),
            writable=True,
            prelude="all",
            allow_run=allow_run,
            supervise=os.environ.get("CODE_TUI_LEGACY_TIMEOUT") != "1",
            emit_event=emit,
        )
        loop = config.build(
            client,
            kernel,
            budget,
            rules=rules,
            history=config.HISTORY,
            obs_chars=config.OBS_CHARS,
            stop="call",
            goal=goal,
            goal_label=goal_label,
            goal_stops=False,
            mode=config.MODE,
        )
        result = loop.run("tui-task", prompt, EventTrace(emit))
    except LLMError as exc:
        emit({"kind": "error", "text": f"LLM call failed: {exc}"})
        emit({"kind": "final", "status": "failed", "stop": "LLM_ERROR"})
        return 1
    except BaseException as exc:
        emit({"kind": "error", "text": f"{type(exc).__name__}: {exc}"})
        emit({"kind": "error", "text": traceback.format_exc()[-4000:]})
        emit({"kind": "final", "status": "failed", "stop": "WORKER_ERROR"})
        return 1
    finally:
        if kernel is not None:
            kernel.stop()

    if result is None:
        emit({"kind": "final", "status": "failed", "stop": "NO_RESULT"})
        return 1

    if result.stop in ("DONE", "VERIFIED"):
        status = "done"
    elif result.stop in ("STEP_LIMIT", "WALL_LIMIT") and (result.final or "").strip():
        status = "partial"
    else:
        status = "failed"
    emit(
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
            "wallMs": round(result.wall_ms),
        }
    )
    return 0 if status == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
