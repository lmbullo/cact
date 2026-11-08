"""One task == one worker process == one agent loop. The seam between the TUI and bench/.

WHAT IT IS
  Spawned by tui/src/server.ts for every task. It builds the loop through
  `bench.agent.config.build` (mode from CODE_TUI_MODE, default `state`), runs the task
  unattended, and streams every turn to the server as `@@TUI@@`-prefixed JSON lines on
  stdout. Its exit code is the task verdict.

ENTRY POINTS
  main()                       argv: --workspace, --prompt-file, --model, --effort,
                               --max-steps, --max-wall, --cell-timeout, --max-output,
                               --allow-run, --check, --checks
  emit(payload)                one `@@TUI@@{json}` line on stdout (the only protocol channel)
  StreamingKernel(kernel)      Kernel that also emits `cell.start` before each cell
  EventTrace                   Trace-shaped adapter: turn records -> `turn` (+ `check`) events
  state_dir(workspace)         ~/.local/state/code-tui/workspaces/<sha256(cwd)[:16]>
  history_brief / record_history   history.jsonl read (prompt prefix) and appended

MESSAGE KINDS (consumed by server.ts::handleWorkerLine)
  cell.start {cell, tool, code|args} | cell.end {cell, tool, ok, verb_kind, code, page,
  chars, duration_ms, notes} | turn {turn, data} | check {ok, name, line} | log {text}
  | error {text} | final {status, final, stop, steps, calls, llmCalls, errors, usage, wallMs}
  `check` is emitted right after the turn whose record carried a verdict; it is the one place
  the failing line lives, so the log header can name a failed check without re-running it.

INVARIANTS
  - Rules = STATE_RULES (or TOOL_RULES when MODE != "state") + AUTONOMOUS, plus RUN_NOTE
    when the process tools (`exec`/`git`/`run()`) are enabled. Production is always
    unattended: no human answers questions; typed modes build the kernel with
    `allow_shell=False`, so only repl has `run()`.
  - `--checks` (JSON plan) wins over `--check` (one command); see bench/agent/check_plan.py.
    One `check_plan.Verifier` owns the plan, so the goal re-runs it on every finish attempt and
    `final_answer` is rejected until it passes; `goal_stops=False` means a failing check
    never ends the run by itself. The same object decides the `cheap` health run (the
    `[check]` card line): each spec's `scope` keeps it to the paths that changed, the whole
    plan still runs on a finish, and the cache is injected into the kernel.
  - A turn whose record carries `check` is followed by one `check` event: the verdict, the
    plan entry's *name* (not its command line) and the first failing line of its output, read
    from the Verifier's last `Check`. The journal is the only source; nothing is re-run for it.
  - CODE_TUI_LEGACY_CHECK=1 makes the goal answer with a bare pass/fail (no Check text);
    CODE_TUI_LEGACY_TIMEOUT=1 turns off the in-child cell alarm.
  - Status mapping is fixed: DONE|VERIFIED -> done, STEP_LIMIT|WALL_LIMIT with a non-empty
    summary -> partial, anything else -> failed. Exit 0 only for done.
  - A missing DEEPSEEK_API_KEY or an empty prompt fails fast with NO_API_KEY/EMPTY_PROMPT
    rather than burning a kernel.
  - Fire-and-forget is the contract: `config.AUTONOMY` rejects a finish that asks a human,
    `docs(url)` reads a documentation page parent-side (no kernel network), and
    `CODE_TUI_SUBAGENTS`/`CODE_TUI_SKEPTIC`/`CODE_TUI_TOOLS` are opt-in experiment levers.
    The check plan now includes the project's lint/format scripts, so a correct but dirty
    tree is not done.
  - A fire-and-forget task may be asked to commit. The worker supplies a default git identity
    through GIT_AUTHOR_*/GIT_COMMITTER_* (CODE_TUI_GIT_NAME / CODE_TUI_GIT_EMAIL override),
    and Kernel.start forwards those four variables to exec()/run() children. It never writes
    the user's git config.
  - history_brief is prepended to the prompt (state mode only) so the next task in the same
    workspace knows what the previous ones changed and, capped newest-first, which deferred
    and untested items they left open; record_history persists the structured handoff dict
    (`summary/changed/gates/deferred/untested`) after a completed run even when the task
    failed, because a failed attempt still moved files.

WHERE TO CHANGE
  What the TUI sends the agent .... AUTONOMOUS, the rules assembly, budgets
  What the board sees ............. emit payloads + the status mapping at the end of main()
  Per-task limits ................. argparse defaults (CODE_TUI_MAX_* in server.ts feeds them)
  Cross-task memory ............... history_brief / record_history
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import sys
import time
import traceback
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bench.agent import config, handoff  # noqa: E402
from bench.agent.check_plan import CheckSpec, Verifier  # noqa: E402
from bench.agent.kernel import Kernel  # noqa: E402
from bench.agent.llm import Client, LLMError  # noqa: E402
from bench.agent.repl import REPL_RULES, RUN_NOTE  # noqa: E402
from bench.agent import custom  # noqa: E402
from bench.agent.docs import DocsClient  # noqa: E402
from bench.agent.state_loop import STATE_RULES  # noqa: E402
from bench.agent.subagent import Subagent  # noqa: E402
from bench.agent.skeptic import Skeptic  # noqa: E402
from bench.agent.tool_loop import TOOL_RULES  # noqa: E402

PREFIX = "@@TUI@@"


def state_dir(workspace: Path) -> Path:
    """The per-workspace state root: $XDG_STATE_HOME/code-tui/workspaces/<sha256(cwd)[:16]>."""
    root = Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state"))
    digest = hashlib.sha256(str(workspace.resolve()).encode()).hexdigest()[:16]
    return root / "code-tui" / "workspaces" / digest


def history_brief(workspace: Path, limit: int = 4, issues: int = 6) -> str:
    """The prompt prefix: the last few tasks, then their open deferred/untested items."""
    path = state_dir(workspace) / "history.jsonl"
    if not path.exists():
        return ""
    rows = []
    for line in path.read_text().splitlines()[-limit:]:
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not rows:
        return ""
    lines = []
    for row in rows:
        files = ", ".join(row.get("files") or [])[:120]
        lines.append(f"- {row.get('when', '?')}: {row.get('summary', '')[:180]} [{files}]")
    open_items = []
    for row in reversed(rows):
        for label in ("deferred", "untested"):
            for item in row.get(label) or []:
                open_items.append(f"- {row.get('when', '?')} {label}: {str(item)[:200]}")
                if len(open_items) >= issues:
                    break
            if len(open_items) >= issues:
                break
        if len(open_items) >= issues:
            break
    if open_items:
        lines.append("open items from earlier tasks (newest first):")
        lines.extend(open_items)
    return "[workspace history] previous tasks in this workspace and what they changed:\n" + "\n".join(lines)


def record_history(workspace: Path, prompt: str, result, files: list[str]) -> None:
    """Append one history.jsonl row (last 50 kept) so the next task sees what changed."""
    path = state_dir(workspace) / "history.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    structured = dict(getattr(result, "handoff", None) or {})
    data = handoff.fields(structured) if structured else {}
    if data and not data.get("summary"):
        data["summary"] = str(result.final or "").strip()[:handoff.SUMMARY_CHARS]
    row = {
        "when": time.strftime("%H:%M"),
        "prompt": prompt[:200],
        "summary": (data.get("summary") or (result.final or "").strip())[:400],
        "files": files[:12],
        "stop": result.stop,
        "changed": (data.get("changed") or files)[:12],
        "gates": data.get("gates") or [],
        "deferred": data.get("deferred") or [],
        "untested": data.get("untested") or [],
    }
    rows = []
    if path.exists():
        rows = [line for line in path.read_text().splitlines() if line.strip()][-49:]
    rows.append(json.dumps(row))
    path.write_text("\n".join(rows) + "\n")


def emit(payload: dict[str, Any]) -> None:
    """Write one `@@TUI@@` JSON line to stdout: the only channel server.ts reads."""
    sys.stdout.write(PREFIX + json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


AUTONOMOUS = """

