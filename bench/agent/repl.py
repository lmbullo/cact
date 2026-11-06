"""ReplLoop: one fenced ```python block per reply (`--mode repl`), plus the rules text
shared with the tool loops.

WHAT IT IS
  The fenced-block alternate to ToolLoop's tool calls: the model writes code, the code runs
  as a kernel cell, and stdout/stderr/result come back as an `[observation]`. It subclasses
  `ToolLoop` and reuses that turn machine: budget stops (STEP_LIMIT/WALL_LIMIT), usage
  accounting, goal rejection (GOAL_UNMET), goal_stops (VERIFIED), trace records and the
  RunResult. This module owns only the repl dialect:
    - reply parsing: one fenced block / ANSWER line (reply.compile_reply)
    - the observation transcript (`observation`, also ToolLoop's default) and `history`
    - the finish switches: stop="call" (final_answer in the cell) or stop="var" (the
      ANSWER variable); a printing cell is UNREVIEWED, a non-action reply is PROTOCOL
  `_reply` is the single seam: ToolLoop.run calls it with the model's reply, and this class
  turns that reply into an Action, a kernel cell and a TurnOutcome.

ENTRY POINTS
  REPL_RULES / BARE_RULES .... system prompts (BARE_RULES is the minimal variant)
  RUN_NOTE / RUN_NOTE_MUT .... appended when run() is enabled; the "shell mutates bypass
                               the journal" contract the model must obey
  observation(cell, cap) ..... one cell -> the text the model sees (the ToolLoop default)
  history_entry(action) ...... a parsed action -> the line kept in the transcript
  ReplLoop(client, kernel, budget, rules, stop="call"|"var",
           history="full"|"obs"|"drop"|"action", obs_chars, goal, goal_label, goal_stops,
           wrap_up_at)
    .run(...)   inherited from tool_loop.ToolLoop
    ._reply(...)  reply -> Action -> cell -> TurnOutcome (the repl seam)

INVARIANTS
  - A reply with neither a python block nor an ANSWER line does not end the task: it comes
    back as PROTOCOL with a note, and the loop continues.
  - `final_answer` in a printing cell is UNREVIEWED: the output is shown and the answer is
    not accepted. With a goal, both ANSWER and final_answer are rejected until it passes
    (GOAL_UNMET).
  - `history` decides what stays in the transcript: the full reply, just the action, or
    nothing. Large observations are capped by `obs_chars` with an `_OBS` handle.
  - `wrap_up_at` sends a "start wrapping up" nudge at that fraction of the step budget; the
    base ToolLoop owns the timing, this class only supplies the fenced-answer wording.
  - Budget, usage, goal gating and the trace summary are ToolLoop's; this module only
    supplies the repl wording (`BUDGET_NOTE`, `CHECK_NOUN`, `REJECT_*_TAIL`) and the
    per-turn wraparound reminders in `_before_turn`.

WHERE TO CHANGE
  What counts as an action ......... reply.py (compile_reply), not here
  Observation format ............... `observation` (also the ToolLoop default)
  REPL finish semantics ............ `_reply` (UNREVIEWED / PROTOCOL / the answer paths)
  Budget, usage, goal gating ....... tool_loop.ToolLoop (`run`, `_push`, `_reject`)
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .kernel import Cell, Kernel
from .llm import Client
from .loop import Budget
from .reply import Action, compile_reply
from .tool_loop import DONE, GOAL_UNMET, VERIFIED, ToolLoop, TurnOutcome

REPL_RULES = """PYTHON REPL CONTRACT
1. Reply with exactly one fenced ```python block. It runs in a persistent kernel: variables, imports and data persist between turns.
2. stdout, stderr and the value of the final expression come back to you as an [observation].
3. To finish, call final_answer(<value>) in a cell that prints nothing. If the cell printed output, the answer is not accepted; the output is shown to you and you resubmit.
4. To finish without code, the last line must be exactly "ANSWER: <value>". A reply with neither a python block nor an ANSWER line does not end the task; it is returned to you as a protocol error.
5. The kernel imports only the standard library; do not install packages into it.
6. run() returns a dict: result['stdout'] (or result.stdout), result['stderr'], result['exit_code'], result['kind']. Print only what the answer needs; output is returned in full.
7. Every write and edit is journalled: changes() lists what this session changed, diff(path=None) returns the unified diff as a string, and undo(path=None) restores files to their session-start state. For an existing file, splice(path, start, end, text) replaces 1-based inclusive lines and returns the hunk; edit() replaces exact old text; edit_many([{path, old, new}, ...]) applies a batch atomically; use write() only for a new file or a real rewrite. For several files in one change, prefer apply(<unified diff>) or commit({path: text, ...}) over many write() calls: both land in one diff and one undo. Read diff() before you finish.
8. Earlier cells keep their full output in _OBS. recall(pattern) searches it, including turns a fold removed, and returns matching snippets plus the handle to page the full text; grep() is regex-by-default and for the tree, recall() is for the session, outline(file) lists headings/defs with line numbers, and guide(query) answers "which module owns this?" when the repo documents its modules with headers.
9. This is not tools mode. Never emit DSML, XML, <|...|>, or tool-call markup. Your reply must contain only prose and at most one fenced python block.
"""

RUN_NOTE = """

RUNNING PROGRAMS
In tools modes, exec(argv, timeout=60, cwd=None, env=None, stdin=None) is the one process tool: argv or commands=[[...],[...]] for programs, shell=<string> for explicit shell syntax. Use cwd=... instead of `cd`; env/stdin are explicit. A shell interpreter with -c/-lc in argv is refused (shell_via_exec): pass shell=<string> instead. Use search()/read() for files.

In a python-only run (repl/bare), run(command or [commands], timeout=60, cwd=None) executes one bash command (or a list) from the project root and returns kind, exit_code, stdout, stderr. Reads, searches, tests, builds and read-only git work as written: `cat file`, `sed -n '1,40p' file`, `rg TODO`, `python -m pytest -q`, `git diff`, `python3 -c '...'`.

Mutation inside the workspace goes through the journal, not a process. Use write(), edit(), splice(), delete(), mkdir() or apply() instead of tee, touch, cp, mv, rm, mkdir, sed -i or `>`; exec and run refuse those with kind "wrong_call" because they would bypass diff() and undo(). Scratch paths (/tmp, /var/tmp, /dev/shm, $TMPDIR) are not gated: rm/mkdir/cp there is fine. A program you run can still write files; the journal only covers the typed helpers, so do not claim undo() covers a script's output.
"""

RUN_NOTE_MUT = """

RUNNING PROGRAMS
In tools modes, exec(argv, timeout=60, cwd=None, env=None, stdin=None) is the one process tool: argv or commands=[[...],[...]] for programs, shell=<string> for explicit shell syntax. cwd replaces `cd`; a bash -lc argv is refused (shell_via_exec), so pass shell=<string> instead.

In a python-only run (repl/bare), run(command or [commands], timeout=60, cwd=None) executes one bash command (or a list) from the project root and returns kind, exit_code, stdout, stderr.

Mutation inside the workspace goes through the journal, not a process. Use write(), edit(), splice(), delete(), mkdir() or apply() instead of tee, touch, cp, mv, rm, mkdir, sed -i or `>`; exec and run refuse those with kind "wrong_call" because they would bypass diff() and undo(). Scratch paths (/tmp, /var/tmp, /dev/shm, $TMPDIR) are not gated: rm/mkdir/cp there is fine. A program you run can still write files; the journal only covers the typed helpers, so do not claim undo() covers a script's output.
"""

BARE_RULES = """Write Python in one ```python block. Cells share a kernel; variables persist. Set ANSWER to the final value to end the task.
"""


def history_entry(action: Action) -> str:
    """One past action as the text a `history=full` run replays (fence or ANSWER line)."""
    if action.kind == "code":
        return f"```python\n{action.code}\n```"
    if action.kind == "answer":
        return f"ANSWER: {action.value}"
    return ""


def observation(cell: Cell, cap: int = 0) -> str:
    """The repl-mode observation block for one cell; over `cap` chars the middle is hidden."""
    parts = [f"[observation] exit={'raise' if cell.stderr else 'ok'} time={cell.duration_ms}ms"]
    if cell.timeout:
        parts.append("timeout: the cell was interrupted; the namespace and variables are intact")
    if cell.result is not None:
        parts.append(f"result: {cell.result}")
    if cell.changed:
        parts.append(f"changed: {', '.join(cell.changed)}")
    if cell.stdout:
        parts.append(f"stdout:\n{cell.stdout.rstrip()}")
    if cell.stderr:
        parts.append(f"stderr:\n{cell.stderr.rstrip()}")
    body = "\n".join(parts)
    if cap and len(body) > cap:
        handle = f"_OBS[{cell.obs_index}]" if cell.obs_index is not None else "_OBS[-1]"
        head = body[: int(cap * 0.65)]
        tail = body[-int(cap * 0.25):]
        body = (f"{head}\n...[middle hidden: {len(body)} chars total; full output is {handle}"
                f" -- print({handle}['stdout'])]...\n{tail}")
    return body


class ReplLoop(ToolLoop):
    """ToolLoop whose action space is one ```python block per reply; `ANSWER:` or a bare reply
    finishes. It inherits the turn machine (pairing, stop reasons, goals) and overrides only the
    reply dialect."""

    BUDGET_NOTE = ("[runtime] The cell budget is exhausted. Reply with the final answer "
                   "as plain text and no code block.")
    CHECK_NOUN = "goal check"
    REJECT_NONE_TAIL = "The task is not complete. Continue working."
    REJECT_DETAIL_TAIL = "Keep working and re-run the check when you believe it passes."
    WRAP_UP_NOTE = ("[runtime] {left} steps left. Stop exploring. Make the smallest necessary "
                    "change, verify it, and finish with final_answer(<short summary>). Do not "
                    "start new investigations.")
    LAST_TURN_NOTE = ("[runtime] This is your last turn. Do not run more code or inspect more "
                      "files. Reply with exactly one fenced ```python block that calls "
                      "final_answer(<short summary>) and prints nothing.")

    def __init__(
        self,
        client: Client,
        kernel: Kernel,
        budget: Budget | None = None,
        rules: str | None = REPL_RULES,
        stop: str = "call",
        history: str = "full",
        obs_chars: int = 0,
        goal: Any = None,
        goal_label: str = "verified",
        goal_stops: bool = True,
        wrap_up_at: float | None = 0.8,
    ) -> None:
        super().__init__(
            client,
            kernel,
            budget,
            rules=rules,
            goal=goal,
            goal_label=goal_label,
            goal_stops=goal_stops,
            obs_chars=obs_chars,
            wrap_up_at=wrap_up_at,
        )
        self.tools = None                   # repl replies carry no tool_calls
        self.stop_mode = stop               # "call" -> cell.final, "var" -> cell.answer
        self.history = history
        self.wrap_up_at = wrap_up_at

    def _on_run_start(self, messages: list[dict]) -> None:
        super()._on_run_start(messages)
        self._prefix_key = hashlib.sha256(
            json.dumps(messages, sort_keys=True).encode()).hexdigest()
        self._drift = 0

    def _before_turn(self, messages: list[dict], tally) -> None:
        super()._before_turn(messages, tally)
        if hashlib.sha256(json.dumps(messages[:2], sort_keys=True).encode()).hexdigest() != self._prefix_key:
            self._drift += 1

    def _drift_count(self) -> int:
        return getattr(self, "_drift", 0)

    def _observe(self, cell, steps: int) -> str:
        return observation(cell, self.obs_chars)

    def _turn_record(self, task_id: str, step: int, resp: dict, hit: int, miss: int) -> dict:
        return {
            "task": task_id,
            "turn": step,
            "latency_ms": round(resp["latency_ms"]),
            "content": resp.get("content") or "",
            "reasoning": resp.get("reasoning") or "",
            "code": None,
            "observation": None,
            "cache": {"hit": hit, "miss": miss},
            "runs": [],
        }

    def _reply(self, messages: list[dict], rec: dict, resp: dict, finish, tally,
               seen: set[str], step: int) -> TurnOutcome:
        """Parse one reply, run its cell, and decide the turn. Repl's only loop seam."""
        content = resp.get("content") or ""
        action = compile_reply(content)
        code = action.code if action.kind == "code" else None
        rec["code"] = code

        if code is None:
            if action.kind == "answer":
                met, check = self._goal_state()
                if not met:
                    if self.history != "drop":
                        messages.append({"role": "assistant", "content": history_entry(action) or content})
                    messages.append({"role": "user", "content": self._reject(check, "ANSWER rejected")})
                    return TurnOutcome(stop=GOAL_UNMET)
                return TurnOutcome(stop=DONE, final=action.value or "", end=True)
            if action.error:
                note = f"[observation] that reply did not produce code: {action.error}. Nothing ran."
                if action.detail:
                    note += f"\n{action.detail}"
                if action.source == "markup":
                    note += "\nThe call was understood but carried no runnable body. Reply with one ```python block."
            else:
                note = "[observation] No ```python block and no line 'ANSWER: <value>' in that reply. The task is still open."
            messages.append({"role": "user", "content": note})
            rec["error"] = action.error
            return TurnOutcome(stop="PROTOCOL")

        if code in seen:
            tally.repeats += 1
        else:
            seen.add(code)
        tally.calls += 1
        cell = self.kernel.run(code)
        rec["runs"] = cell.runs or []
        rec["observation"] = {
            "exit": "raise" if cell.stderr else "ok",
            "result": cell.result,
            "stdout": cell.stdout,
            "stderr": cell.stderr,
            "restarted": cell.restarted,
            "duration_ms": cell.duration_ms,
        }
        if cell.stderr:
            tally.errors += 1

        terminal = cell.final if self.stop_mode == "call" else cell.answer
        if terminal is not None and (cell.stdout.strip() or cell.stderr.strip()):
            if self.history != "drop":
                kept = content if self.history == "full" else history_entry(action)
                messages.append({"role": "assistant", "content": kept})
            messages.append({"role": "user", "content": observation(cell, self.obs_chars) + "\n[observation] final_answer was not accepted: this cell printed output, so you have not seen it yet. Read it, then call final_answer(<value>) in a cell that prints nothing."})
            return TurnOutcome(stop="UNREVIEWED")
        if terminal is not None:
            met, check = self._goal_state()
            if not met:
                if self.history != "drop":
                    kept = content if self.history == "full" else history_entry(action)
                    messages.append({"role": "assistant", "content": kept})
                messages.append({"role": "user", "content": self._reject(check, "final_answer rejected")})
                return TurnOutcome(stop=GOAL_UNMET)
            return TurnOutcome(stop=DONE, final=terminal.strip(), end=True)

        if self.goal_stops and self.goal is not None and cell.mutated and self._goal_state()[0]:
            return TurnOutcome(stop=VERIFIED, final=self.goal_label, end=True)

        if self.history != "drop":
            kept = content if self.history == "full" else history_entry(action)
            messages.append({"role": "assistant", "content": kept})
        messages.append({"role": "user", "content": observation(cell, self.obs_chars)})
        return TurnOutcome()
