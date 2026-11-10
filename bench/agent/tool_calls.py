"""tool_calls: ToolCalls: one turn's tool calls, paired with their results and routed.
WHAT IT IS
  The dispatch half of ToolLoop, as a mixin: _answer_calls pairs each reply tool_call with one
  role:"tool" message, _dispatch validates (unknown name, scope, truncated/malformed arguments,
  green gate, foreign exec gate) and routes python/code to Kernel.run, plan/task/delegate/docs
  to parent helpers, typed names to Kernel.call; cell text returns via tool_turns.py.

ENTRY POINTS
  ToolCalls  mixin base of tool_loop.ToolLoop
  _answer_calls(messages, rec, tool_calls, finish_reason, finish, tally, seen, step)
  _dispatch(fn, name, record, finish_reason, finish, tally, seen, step) -> str;  _skip_call(record)

INVARIANTS
  exactly one role:"tool" per tool_calls entry, even when dispatch raises, so no assistant call
  is orphaned; BAD_ARGS/TRUNCATED mean nothing ran and the model is told why.
  a call after an accepted/staged final_answer is skipped; a mutating cell drops the verifier
  cache and last check, then refreshes the cheap check; a reported change clears the gate.
  a call outside limits.Scope or an unknown name is BAD_ARGS with the names allowed; a typed
  call's refusal is the cell's, with its error.use.

WHERE TO CHANGE
  routing / pairing ................. _dispatch; _answer_calls, _skip_call
  what a call may touch ............. limits.Scope; green_gate.GreenGate
  typed tool names and schemas ...... verbs.VERBS (tools.py generates the specs)
"""

from __future__ import annotations

import json

from .call_shape import parse_args, repair
from .finish_flow import after_cell, report_cell
from .green_gate import FOREIGN_GATE_NOTE, plan_command
from .records import CallRecord, FinishState, Tally, TurnOutcome
from .stops import BAD_ARGS, DONE, TRUNCATED, VERIFIED
from .tools import TOOL_NAMES


class ToolCalls:
    """One turn's calls: validated, routed to the kernel or a parent-side helper, and paired."""

    def _answer_calls(self, messages: list[dict], rec: dict, tool_calls: list[dict],
                      finish_reason, finish: FinishState, tally: Tally, seen: set[str],
                      step: int) -> TurnOutcome:
        """Dispatch every tool call, pairing exactly one `role: "tool"` message with each."""
        self._surprise = False
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
                elif index >= self.scope.max_calls_per_turn:
                    record.skipped = True
                    record.notice = ("[observation] skipped: this task allows at most %d tool "
                                     "calls per turn; split the work across turns."
                                     % self.scope.max_calls_per_turn)
                    content = record.notice
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
        rec["card_chars"] = sum(len(record.obs or "") for record in records)
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
        if name not in ("code", "python") and name not in TOOL_NAMES \
                and name not in self.extra_names and name not in ("delegate", "docs", "plan", "task"):
            record.notice = (f"[observation] unknown tool {name!r}; use the typed tools or python. "
                             "help() lists the available verbs.")
            return record.notice
        if not self.scope.allows(name):
            tally.errors += 1
            record.stop = BAD_ARGS
            record.notice = ("[observation] tool %r is outside this task's scope; allowed: %s"
                             % (name, ", ".join(sorted(self.scope.tools))))
            return record.notice
        if finish_reason == "length":
            record.stop = TRUNCATED
            record.notice = "[observation] the call arguments were truncated by the output limit. Nothing ran. Split the work into smaller calls."
            return record.notice
        try:
            args, json_note = parse_args(fn.get("arguments"))
        except (json.JSONDecodeError, ValueError) as exc:
            record.stop = BAD_ARGS
            record.notice = f"[observation] arguments were not valid JSON: {exc}. Nothing ran."
            return record.notice

        shape = repair(name, args, self.scope.tools)
        if shape.changed:
            name, args = shape.name, shape.args
            record.name = name
            record.args = args
        shape_note = "".join(note for note in (
            f"[shape] {json_note}\n" if json_note else "",
            f"[shape] {shape.note}\n" if shape.note else "") if note)

        if name == "exec" and self.verifier is not None:
            spec = plan_command(args, getattr(self.verifier, "specs", ()) or ())
            if spec is not None:
                name, args = "checks", {"name": spec.name}
                record.name, record.args = name, args
                shape_note += "[shape] exec(plan command) ran as checks(%r)\n" % spec.name

        refusal = self.gate.refusal(name)
        if refusal is not None:
            record.stop, record.notice = refusal
            return record.notice
        if name == "exec" and self.verifier is not None and self._is_foreign_gate(args):
            record.notice = FOREIGN_GATE_NOTE
            return record.notice
        if name == "delegate":
            return self._delegate(args, record, tally)
        if name == "docs":
            return self._docs(args, record, tally)

        if name in ("code", "python"):
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
        if name == "plan":
            return self._plan(args, record)
        if name == "task":
            return self._task(record)
        if name == "checks":
            cell = self._checks_cell(args)
        elif name in ("code", "python"):
            cell = self.kernel.run(code, check_cache=self._check_cache(),
                                   extra_obs=self._take_extra_obs())
        else:
            cell = self.kernel.call(name, args, check_cache=self._check_cache(),
                                    extra_obs=self._take_extra_obs())
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
        self._surprise = self._surprise or bool(cell.failed or cell.rejected or cell.restarted)
        git_changed = (name == "git"
                       and str((args or {}).get("action") or "").lower() in ("add", "commit"))
        if cell.mutated or git_changed:
            if self.verifier is not None:
                self.verifier.invalidate()
            else:
                self.check_cache = {}
            self.last_check = None
            self.last_check_handle = None
            self._refresh_health(record, cell)
        if cell.changed:
            self.changed.update(cell.changed)
            self.state.changed.update(cell.changed)
            self.state.phase = "act"
            self.gate.invalidate()
        if name == "checks" and self.changed:
            self.gate.note_checks(cell)
        if self.last_check is not None:
            self.state.checks = [{"name": self.last_check.cmd, "ok": bool(self.last_check.ok)}]
            self.state.phase = "verify"
        obs = self._observe(cell, step) + self.gate.suffix(name)
        record.obs = obs

        if cell.final is not None:
            return report_cell(self, obs, cell, record, finish, step)

        after_cell(self, cell, finish, step)
        return shape_note + obs