AUTONOMOUS TASK MODE
You are running unattended. No human will answer questions or approve actions.
- Inspect the workspace, make the changes, and verify them with real commands.
- Keep working until the task is complete or you are genuinely blocked.
- Finish with final_answer(<short summary>) in a cell that prints nothing. When you leave
  work open, pass the structured dict instead, so the next task inherits it:
  final_answer({summary, changed, gates, deferred, untested}).
- The summary should say what changed, what you verified, and any blockers.
"""


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


class StreamingKernel(Kernel):
    """A Kernel that emits `cell.start` before each cell and one compact `cell.end` after it."""

    def __init__(self, *args: Any, emit_event, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._emit_event = emit_event
        self._cell_no = 0

    def run(self, code: str, check_cache: dict | None = None,
            extra_obs: list | None = None):
        """One python cell: `cell.start` before it runs, `cell.end` after it answers."""
        self._cell_no += 1
        number = self._cell_no
        self._emit_event({"kind": "cell.start", "cell": number, "tool": "python", "code": code})
        cell = super().run(code, check_cache=check_cache, extra_obs=extra_obs)
        self._emit_event(cell_end_payload(cell, number, "python"))
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
            # The provider's per-turn usage is what the TUI's live counters are made of:
            # in/out tokens, the prompt cache split, and the window they have to fit in.
            "usage": record.get("usage") or {},
            "window": config.CONTEXT_WINDOW,
            "check": record.get("check"),
            "notice": record.get("notice"),
        }
        self._emit_event({"kind": "turn", "turn": int(record.get("turn") or 0), "data": data})
        # The verdict rides its own event: the header reads the *last* one, so a later pass
        # clears an earlier failure instead of leaving it on screen.
        check = record.get("check")
        if isinstance(check, dict):
            self._emit_event(check_payload(check, self._verifier))

    def close(self) -> None:
        """No-op: there is no file to close, the events were streamed as they happened."""
        pass


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
    # Fire-and-forget tasks can be asked to commit; a fresh workspace has no git identity.
    # Supply one for the kernel's run() children without touching the user's git config.
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
    base_rules = (STATE_RULES if config.MODE == "state"
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

    if result.error:
        emit({"kind": "error", "text": result.error})
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
            "error": result.error,
        }
    )
    return 0 if status == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
