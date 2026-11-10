"""worker: one task = one worker process = one agent loop; the TUI <-> bench/ seam.

WHAT IT IS
  Spawned by tui/src/server.ts per task; config.build makes the loop (mode CODE_TUI_MODE, default state),
  runs unattended, streams @@TUI@@ JSON lines; exit code = verdict. Wire in worker_events.py, memory in worker_history.py.

ENTRY POINTS
  main()  argv: --workspace --prompt-file --model --effort --max-steps --max-wall --cell-timeout
          --max-output --allow-run --check --checks; emits cell.start|cell.end|turn|check|log|error|final.
  re-exported: emit/EventTrace/StreamingKernel/cell_end_payload; state_dir/history_brief/record_history.

INVARIANTS
  allow_shell=False in typed modes, only repl has run(); --checks (JSON plan) wins over --check.
  check_plan.Verifier: the goal re-runs the plan on every finish attempt, final_answer is rejected until it
  passes; goal_stops=False -> a failing check never ends the run; the cheap health run keeps to changed paths.
  status: DONE|VERIFIED -> done, STEP_LIMIT|WALL_LIMIT with summary -> partial, else failed; exit 0 only done.
  missing DEEPSEEK_API_KEY / empty prompt -> NO_API_KEY / EMPTY_PROMPT, no kernel spent; no human answers.
  history_brief is prepended (state mode); record_history persists the handoff even after a failed run.
  git identity GIT_AUTHOR_*/GIT_COMMITTER_* is forwarded to children, the user's config is never written.

WHERE TO CHANGE
  payloads / status mapping ... worker_events.py   rules assembly / budgets ... main (AUTONOMOUS, RUN_NOTE)
  per-task limits ... argparse defaults (CODE_TUI_MAX_* in server.ts)   memory ... worker_history.py
  logs ... <state>/logs/<task-id>.jsonl (server writes: raw line + parsed event, prompt, usage, error)
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bench.agent import config, custom  # noqa: E402
from bench.agent.check_plan import CheckSpec, Verifier  # noqa: E402
from bench.agent.docs import DocsClient  # noqa: E402
from bench.agent.llm import Client, LLMError  # noqa: E402
from bench.agent.repl import REPL_RULES, RUN_NOTE  # noqa: E402
from bench.agent.skeptic import Skeptic  # noqa: E402
from bench.agent.subagent import Subagent  # noqa: E402
from bench.agent.tool_loop import TOOL_RULES  # noqa: E402
from tui.python.worker_events import (  # noqa: E402
    EventTrace, StreamingKernel, cell_end_payload, emit, final_payloads)  # noqa: F401  (re-exported)
from tui.python.worker_history import (  # noqa: E402
    history_brief, record_history, state_dir)  # noqa: F401  (re-exported: worker.state_dir)

AUTONOMOUS = """

