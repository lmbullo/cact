"""ToolLoop: typed kernel verbs plus the `python` escape hatch (`--mode tool`).

WHAT IT IS
  The turn machine the production loop extends. The model gets the typed tools generated
  from `verbs.VERBS` (guide/map/read/session_diff/search/recall/outline/edit/splice/write/apply/exec/git)
  plus `PYTHON_TOOL`, the persistent-kernel escape hatch. A call is dispatched to
  `Kernel.call(name, args)`; the python call to `Kernel.run(code)`. Both return the same
  `Cell`; a `Verifier` (checks.py) decides whether the task may finish and what the cheap
  `[check]` line says.

  One `run()` is a sequence of *turns*; one turn is one reply with zero or more tool calls.
  The machine keeps four explicit pieces of state instead of ad-hoc locals:
    FinishState   the staged answer, and the accepted (terminal) one
    TurnOutcome   what a turn decided: keep going, or end the run with a stop reason
    CallRecord    one record per tool call in the turn, kept in `turns` and the trace
    Tally         the run counters (steps, calls, errors, repeats, llm_calls)

ENTRY POINTS
  PYTHON_TOOL ... the persistent-kernel tool spec (the escape hatch)
  tools.TYPE_TOOLS .. the typed specs generated from verbs.VERBS (the default set)
  TOOL_RULES ... system prompt: typed tools for I/O and mutation, python for computation
  Stop vocabulary: DONE, VERIFIED, STEP_LIMIT, WALL_LIMIT, STAGED, GOAL_UNMET, TRUNCATED,
                   BAD_ARGS, LLM_FAILED (module constants, collected in STOPS)
  FinishState()  .stage(v) / .accept(v) / .clear() / .staged / .terminal
  CallRecord(index, name, call_id) .. .code/.args/.observation/.runs/.reads/.notice/.stop
  TurnOutcome(stop, final, end)
  ToolLoop(client, kernel, budget, rules, tools, goal, goal_label, goal_stops, obs_chars,
           health, verifier)
    .run(task_id, prompt, trace) -> RunResult
  Hooks subclasses override: `_reply(messages, rec, resp, finish, tally, seen, step)`
    (reply -> TurnOutcome; ReplLoop's fenced-block dialect is the only other implementation),
    `_observe(cell, steps)` (cell -> text), `_compact(messages)` (context management),
    `_goal_state()` (goal -> (ok, Check)), and `_on_run_start` / `_before_turn` /
    `_drift_count` (per-run state, per-turn nudges, trace drift).

INVARIANTS
  - The staged answer (`FinishState.staged`) is set by `final_answer` in any cell (STAGED).
    A printing cell shows the output and holds the answer; a silent cell holds it too, so a
    model can call `final_answer("short")` and still write the real handoff in the next
    message. The next reply with no tool call accepts it (after the goal check, when there
    is one); for a silent stage that reply's content wins over the short tool value. A
    non-final cell in a later turn clears it (a later call in the same turn does not), and
    so does a no-tool reply whose goal check rejects the finish.
  - A silent `final_answer` is terminal intent for its turn: the remaining tool calls in the
    same assistant message are NOT executed, but each still gets its paired `role: "tool"`
    message (marked skipped), so the next request is valid.
  - `_answer_calls` appends exactly one `role: "tool"` message per `tool_calls` entry, even
    when dispatch raises, so an assistant `tool_calls` message is never orphaned.
  - `turns` holds one record per turn with `calls`: one CallRecord per tool call. The
    top-level `code`/`observation`/`reads`/`notice` fields mirror the last call that set
    them (the pre-`calls` shape), and `runs` concatenates every call's runs.
  - The record's `stop` is derived once from the turn's outcome, never overwritten branch by
    branch; a continuing turn reports the last call-level stop (or none).
  - With a goal, finishing before the check passes is rejected (GOAL_UNMET).
    `goal_stops=True` ends the run itself once a mutating cell passes the goal (VERIFIED).
  - A `verifier` (checks.Verifier) owns the goal, the cheap subset, the scope filter, the
    20s throttle, the check cache and its rendering. `_goal_state` asks it for the whole
    plan; after a mutating cell `invalidate()` drops the cache, and `should_run`/`run`
    refresh `self.last_check` (the `[check]` line of the next card). It never ends the run;
    the full goal does. `goal=`/`health=` remain accepted for the benchmark and smoke tests.
  - A finish is never rejected for citation coverage: `read()` records ranges for the
    `[read]` card, but reads through python `run()`/`open()` are equally valid and the ledger
    cannot see them, so gating the finish on it was inconsistent.
  - BAD_ARGS / TRUNCATED mean nothing ran and the model is told why.
  - A primary `client.complete` failure (LLMError) is not an exception out of `run`: the
    loop stops with LLM_FAILED, keeping whatever final/staged answer exists, and still writes
    the trace summary. `_push` already turns its own LLMError into an empty summary text.
  - `tally.steps` counts turns (primary completes); `tally.llm_calls` counts every request,
    including the budget-forced `_push`, so `llm_calls >= steps`.
  - `changed` accumulates every file the kernel reports; worker.py records it as history.

WHERE TO CHANGE
  Finish protocol / stop reasons ....... Stop constants, FinishState, `_finish_reply`,
                                          `_answer_calls`, `_dispatch`
  Per-call record shape ................ CallRecord.as_dict, `_merge_calls`
  What the model sees per cell ......... `_observe` (StateLoop overrides it with state cards)
  Another reply dialect ................ `_reply` (ReplLoop overrides it in repl.py)
  Budget / goal-check wording .......... BUDGET_NOTE, CHECK_NOUN, REJECT_*_TAIL
  Rules and the python escape hatch .... TOOL_RULES, PYTHON_TOOL
  Typed tool names, schemas, summaries . verbs.VERBS (tools.py follows); `_dispatch` routes
                                          python to Kernel.run and every typed name to Kernel.call
  When a task counts as done ........... the `verifier` passed in by run.py / worker.py (or
                                          `goal=` for a bare callable)
  The cheap check in the card .......... `_refresh_health`, `last_check`, checks.Verifier
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from .check import Check, evaluate
from .kernel import Kernel
from .llm import Client, LLMError
from .loop import Budget, RunResult, _usage_add
from .tools import TOOL_NAMES, TYPE_TOOLS
from .verbs import VERBS

# --- stop vocabulary: the only stop names a RunResult or a trace record may carry ---------
DONE = "DONE"                    # the model finished; its answer (or prose) is final
VERIFIED = "VERIFIED"            # goal_stops: a mutating cell passed the goal
STEP_LIMIT = "STEP_LIMIT"        # budget.max_steps exhausted
WALL_LIMIT = "WALL_LIMIT"        # budget.max_wall_s exhausted
STAGED = "STAGED"                # turn staged an answer; the run continues
GOAL_UNMET = "GOAL_UNMET"        # a finish was rejected; the run continues
TRUNCATED = "TRUNCATED"          # the reply was cut off; nothing ran
BAD_ARGS = "BAD_ARGS"            # the call arguments were unusable; nothing ran
LLM_FAILED = "LLM_FAILED"        # the primary model call raised LLMError; the run stops
STOPS = (DONE, VERIFIED, STEP_LIMIT, WALL_LIMIT, STAGED, GOAL_UNMET, TRUNCATED, BAD_ARGS,
         LLM_FAILED)

PYTHON_TOOL = {
    "type": "function",
    "function": {
        "name": "python",
        "description": (
            "Execute Python in a persistent kernel. Variables, imports and data persist between calls. "
            "The value of the last expression and anything printed are returned as the tool result. "
            "Use the typed tools for reading, searching, editing and running programs; use python "
            "for computation and for anything the typed tools do not cover. In typed modes the "
            "process tool is exec; run() is available only in a python-only repl/bare run. "
            "help() lists the verbs with their live signatures."
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

TOOL_RULES = """You act by calling tools. The typed tools (guide, map, read, session_diff, search, recall, outline, edit, splice, write, delete, mkdir, apply, exec, git) execute against the workspace and return structured observations. A typed observation has `[observe]`/`[page]` (`shown/total/next/unit/has_more/complete`) and `[data]`; the card does not clip it, so trust `page.complete` rather than a prose tail. The python tool is a persistent kernel: variables, imports and data persist between calls; use it for computation and anything the typed tools do not cover. In typed modes there is no run() helper; exec(shell=...) is the explicit shell escape hatch.

