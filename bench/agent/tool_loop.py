"""ToolLoop: one tool (`python`) executed as a kernel cell (`--mode tool`).

WHAT IT IS
  The turn machine the production loop extends. The model gets a single function,
  `PYTHON_TOOL`, whose `code` argument is run by `Kernel.run`. The cell's stdout/stderr/result
  becomes the observation; a `goal` callable (typically `run_check` from check.py) decides
  whether the task may finish.

ENTRY POINTS
  PYTHON_TOOL .. the single tool spec sent to the client
  TOOL_RULES ... system prompt: use the kernel for everything, finish with final_answer
  ToolLoop(client, kernel, budget, rules, tools, goal, goal_label, goal_stops, obs_chars,
           health)
    .run(task_id, prompt, trace) -> RunResult
  Hooks subclasses override: `_observe(cell, steps)` (cell -> text), `_compact(messages)`
    (context management), `_goal_state()` (goal -> (ok, Check)).

INVARIANTS
  - `final_answer` in a cell that printed is *staged* (STAGED): the output is shown and the
    answer is held. A next reply with no tool call accepts the staged answer (after the goal
    and read gates), so output is never lost and no extra empty cell is required.
  - With a goal, finishing before the check passes is rejected (GOAL_UNMET).
    `goal_stops=True` ends the run itself once a mutating cell passes the goal (VERIFIED).
  - `health` is an optional callable built from the plan's `cheap` specs (worker.py uses
    checks.run_plan, which respects each spec's `scope`). After a mutating cell, at most once
    every 20s, its result lands in `self.last_check` and is rendered as the `[check]` line of
    the next card. It never ends the run; the full goal does.
  - A finish is never rejected for citation coverage: `read()` records ranges for the
    `[read]` card, but reads through `run()`/`open()` are equally valid and the ledger cannot
    see them, so gating the finish on it was inconsistent.
  - BAD_ARGS / TRUNCATED mean nothing ran and the model is told why.
  - Stop vocabulary: DONE, VERIFIED, STEP_LIMIT, WALL_LIMIT, STAGED, GOAL_UNMET,
    TRUNCATED, BAD_ARGS.
  - `changed` accumulates every file the kernel reports; worker.py records it as history.

WHERE TO CHANGE
  Finish protocol / stop reasons ....... ToolLoop.run, `_reject`, `_push`
  What the model sees per cell ......... `_observe` (StateLoop overrides it with state cards)
  Rules and tool description ........... TOOL_RULES, PYTHON_TOOL
  When a task counts as done ........... the `goal` passed in by run.py / worker.py
  The cheap check in the card .......... `health`, `_health_at`, `last_check` (remember to
                                          reset it in `_observe` when you change the card)
"""
from __future__ import annotations

import json
import time
from typing import Any

from .check import Check, evaluate
from .kernel import Kernel
from .llm import Client, LLMError
from .loop import Budget, RunResult, _usage_add
from .repl import observation

PYTHON_TOOL = {
    "type": "function",
    "function": {
        "name": "python",
        "description": (
            "Execute Python in a persistent kernel. Variables, imports and data persist between calls. "
            "The value of the last expression and anything printed are returned as the tool result."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python source to execute in the kernel."},
            },
            "required": ["code"],
            "additionalProperties": False,
        },
    },
}

TOOL_RULES = """You act by calling the python tool. The call is executed in a persistent kernel: variables, imports and data persist between calls, and the value of the last expression plus anything printed come back as the tool result.

Use the kernel for everything: read(), write(), edit(), grep(), map() for files, guide() for a repo that documents its own modules (guide(query) returns the module that owns a topic, with its entry points and where to change it), and run() for whole programs such as tests, builds and linters.

Prefer one large call over many small ones. A single call can loop over files, run several commands, inspect and compute, and print everything you need. Do not spend one call per read or grep.

Every write and edit is journalled: changes() lists what this session changed, diff(path=None) returns the unified diff as a string, and undo(path=None) restores files to their session-start state. Read diff() before you finish, so you review what you actually changed.

Earlier cells keep their full output in _OBS. recall(pattern) searches it, including turns a fold removed, and returns matching snippets plus the handle to page the full text; grep() is for the tree, recall() is for the session. Use recall() before re-running a command whose result you need again.

For several files in one change, prefer commit({path: text, ...}) or apply(<unified diff>) over many write() calls: both land in one diff and one undo. run() runs any command, including cat/grep/sed/tests/git; only shell verbs that mutate files inside the workspace (tee, cp, mv, rm, sed -i, `>`) are refused, because they would bypass the journal; scratch paths (/tmp, /var/tmp, /dev/shm, $TMPDIR) are not gated.

Finish by calling the python tool with a cell that calls final_answer("<summary>") and prints nothing. If a cell prints output and also calls final_answer, the answer is rejected and the output is returned to you to read first. When the task asks for a specific value, put that value in final_answer, not in prose.

A reply with no tool call also ends the task; its text is the summary.
"""