AUTONOMOUS TASK MODE
You are running unattended. No human will answer questions or approve actions.
- Inspect, change, and verify with real commands. Keep working until the task is complete or genuinely blocked.
- Verify with checks(); a check is clean only when its latest execution passed. Never claim a gate without the run that passed it.
- Finish with final_answer(<short summary>) in a silent cell. A silent finish that passes the plan ends the turn.
- When you leave work open, pass final_answer({summary, changed, gates, deferred, untested}); the next task inherits it.
"""


def main() -> int:
    """Run one task end to end and stream `@@TUI@@` events; 0 on a clean finish, 1 on failure."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--prompt-file", required=True)
    parser.add_argument("--model", default="deepseek-flash")
    parser.add_argument("--effort", default="low")
    parser.add_argument("--max-steps", type=int, default=300)
    parser.add_argument("--max-wall", type=float, default=1800.0)
    parser.add_argument("--cell-timeout", type=float, default=120.0)
    parser.add_argument("--max-output", type=int, default=config.MAX_OUTPUT)
    parser.add_argument("--allow-run", default="1")
    parser.add_argument("--check", default="")
    parser.add_argument("--checks", default="")
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
    # tasks may commit; a fresh workspace has no git identity. Supply one for the kernel's
    # run() children; never write the user's git config.
    git_name = os.environ.get("CODE_TUI_GIT_NAME", "code-tui agent")
    git_mail = os.environ.get("CODE_TUI_GIT_EMAIL", "agent@code-tui.local")
    os.environ.setdefault("GIT_AUTHOR_NAME", git_name)
    os.environ.setdefault("GIT_AUTHOR_EMAIL", git_mail)
    os.environ.setdefault("GIT_COMMITTER_NAME", git_name)
    os.environ.setdefault("GIT_COMMITTER_EMAIL", git_mail)
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
    base_rules = (config.RULES if config.MODE == "state"
                  else TOOL_RULES if config.MODE == "tool" else REPL_RULES)
    rules = base_rules + AUTONOMOUS
    if allow_run:
        rules += RUN_NOTE
    specs = CheckSpec.parse(json.loads(args.checks)) if args.checks.strip() else CheckSpec.parse(args.check.strip())
    verifier = None
    goal_label = "verified"
    legacy_check = os.environ.get("CODE_TUI_LEGACY_CHECK") == "1"
    if specs:
        verifier = Verifier(specs, str(workspace), legacy=legacy_check)
        goal_label = "verified: " + ", ".join(spec.name for spec in specs) + " passed"

    kernel: StreamingKernel | None = None

    def stop_kernel(_signum, _frame) -> None:
        """SIGTERM/SIGINT: stop the kernel child (so its process groups die) and exit 143."""
        if kernel is not None:
            kernel.stop()
        raise SystemExit(143)

    signal.signal(signal.SIGTERM, stop_kernel)
    signal.signal(signal.SIGINT, stop_kernel)

    result = None
    manifest = os.environ.get("CODE_TUI_TOOLS", "").strip()
    extra = custom.load(manifest) if manifest else []
    subagent = (Subagent(client, workspace)
                if os.environ.get("CODE_TUI_SUBAGENTS") == "1" else None)
    skeptic = (Skeptic(client, workspace)
               if os.environ.get("CODE_TUI_SKEPTIC") == "1" else None)
    docs = (DocsClient(cache_dir=state_dir(workspace) / "docs")
            if os.environ.get("CODE_TUI_DOCS", "1") != "0" else None)
    try:
        kernel = StreamingKernel(
            workspace,
            timeout=max(5.0, args.cell_timeout),
            writable=True,
            prelude="all",
            allow_run=allow_run,
            allow_shell=config.MODE not in ("state", "tool"),
            supervise=os.environ.get("CODE_TUI_LEGACY_TIMEOUT") != "1",
            extra_verbs=custom.verbs(extra),
            extra_prelude=custom.prelude(extra),
            check_commands=[{"name": spec.name, "cmd": spec.cmd} for spec in specs],
            emit_event=emit,
            cell_label="code" if config.CODE_MODE else "python",
        )
        loop = config.build(
            client,
            kernel,
            budget,
            rules=rules,
            history=config.HISTORY,
            obs_chars=config.OBS_CHARS,
            stop="call",
            goal_label=goal_label,
            goal_stops=False,
            mode=config.MODE,
            verifier=verifier,
            autonomy=config.AUTONOMY,
            subagent=subagent,
            skeptic=skeptic,
            extra_tools=custom.specs(extra),
            docs=docs,
        )
        original = prompt
        brief = history_brief(workspace)
        if brief and config.MODE == "state":
            prompt = brief + "\n\n[TASK]\n" + prompt
        result = loop.run("tui-task", prompt, EventTrace(emit, verifier))
        try:
            record_history(workspace, original, result, sorted(getattr(loop, "changed", set())))
        except OSError:
            pass
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

    status, payloads = final_payloads(result)
    for payload in payloads:
        emit(payload)
    return 0 if status == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