For "which module owns this?", use guide(query) (ranked rows; total/next are truthful, default limit 10) or guide(path) for one module's full header. For files, use map, outline, read and search. map(path, include='*.md') lists files with line counts; a literal include is an exact relative path and a glob matches full path or basename, skipping .gitignore/generated trees (use it instead of ls/find through exec); outline(path) returns headings and definitions with line numbers (Markdown sections, Python classes/functions/methods, TS/JS defs and tests, HTML sections/headings inside JS template literals, CSS section comments), so read a long file by its structure instead of guessing. read takes a path or a list and a line range (start/end); page.next is a replayable argument (`{"start": N}`), has_more means the file continues, truncated only means the default page limit cut it, and a range already shown this session returns a short pointer (read(..., repeat=True) re-delivers it). search takes a pattern, one path or a list, include='*.py' to filter, context=N for merged surrounding lines, and ignore_case; its line text is exact, and broad context returns locators (`text_elided`) instead of a silently digested blob. Search is regex by default, so `a|b` is one call; pass regex=False for a literal substring. They return line numbers and handles, so you can go straight to the range you need.

exec(argv, ...) is the one process tool. Use argv for one program, commands=[[...],[...]] for several in order, and shell=<string> when shell syntax is truly needed. cwd=... replaces cd; env/stdin are explicit. Do not pass bash -lc in argv; use shell. For files use read/search, not the shell. exec refuses programs that mutate tracked files (rm, cp, mv, tee, sed -i, git checkout/apply, ...); use delete(path) for one file, mkdir(path) for a directory, and the typed editors for content. For git use the git tool: status, diff/show (paged with offset/max_chars, returning next.offset and has_more), log, files, add, commit, push, remote, branch. It has no pager and returns structured fields.

