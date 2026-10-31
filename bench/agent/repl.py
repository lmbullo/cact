"""ReplLoop: one fenced ```python block per reply (`--mode repl`), plus the rules text
shared with the tool loops.

WHAT IT IS
  The model writes code, the code runs as a kernel cell, and stdout/stderr/result come back
  as an `[observation]`. Finishing is `final_answer(...)` inside the cell (stop="call") or an
  `ANSWER: <value>` line (stop="answer"). Reply text is parsed by reply.compile_reply.

ENTRY POINTS
  REPL_RULES / BARE_RULES .... system prompts (BARE_RULES is the minimal variant)
  RUN_NOTE / RUN_NOTE_MUT .... appended when run() is enabled; the "run() is not a file
                               interface" contract the model must obey
  observation(cell, cap) ..... one cell -> the text the model sees (the ToolLoop default)
  history_entry(action) ...... a parsed action -> the line kept in the transcript
  ReplLoop(client, kernel, budget, rules, stop="call"|"answer", history="full"|"action"|
           "drop", obs_chars, goal, goal_label, goal_stops, wrap_up_at)
    .run(task_id, prompt, trace) -> RunResult

INVARIANTS
  - A reply with neither a python block nor an ANSWER line does not end the task: it comes
    back as PROTOCOL with a note, and the loop continues.
  - `final_answer` in a printing cell is UNREVIEWED: the output is shown and the answer is
    not accepted. With a goal, both ANSWER and final_answer are rejected until it passes
    (GOAL_UNMET).
  - `history` decides what stays in the transcript: the full reply, just the action, or
    nothing. Large observations are capped by `obs_chars` with an `_OBS` handle.
  - `wrap_up_at` sends a "start wrapping up" nudge at that fraction of the wall budget.

WHERE TO CHANGE
  What counts as an action ......... reply.py (compile_reply), not here
  Observation format ............... `observation` (also the ToolLoop default)
  REPL finish semantics ............ ReplLoop.run, `_goal_state`, `_reject`
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from .check import evaluate
from .kernel import Cell, Kernel
from .llm import Client, LLMError
from .loop import Budget, RunResult, _usage_add
from .reply import Action, compile_reply

REPL_RULES = """PYTHON REPL CONTRACT
1. Reply with exactly one fenced ```python block. It runs in a persistent kernel: variables, imports and data persist between turns.
2. stdout, stderr and the value of the final expression come back to you as an [observation].
3. To finish, call final_answer(<value>) in a cell that prints nothing. If the cell printed output, the answer is not accepted; the output is shown to you and you resubmit.
4. To finish without code, the last line must be exactly "ANSWER: <value>". A reply with neither a python block nor an ANSWER line does not end the task; it is returned to you as a protocol error.
5. The kernel imports only the standard library; do not install packages into it.
6. Print only what the answer needs. Output is returned in full.
7. Every write and edit is journalled: changes() lists what this session changed, diff(path=None) returns the unified diff, and undo(path=None) restores files to their session-start state. Read diff() before you finish.
8. This is not tools mode. Never emit DSML, XML, <|...|>, or tool-call markup. Your reply must contain only prose and at most one fenced python block.
"""

RUN_NOTE = """

RUNNING PROGRAMS
run(command, timeout=60, cwd=None) executes one bash command from the project root and returns kind, exit_code, stdout, stderr. Use it only for programs that do work: test runners, compilers, linters, formatters, package managers, git, servers, downloads, builds.

run() is not a file interface. Every file action already has a direct call:
  read a file      -> read(path, offset=1, limit=0)  (whole file; raw=True for unnumbered text)
  search text      -> grep(pattern, path='.', max_results=50)
  list files       -> map(path='.')
  write a file     -> write(path, content)  (force=True only to intentionally drop a tail)
  edit a file      -> edit(path, old, new)
Passing any of these to run() is a protocol violation, not a shortcut: cat, head, tail, less, more, wc, stat, grep, rg, ag, sed, awk, find, ls, tree, du, tee, touch, cp, mv, rm, truncate, echo with >, printf with >, and python3 -c or python3 -m that imports, prints or inspects a project file. run() executes whole programs only: python3 app.py ledger.csv, pytest, make, git, docker.

Why: importing a module to print one constant is file inspection wearing a program's clothes. Shell output is also unparsed and unnumbered. The calls above return path, line numbers, total_lines, the next offset, and conflict/ambiguity/no-match diagnostics. A shell command returns text you must parse. run() checks the command and refuses file verbs with a wrong_call result naming the call to use. If the command you are about to pass begins with one of those words, you have picked the wrong call.
"""

RUN_NOTE_MUT = """

RUNNING PROGRAMS
run(command, timeout=60, cwd=None) executes one bash command from the project root and returns kind, exit_code, stdout, stderr. Use it only for programs that do work: test runners, compilers, linters, formatters, package managers, git, servers, downloads, builds.

run() is not a file interface. Every file action already has a direct call:
  read a file      -> open(path).read()
  search text      -> a string or regex check on that text
  list files       -> os.walk / os.listdir
  write a file     -> write(path, content)  (force=True only to intentionally drop a tail)
  edit a file      -> edit(path, old, new)
Passing any of these to run() is a protocol violation, not a shortcut: cat, head, tail, less, more, wc, stat, grep, rg, ag, sed, awk, find, ls, tree, du, tee, touch, cp, mv, rm, truncate, echo with >, printf with >, and python3 -c or python3 -m that imports, prints or inspects a project file. run() executes whole programs only: python3 app.py ledger.csv, pytest, make, git, docker.

Why: importing a module to print one constant is file inspection wearing a program's clothes. Shell output is also unparsed and unnumbered. write() and edit() return conflict, ambiguity and closest-match diagnostics; a shell command returns text you must parse, truncated to its last 6000 characters. If the command you are about to pass begins with one of those words, you have picked the wrong call.
"""

BARE_RULES = """Write Python in one ```python block. Cells share a kernel; variables persist. Set ANSWER to the final value to end the task.
"""


def history_entry(action: Action) -> str:
    if action.kind == "code":
        return f"```python\n{action.code}\n```"
    if action.kind == "answer":
        return f"ANSWER: {action.value}"
    return ""


def observation(cell: Cell, cap: int = 0) -> str:
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


class ReplLoop:
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
        self.client = client
        self.kernel = kernel
        self.budget = budget or Budget()
        self.rules = rules
        self.stop_mode = stop
        self.history = history
        self.obs_chars = obs_chars
        self.goal = goal
        self.goal_label = goal_label
        self.goal_stops = goal_stops
        self.wrap_up_at = wrap_up_at

    def _goal_state(self):
        return evaluate(self.goal)

    def _reject(self, check, prefix: str) -> str:
        if check is None:
            return f"[observation] {prefix}: the goal check still fails. The task is not complete. Continue working."
        return (f"[observation] {prefix}: the goal check says the task is not complete.\n"
                f"{check.text()}\nKeep working and re-run the check when you believe it passes.")

    def _push(self, messages: list[dict], usage: dict | None = None) -> str:
        msgs = messages + [{
            "role": "user",
            "content": "[runtime] The cell budget is exhausted. Reply with the final answer as plain text and no code block.",
        }]
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
        messages: list[dict] = []
        if self.rules:
            messages.append({"role": "system", "content": self.rules})
        messages.append({"role": "user", "content": prompt})
        prefix_key = hashlib.sha256(json.dumps(messages, sort_keys=True).encode()).hexdigest()
        drift = 0
        steps = calls = errors = repeats = 0
        llm_calls = 0
        seen: set[str] = set()
        usage: dict = {}
        turns: list[dict] = []
        final = ""
        stop = "STEP_LIMIT"
        wrapped = False
        last_turn = False

        def push() -> str:
            nonlocal llm_calls
            llm_calls += 1
            return self._push(messages, usage)

        while True:
            if steps >= b.max_steps:
                final, stop = push(), "STEP_LIMIT"
                break
            if (time.perf_counter() - t0) > b.max_wall_s:
                final, stop = push(), "WALL_LIMIT"
                break

            if not last_turn and steps >= max(1, b.max_steps - 1):
                last_turn = True
                messages.append({
                    "role": "user",
                    "content": (
                        "[runtime] This is your last turn. Do not run more code or inspect more files. "
                        "Reply with exactly one fenced ```python block that calls "
                        "final_answer(<short summary>) and prints nothing."
                    ),
                })
            elif self.wrap_up_at is not None and not wrapped:
                threshold = max(1, int(b.max_steps * self.wrap_up_at))
                if steps >= threshold and steps < max(1, b.max_steps - 1):
                    wrapped = True
                    messages.append({
                        "role": "user",
                        "content": (
                            f"[runtime] {b.max_steps - steps} steps left. Stop exploring. "
                            "Make the smallest necessary change, verify it, and finish with "
                            "final_answer(<short summary>). Do not start new investigations."
                        ),
                    })

            steps += 1
            llm_calls += 1
            if hashlib.sha256(json.dumps(messages[:2], sort_keys=True).encode()).hexdigest() != prefix_key:
                drift += 1
            resp = self.client.complete(messages, None, b.max_output_tokens)
            _usage_add(usage, resp["usage"])
            cu = resp.get("usage") or {}
            hit = int(cu.get("prompt_cache_hit_tokens") or 0)
            miss = int(cu.get("prompt_cache_miss_tokens") or 0)
            content = resp.get("content") or ""
            action = compile_reply(content)
            code = action.code if action.kind == "code" else None
            rec: dict = {
                "task": task_id,
                "turn": steps,
                "latency_ms": round(resp["latency_ms"]),
                "content": content,
                "reasoning": resp.get("reasoning") or "",
                "code": code,
                "observation": None,
                "cache": {"hit": hit, "miss": miss},
                "runs": [],
            }

            if code is None:
                if action.kind == "answer":
                    met, check = self._goal_state()
                    if not met:
                        if self.history != "drop":
                            messages.append({"role": "assistant", "content": history_entry(action) or content})
                        messages.append({"role": "user", "content": self._reject(check, "ANSWER rejected")})
                        rec["stop"] = "GOAL_UNMET"
                        turns.append(rec)
                        if trace:
                            trace.write(rec)
                        continue
                    final = action.value or ""
                    stop = "DONE"
                    rec["stop"] = stop
                    turns.append(rec)
                    if trace:
                        trace.write(rec)
                    break
                if action.error:
                    note = f"[observation] that reply did not produce code: {action.error}. Nothing ran."
                    if action.detail:
                        note += f"\n{action.detail}"
                    if action.source == "markup":
                        note += "\nThe call was understood but carried no runnable body. Reply with one ```python block."
                else:
                    note = "[observation] No ```python block and no line 'ANSWER: <value>' in that reply. The task is still open."
                messages.append({"role": "user", "content": note})
                rec["stop"] = "PROTOCOL"
                rec["error"] = action.error
                turns.append(rec)
                if trace:
                    trace.write(rec)
                continue

            if code in seen:
                repeats += 1
            else:
                seen.add(code)
            calls += 1
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
                errors += 1

            terminal = cell.final if self.stop_mode == "call" else cell.answer
            if terminal is not None and (cell.stdout.strip() or cell.stderr.strip()):
                if self.history != "drop":
                    kept = content if self.history == "full" else history_entry(action)
                    messages.append({"role": "assistant", "content": kept})
                messages.append({"role": "user", "content": observation(cell, self.obs_chars) + "\n[observation] final_answer was not accepted: this cell printed output, so you have not seen it yet. Read it, then call final_answer(<value>) in a cell that prints nothing."})
                rec["stop"] = "UNREVIEWED"
                turns.append(rec)
                if trace:
                    trace.write(rec)
                continue
            if terminal is not None:
                met, check = self._goal_state()
                if not met:
                    if self.history != "drop":
                        kept = content if self.history == "full" else history_entry(action)
                        messages.append({"role": "assistant", "content": kept})
                    messages.append({"role": "user", "content": self._reject(check, "final_answer rejected")})
                    rec["stop"] = "GOAL_UNMET"
                    turns.append(rec)
                    if trace:
                        trace.write(rec)
                    continue
                final = terminal.strip()
                stop = "DONE"
                rec["stop"] = stop
                turns.append(rec)
                if trace:
                    trace.write(rec)
                break

            if self.goal_stops and self.goal is not None and cell.mutated and self._goal_state()[0]:
                final = self.goal_label
                stop = "VERIFIED"
                rec["stop"] = stop
                turns.append(rec)
                if trace:
                    trace.write(rec)
                break

            if self.history != "drop":
                kept = content if self.history == "full" else history_entry(action)
                messages.append({"role": "assistant", "content": kept})
            messages.append({"role": "user", "content": observation(cell, self.obs_chars)})
            turns.append(rec)
            if trace:
                trace.write(rec)

        wall_ms = (time.perf_counter() - t0) * 1000.0
        if trace:
            trace.write({
                "task": task_id,
                "summary": True,
                "stop": stop,
                "steps": steps,
                "calls": calls,
                "llm_calls": llm_calls,
                "errors": errors,
                "misses": 0,
                "repeats": repeats,
                "drift": drift,
                "usage": usage,
                "wall_ms": round(wall_ms),
                "final": final,
            })
            trace.close()

        return RunResult(
            task=task_id,
            stop=stop,
            final=final,
            steps=steps,
            calls=calls,
            errors=errors,
            misses=0,
            repeats=repeats,
            usage=usage,
            wall_ms=wall_ms,
            trace=getattr(trace, "path", None),
            turns=turns,
            llm_calls=llm_calls,
        )