class ToolLoop:
    def __init__(
        self,
        client: Client,
        kernel: Kernel,
        budget: Budget | None = None,
        rules: str = TOOL_RULES,
        tools: list[dict] | None = None,
        goal: Any = None,
        goal_label: str = "verified",
        goal_stops: bool = False,
        obs_chars: int = 0,
        health: Any = None,
    ) -> None:
        self.client = client
        self.kernel = kernel
        self.budget = budget or Budget()
        self.rules = rules
        self.tools = tools or [PYTHON_TOOL]
        self.goal = goal
        self.goal_label = goal_label
        self.goal_stops = goal_stops
        self.obs_chars = obs_chars
        self.changed: set[str] = set()
        self.compactions = 0
        self.health = health
        self.last_check: Any = None
        self._health_at = 0.0
        self.check_cache: dict[str, str] = {}

    def _observe(self, cell, steps: int) -> str:
        return observation(cell, self.obs_chars)

    def _compact(self, messages: list[dict]) -> None:
        return None

    def _goal_state(self):
        return evaluate(self.goal)

    def _note_cell(self, cell) -> None:
        return None

    def _reject(self, check, prefix: str) -> str:
        if check is None:
            return f"[observation] {prefix}: the project check still fails. Keep working."
        return (f"[observation] {prefix}: the project check says the task is not complete.\n"
                f"{check.text()}\nKeep working and re-run the check when you believe it passes.")

    def _push(self, messages: list[dict], usage: dict | None = None) -> str:
        msgs = messages + [{
            "role": "user",
            "content": "[runtime] The step budget is exhausted. Reply with a short final summary and no tool call.",
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

        steps = calls = errors = repeats = llm_calls = 0
        usage: dict = {}
        turns: list[dict] = []
        final = ""
        stop = "STEP_LIMIT"
        seen: set[str] = set()
        pending: str | None = None

        while True:
            self._compact(messages)
            if steps >= b.max_steps or (time.perf_counter() - t0) > b.max_wall_s:
                final, stop = self._push(messages, usage), ("STEP_LIMIT" if steps >= b.max_steps else "WALL_LIMIT")
                break

            steps += 1
            llm_calls += 1
            resp = self.client.complete(messages, self.tools, b.max_output_tokens)
            _usage_add(usage, resp["usage"])
            cu = resp.get("usage") or {}
            hit = int(cu.get("prompt_cache_hit_tokens") or 0)
            miss = int(cu.get("prompt_cache_miss_tokens") or 0)
            self.last_prompt_tokens = hit + miss
            content = resp.get("content") or ""
            tool_calls = resp.get("tool_calls") or []
            finish = resp.get("finish")
            rec: dict = {
                "task": task_id,
                "turn": steps,
                "latency_ms": round(resp["latency_ms"]),
                "content": content,
                "reasoning": resp.get("reasoning") or "",
                "tool_calls": [tc.get("function", {}).get("name") for tc in tool_calls],
                "code": None,
                "observation": None,
                "cache": {"hit": hit, "miss": miss},
                "runs": [],
                "finish": finish,
            }

            if not tool_calls:
                if finish == "length":
                    messages.append({"role": "user", "content": "[runtime] Your reply was cut off before it finished. Continue from where it stopped."})
                    rec["stop"] = "TRUNCATED"
                    turns.append(rec)
                    if trace:
                        trace.write(rec)
                    continue
                candidate = pending if pending is not None else content.strip()
                met, check = self._goal_state()
                if self.goal is not None and not met:
                    notice = self._reject(check, "You finished without a passing check")
                    messages.append({"role": "user", "content": notice})
                    rec["notice"] = notice[:2000]
                    rec["stop"] = "GOAL_UNMET"
                    turns.append(rec)
                    if trace:
                        trace.write(rec)
                    pending = None
                    continue
                final = candidate
                stop = "DONE"
                rec["stop"] = stop
                turns.append(rec)
                if trace:
                    trace.write(rec)
                break

            messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})

            terminal = None
            for call in tool_calls:
                fn = call.get("function") or {}
                name = fn.get("name") or ""
                call_id = call.get("id") or f"call_{steps}_{calls}"
                if name != "python":
                    messages.append({"role": "tool", "tool_call_id": call_id,
                                     "content": f"[observation] unknown tool {name!r}; the only tool is python"})
                    continue
                if finish == "length":
                    messages.append({"role": "tool", "tool_call_id": call_id,
                                     "content": "[observation] the call arguments were truncated by the output limit. Nothing ran. Split the work into smaller cells."})
                    rec["stop"] = "TRUNCATED"
                    continue
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError as exc:
                    messages.append({"role": "tool", "tool_call_id": call_id,
                                     "content": f"[observation] arguments were not valid JSON: {exc}. Nothing ran."})
                    rec["stop"] = "BAD_ARGS"
                    continue
                code = args.get("code")
                if not isinstance(code, str) or not code.strip():
                    messages.append({"role": "tool", "tool_call_id": call_id,
                                     "content": "[observation] the code argument was empty. Nothing ran."})
                    rec["stop"] = "BAD_ARGS"
                    continue

                if code in seen:
                    repeats += 1
                else:
                    seen.add(code)
                calls += 1
                rec["code"] = code
                cell = self.kernel.run(code, check_cache=self.check_cache)
                rec["runs"] = (rec["runs"] or []) + (cell.runs or [])
                rec["reads"] = cell.reads or {}
                self._note_cell(cell)
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
                if cell.mutated:
                    self.check_cache = {}
                    self.last_check = None
                    if self.health is not None:
                        now = time.perf_counter()
                        if now - self._health_at >= 20.0:
                            self._health_at = now
                            try:
                                outcome = self.health(sorted(cell.changed))
                            except Exception:
                                outcome = None
                            if isinstance(outcome, tuple):
                                self.last_check, results = outcome
                                self.check_cache = {cmd: {"text": item.text(600), "ok": bool(item.ok)}
                                                    for cmd, item in results.items() if item is not None}
                            else:
                                self.last_check = outcome
                            if isinstance(self.last_check, bool):
                                self.last_check = Check(
                                    cmd="health", state="passed" if self.last_check else "failed")
                            if self.last_check is not None:
                                rec["check"] = {"ok": bool(self.last_check.ok), "cmd": self.last_check.cmd}
                if cell.changed:
                    self.changed.update(cell.changed)
                obs = self._observe(cell, steps)
                rec["obs"] = obs

                if cell.final is not None:
                    if cell.stdout.strip() or cell.stderr.strip():
                        pending = cell.final
                        notice = (obs + f"\n[finish] answer staged: {str(cell.final)[:400]!r}. "
                                  "The cell also produced the output above; check it. Your next reply "
                                  "with no tool call accepts this answer, or call final_answer(...) "
                                  "again to change it.")
                        messages.append({"role": "tool", "tool_call_id": call_id, "content": notice})
                        rec["notice"] = notice[-2000:]
                        rec["stop"] = "STAGED"
                        continue
                    met, check = self._goal_state()
                    if not met:
                        notice = self._reject(check, "final_answer rejected")
                        messages.append({"role": "tool", "tool_call_id": call_id,
                                         "content": notice})
                        rec["notice"] = notice[:2000]
                        rec["stop"] = "GOAL_UNMET"
                        continue
                    messages.append({"role": "tool", "tool_call_id": call_id, "content": obs})
                    terminal = cell.final
                    continue

                pending = None
                messages.append({"role": "tool", "tool_call_id": call_id, "content": obs})
                if self.goal_stops and self.goal is not None and cell.mutated and self._goal_state()[0]:
                    terminal = self.goal_label

            if terminal is not None:
                final = terminal.strip()
                stop = "VERIFIED" if final == self.goal_label else "DONE"
                rec["stop"] = stop
                turns.append(rec)
                if trace:
                    trace.write(rec)
                break

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
                "drift": 0,
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