Every mutation is journalled through write, edit, splice, delete, mkdir or apply. The typed edit takes edits=[{path, old, new}, ...]; the list is atomic, so use one call instead of one edit per file. splice(path, start, end, text) replaces 1-based inclusive lines; apply takes a unified diff; write is for a new file or a real rewrite. Scratch paths (/tmp, /var/tmp, /dev/shm, $TMPDIR) are not gated.

Earlier cells keep their full output in _OBS. The typed recall tool searches it (including turns a fold removed) and returns matching snippets plus the handle to page the full text; search is for the tree, recall is for the session. The typed session_diff tool is the journal view of the current session; git diff is the working tree, and session_diff()/changes()/undo()/commit() remain python helpers.
"""

@dataclass
class FinishState:
    """The staged-then-accepted answer of one run.

    `staged` is set by `final_answer`. If the cell also printed, the answer is held while
    the model reads the output. If the cell was silent, it is still held: the next assistant
    message is the handoff, so a model that calls `final_answer("short")` and then writes the
    real report cannot lose the report. The next reply with no tool call accepts it
    (`accept`), which sets `terminal`; a silent stage prefers that reply's content over the
    tool's short value. A non-final cell in a *later* turn clears it; a later call in the same
    turn does not. A no-tool reply whose goal check rejects the finish clears it too.
    """

    staged: str | None = None
    terminal: str | None = None
    staged_turn: int | None = None
    silent: bool = False
    content: str | None = None

    @property
    def accepted(self) -> bool:
        return self.terminal is not None

    def stage(self, answer: Any, turn: int | None = None, *, silent: bool = False,
              content: str | None = None) -> None:
        self.staged = answer
        self.staged_turn = turn
        self.silent = silent
        self.content = content

    def clear(self, turn: int | None = None) -> None:
        """Drop the staged answer, unless `turn` staged it earlier in this same turn."""
        if turn is not None and self.staged is not None and self.staged_turn == turn:
            return
        self.staged = None
        self.staged_turn = None
        self.silent = False
        self.content = None

    def accept(self, answer: str) -> None:
        self.staged = None
        self.staged_turn = None
        self.silent = False
        self.content = None
        self.terminal = answer


@dataclass
class CallRecord:
    """One tool call of one turn: what ran, what came back, and how it ended."""

    index: int
    name: str
    call_id: str
    code: str | None = None
    args: dict | None = None
    observation: dict | None = None
    runs: list = field(default_factory=list)
    reads: dict | None = None
    notice: str | None = None
    stop: str | None = None
    obs: str | None = None
    check: dict | None = None
    skipped: bool = False

    def as_dict(self) -> dict:
        out = {
            "index": self.index,
            "name": self.name,
            "call_id": self.call_id,
            "code": self.code,
            "args": self.args,
            "observation": self.observation,
            "runs": list(self.runs or []),
            "reads": self.reads or {},
            "notice": self.notice,
            "stop": self.stop,
            "obs": self.obs,
            "check": self.check,
        }
        if self.skipped:
            out["skipped"] = True
        return out


@dataclass
class TurnOutcome:
    """What one turn decided. `end` means the run stops with `stop` and `final`."""

    stop: str | None = None
    final: str | None = None
    end: bool = False


@dataclass
class Tally:
    """The run counters, mutated in place by the turn machine."""

    steps: int = 0
    calls: int = 0
    errors: int = 0
    repeats: int = 0
    llm_calls: int = 0


class ToolLoop:
    # The shared turns policy is parameterised where a second loop (ReplLoop) words it
    # differently; keep the wording here so repl.py owns only its reply format.
    BUDGET_NOTE = ("[runtime] The step budget is exhausted. Reply with a short final "
                   "summary and no tool call.")
    CHECK_NOUN = "project check"
    REJECT_NONE_TAIL = "Keep working."
    REJECT_DETAIL_TAIL = ("Keep working and re-run the check when you believe it passes.")

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
        verifier: Any = None,
    ) -> None:
        self.client = client
        self.kernel = kernel
        self.budget = budget or Budget()
        self.rules = rules
        if tools is None:
            allow_run = bool(getattr(kernel, "allow_run", True))
            tools = [PYTHON_TOOL, *[spec for spec in TYPE_TOOLS
                                    if allow_run or not VERBS[spec["function"]["name"]].get("requires_run")]]
        self.tools = tools
        self.goal = goal
        self.goal_label = goal_label
        self.goal_stops = goal_stops
        self.obs_chars = obs_chars
        self.changed: set[str] = set()
        self.compactions = 0
        self.health = health
        self.verifier = verifier
        self.last_check: Any = None
        self._health_at = 0.0
        self.check_cache: dict[str, str] = {}

    @property
    def _gated(self) -> bool:
        """True when a finish must satisfy a check (a Verifier or a bare goal callable)."""
        return (self.verifier is not None and self.verifier.has_goal) or self.goal is not None

    def _observe(self, cell, steps: int) -> str:
        from .repl import observation

        return observation(cell, self.obs_chars)

    def _compact(self, messages: list[dict]) -> None:
        return None

    def _goal_state(self):
        if self.verifier is not None:
            return self.verifier.authoritative()
        return evaluate(self.goal)

    def _note_cell(self, cell) -> None:
        return None

    def _reject(self, check, prefix: str) -> str:
        if check is None:
            return f"[observation] {prefix}: the {self.CHECK_NOUN} still fails. {self.REJECT_NONE_TAIL}"
        return (f"[observation] {prefix}: the {self.CHECK_NOUN} says the task is not complete.\n"
                f"{check.text()}\n{self.REJECT_DETAIL_TAIL}")

    def _on_run_start(self, messages: list[dict]) -> None:
        """Hook: initialise per-run policy state (ReplLoop's prefix drift, for example)."""
        return None

    def _before_turn(self, messages: list[dict], tally: Tally) -> None:
        """Hook: a per-turn nudge (ReplLoop's wrap-up and last-turn reminders)."""
        return None

    def _drift_count(self) -> int:
        return 0

    def _push(self, messages: list[dict], usage: dict | None = None) -> str:
        msgs = messages + [{
            "role": "user",
            "content": self.BUDGET_NOTE,
        }]
        try:
            resp = self.client.complete(msgs, None, self.budget.max_output_tokens)
        except LLMError:
            return ""
        if usage is not None:
            _usage_add(usage, resp["usage"])
        return (resp.get("content") or "").strip()

    # --- the turn machine --------------------------------------------------------------
    def _turn_record(self, task_id: str, step: int, resp: dict, hit: int, miss: int) -> dict:
        tool_calls = resp.get("tool_calls") or []
        return {
            "task": task_id,
            "turn": step,
            "latency_ms": round(resp["latency_ms"]),
            "content": resp.get("content") or "",
            "reasoning": resp.get("reasoning") or "",
            "tool_calls": [tc.get("function", {}).get("name") for tc in tool_calls],
            "code": None,
            "observation": None,
            "cache": {"hit": hit, "miss": miss},
            "runs": [],
            "finish": resp.get("finish"),
        }

    def _finish_reply(self, messages: list[dict], rec: dict, content: str, finish_reason,
                      finish: FinishState) -> TurnOutcome:
        """A reply with no tool call: continue, reject, or accept the finish."""
        if finish_reason == "length":
            messages.append({"role": "user", "content": "[runtime] Your reply was cut off before it finished. Continue from where it stopped."})
            return TurnOutcome(stop=TRUNCATED)
        text = content.strip()
        if finish.staged is not None:
            if finish.silent:
                candidate = text or finish.content or finish.staged
            else:
                candidate = finish.staged
        else:
            candidate = text
        met, check = self._goal_state()
        if self._gated and not met:
            notice = self._reject(check, "You finished without a passing check")
            messages.append({"role": "user", "content": notice})
            rec["notice"] = notice[:2000]
            finish.clear()
            return TurnOutcome(stop=GOAL_UNMET)
        finish.accept(candidate)
        return TurnOutcome(stop=DONE, final=candidate, end=True)

    def _answer_calls(self, messages: list[dict], rec: dict, tool_calls: list[dict],
                      finish_reason, finish: FinishState, tally: Tally, seen: set[str],
                      step: int) -> TurnOutcome:
        """Dispatch every tool call, pairing exactly one `role: "tool"` message with each."""
        records: list[CallRecord] = []
        if finish.staged is not None and finish.silent and not finish.content:
            finish.content = (rec.get("content") or "").strip() or None
        for index, call in enumerate(tool_calls):
            fn = call.get("function") or {}
            name = fn.get("name") or ""
            call_id = call.get("id") or f"call_{step}_{index}"
            record = CallRecord(index=index, name=name, call_id=call_id)
            records.append(record)
            try:
                if finish.accepted or finish.silent:
                    content = self._skip_call(record)
                else:
                    content = self._dispatch(fn, name, record, finish_reason, finish,
                                             tally, seen, step)
            except Exception as exc:  # noqa: BLE001 - the pairing below must always happen
                tally.errors += 1
                record.notice = (f"[observation] the tool call failed: {exc!r}. "
                                 "Nothing ran.")
                content = record.notice
            messages.append({"role": "tool", "tool_call_id": call_id, "content": content})

        rec["calls"] = [record.as_dict() for record in records]
        self._merge_calls(rec, records)
        if finish.accepted:
            final = (finish.terminal or "").strip()
            stop = VERIFIED if final == self.goal_label else DONE
            return TurnOutcome(stop=stop, final=final, end=True)
        return TurnOutcome(stop=next((r.stop for r in reversed(records) if r.stop), None))

    def _skip_call(self, record: CallRecord) -> str:
        """A call after an accepted/staged `final_answer`: not executed, but still answered."""
        record.skipped = True
        record.notice = ("[observation] skipped: final_answer in this turn is terminal "
                         "intent, so this call did not run; the next no-tool reply is the handoff.")
        return record.notice

    def _dispatch(self, fn: dict, name: str, record: CallRecord, finish_reason,
                  finish: FinishState, tally: Tally, seen: set[str], step: int) -> str:
        """Run one tool call and return the text of its paired tool message."""
        if name != "python" and name not in TOOL_NAMES:
            record.notice = (f"[observation] unknown tool {name!r}; use the typed tools or python. "
                             "help() lists the available verbs.")
            return record.notice
        if finish_reason == "length":
            record.stop = TRUNCATED
            record.notice = "[observation] the call arguments were truncated by the output limit. Nothing ran. Split the work into smaller calls."
            return record.notice
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError as exc:
            record.stop = BAD_ARGS
            record.notice = f"[observation] arguments were not valid JSON: {exc}. Nothing ran."
            return record.notice
        if not isinstance(args, dict):
            record.stop = BAD_ARGS
            record.notice = "[observation] arguments must be a JSON object. Nothing ran."
            return record.notice

        if name == "python":
            code = args.get("code")
            if not isinstance(code, str) or not code.strip():
                record.stop = BAD_ARGS
                record.notice = "[observation] the code argument was empty. Nothing ran."
                return record.notice
            key = code
            record.code = code
        else:
            key = name + ":" + json.dumps(args, sort_keys=True, default=repr)
            record.args = args

        if key in seen:
            tally.repeats += 1
        else:
            seen.add(key)
        tally.calls += 1
        if name == "python":
            cell = self.kernel.run(code, check_cache=self._check_cache())
        else:
            cell = self.kernel.call(name, args, check_cache=self._check_cache())
        record.runs = (record.runs or []) + (cell.runs or [])
        record.reads = cell.reads or {}
        self._note_cell(cell)
        record.observation = {
            "tool": name,
            "exit": "raise" if cell.failed else "error" if cell.rejected else "ok",
            "result": cell.result,
            "stdout": cell.stdout,
            "stderr": cell.stderr,
            "restarted": cell.restarted,
            "rejected": cell.rejected,
            "duration_ms": cell.duration_ms,
        }
        if cell.failed or cell.rejected:
            tally.errors += 1
        git_changed = (name == "git"
                       and str((args or {}).get("action") or "").lower() in ("add", "commit"))
        if cell.mutated or git_changed:
            if self.verifier is not None:
                self.verifier.invalidate()
            else:
                self.check_cache = {}
            self.last_check = None
            self._refresh_health(record, cell)
        if cell.changed:
            self.changed.update(cell.changed)
        obs = self._observe(cell, step)
        record.obs = obs

        if cell.final is not None:
            finish.stage(cell.final, turn=step, silent=not (cell.stdout.strip() or cell.stderr.strip()))
            notice = (obs + f"\n[finish] answer staged: {str(cell.final)[:400]!r}. "
                      "The next reply with no tool call is the handoff (its content wins for "
                      "a silent cell); write the real report there, or call final_answer(...) "
                      "again to change it.")
            record.notice = notice[-2000:]
            record.stop = STAGED
            return notice

        finish.clear(turn=step)
        if self.goal_stops and self._gated and cell.mutated and self._goal_state()[0]:
            finish.accept(self.goal_label)
        return obs

    def _check_cache(self) -> dict:
        return self.verifier.cache() if self.verifier is not None else self.check_cache

    def _refresh_health(self, record: CallRecord, cell) -> None:
        """Refresh the cheap check after a mutation, at most once every 20s."""
        if self.verifier is not None:
            paths = sorted(cell.changed or ())
            if not self.verifier.should_run(paths, time.perf_counter()):
                return
            try:
                self.verifier.run(paths)
            except Exception:  # noqa: BLE001 - a broken health check never stops the run
                self.last_check = None
            else:
                self.last_check = self.verifier.last
            self.check_cache = self.verifier.cache()
            if self.last_check is not None:
                record.check = {"ok": bool(self.last_check.ok), "cmd": self.last_check.cmd}
            return
        if self.health is None:
            return
        now = time.perf_counter()
        if now - self._health_at < 20.0:
            return
        self._health_at = now
        try:
            outcome = self.health(sorted(cell.changed))
        except Exception:  # noqa: BLE001 - a broken health check never stops the run
            outcome = None
        if isinstance(outcome, tuple):
            self.last_check, results = outcome
            self.check_cache = {cmd: {"text": item.text(600), "ok": bool(item.ok)}
                                for cmd, item in results.items() if item is not None}
        else:
            self.last_check = outcome
        if isinstance(self.last_check, bool):
            self.last_check = Check(cmd="health", state="passed" if self.last_check else "failed")
        if self.last_check is not None:
            record.check = {"ok": bool(self.last_check.ok), "cmd": self.last_check.cmd}

    def _merge_calls(self, rec: dict, records: list[CallRecord]) -> None:
        """Mirror the pre-`calls` turn shape: last writer wins per field, runs concatenate."""
        runs: list = []
        for record in records:
            runs.extend(record.runs or [])
        rec["runs"] = runs
        for key in ("code", "observation", "reads", "obs", "check"):
            value = None
            for record in records:
                item = getattr(record, key)
                if item is not None:
                    value = item
            if value is not None:
                rec[key] = value
        # The turn-level notice is the finish protocol's, exactly as before `calls`.
        notices = [r.notice for r in records if r.stop in (STAGED, GOAL_UNMET) and r.notice]
        if notices:
            rec["notice"] = notices[-1]

    def _reply(self, messages: list[dict], rec: dict, resp: dict, finish: FinishState,
               tally: Tally, seen: set[str], step: int) -> TurnOutcome:
        """Turn one reply into an outcome. The one seam ReplLoop overrides."""
        content = resp.get("content") or ""
        tool_calls = resp.get("tool_calls") or []
        finish_reason = resp.get("finish")
        if not tool_calls:
            return self._finish_reply(messages, rec, content, finish_reason, finish)
        messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})
        return self._answer_calls(messages, rec, tool_calls, finish_reason, finish,
                                  tally, seen, step)

    def run(self, task_id: str, prompt: str, trace: Any = None) -> RunResult:
        b = self.budget
        t0 = time.perf_counter()
        messages: list[dict] = []
        if self.rules:
            messages.append({"role": "system", "content": self.rules})
        messages.append({"role": "user", "content": prompt})
        self._on_run_start(messages)

        tally = Tally()
        usage: dict = {}
        turns: list[dict] = []
        final = ""
        stop = STEP_LIMIT
        seen: set[str] = set()
        finish = FinishState()

        try:
            while True:
                self._compact(messages)
                if tally.steps >= b.max_steps or (time.perf_counter() - t0) > b.max_wall_s:
                    tally.llm_calls += 1      # _push is a real client.complete
                    final = self._push(messages, usage)
                    stop = STEP_LIMIT if tally.steps >= b.max_steps else WALL_LIMIT
                    break

                self._before_turn(messages, tally)
                tally.steps += 1
                tally.llm_calls += 1
                resp = self.client.complete(messages, self.tools, b.max_output_tokens)
                _usage_add(usage, resp["usage"])
                cu = resp.get("usage") or {}
                hit = int(cu.get("prompt_cache_hit_tokens") or 0)
                miss = int(cu.get("prompt_cache_miss_tokens") or 0)
                self.last_prompt_tokens = hit + miss
                rec = self._turn_record(task_id, tally.steps, resp, hit, miss)
                outcome = self._reply(messages, rec, resp, finish, tally, seen, tally.steps)

                if outcome.stop is not None:
                    rec["stop"] = outcome.stop
                turns.append(rec)
                if trace:
                    trace.write(rec)
                if outcome.end:
                    final = outcome.final or ""
                    stop = outcome.stop or DONE
                    break
        except LLMError:
            # A primary model call failed. Mirror `_push`: stop rather than escape, and keep
            # whatever answer had already been staged or accepted.
            stop = LLM_FAILED
            final = final or finish.terminal or finish.staged or ""
        finally:
            wall_ms = (time.perf_counter() - t0) * 1000.0
            if trace:
                trace.write({
                    "task": task_id,
                    "summary": True,
                    "stop": stop,
                    "steps": tally.steps,
                    "calls": tally.calls,
                    "llm_calls": tally.llm_calls,
                    "errors": tally.errors,
                    "misses": 0,
                    "repeats": tally.repeats,
                    "drift": self._drift_count(),
                    "usage": usage,
                    "wall_ms": round(wall_ms),
                    "final": final,
                })
                trace.close()

        return RunResult(
            task=task_id,
            stop=stop,
            final=final,
            steps=tally.steps,
            calls=tally.calls,
            errors=tally.errors,
            misses=0,
            repeats=tally.repeats,
            usage=usage,
            wall_ms=wall_ms,
            trace=getattr(trace, "path", None),
            turns=turns,
            llm_calls=tally.llm_calls,
        )
