"""tool_subtools: ToolSubTools: plan, task, delegate, docs and the exec-gate probes.
WHAT IT IS
  The parent-side tools of ToolLoop, as a mixin; none reaches the kernel: plan/task read and write
  the durable RunState, delegate runs one read-only child, docs fetches one page.
ENTRY POINTS
  ToolSubTools  mixin base of tool_loop.ToolLoop
  _plan(args, record) -> str;  _task(record) -> str;  _delegate(args, record, tally) -> str
  _docs(args, record, tally) -> str;  _is_foreign_gate(args), _is_plan_command(args) -> bool
INVARIANTS
  no kernel and no shell: an unusable argument or a disabled tool is a BAD_ARGS [observation]
  line, never an exception, never a spawn.
  a child's usage is folded into the run (_sub_usage), so the token budget cannot be evaded by
  delegating; a failed child counts as an error and never ends the run.
  the plan lines are the card's lines: state.plan_lines() is the single renderer.
  the probes read the verifier's frozen specs, not the plan file.
WHERE TO CHANGE
  plan and task text ............... _plan, _task (state.RunState holds the values)
  the child agent .................. _delegate (subagent.DELEGATE_TOOL is its spec)
  document fetch ................... _docs (docs.DOCS_TOOL is its spec)
  the exec gate probes ............. _is_foreign_gate, _is_plan_command
"""

from __future__ import annotations

from .green_gate import is_foreign_gate, is_plan_command
from .loop import _usage_add
from .records import CallRecord, Tally
from .stops import BAD_ARGS


class ToolSubTools:
    """plan/task/delegate/docs and the exec-gate probes; mixed into ToolLoop."""

    def _is_foreign_gate(self, args: dict) -> bool:
        """True when `exec` runs a lint/typecheck/format gate the plan does not contain."""
        specs = list(getattr(self.verifier, "specs", ()) or ()) if self.verifier is not None else []
        return is_foreign_gate(args, specs)

    def _is_plan_command(self, args: dict) -> bool:
        """True when an `exec` argument is exactly one of the check plan's commands."""
        specs = list(getattr(self.verifier, "specs", ()) or ()) if self.verifier is not None else []
        return is_plan_command(args, specs)

    def _plan(self, args: dict, record: CallRecord) -> str:
        """Set or mark the durable plan; return the same `[plan]` lines the card renders."""
        items = args.get("items")
        done = args.get("done")
        if items is not None and not isinstance(items, list):
            record.stop = BAD_ARGS
            return "[observation] plan(items=[...]) needs a list of strings. Nothing changed."
        if done is not None and not isinstance(done, list):
            record.stop = BAD_ARGS
            return "[observation] plan(done=[...]) needs a list of 1-based indices. Nothing changed."
        if items is not None:
            self.state.set_plan(items, done)
            self.state.phase = "plan"
        elif done is not None:
            self.state.mark(done)
        lines = self.state.plan_lines()
        text = "\n".join(lines) if lines else "[plan] empty; pass items=[...] to set one"
        record.observation = {"tool": "plan", "ok": True, **self.state.summary()}
        return text

    def _task(self, record: CallRecord) -> str:
        """Return the durable task/phase view; no kernel and no mutation."""
        summary = self.state.summary()
        lines = ["[task] phase=%s changed=%d checks=%d open=%d" % (
            self.state.phase, len(self.state.changed), len(self.state.checks),
            len(self.state.open))]
        lines.extend(self.state.plan_lines())
        record.observation = {"tool": "task", **summary}
        return "\n".join(lines)

    def _delegate(self, args: dict, record: CallRecord, tally: Tally) -> str:
        """Run the read-only child and return one compact observation; add its usage."""
        if self.subagent is None:
            record.stop = BAD_ARGS
            return "[observation] delegation is not enabled for this run."
        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            record.stop = BAD_ARGS
            return "[observation] delegate(prompt=...) needs the question to answer. Nothing ran."
        task = str(args.get("task") or "delegate")
        tally.calls += 1
        result = self.subagent.run(task, prompt)
        _usage_add(self._sub_usage, result.usage)
        text = result.as_text()
        record.observation = {"tool": "delegate", "stop": result.stop,
                              "steps": result.steps, "usage": result.usage}
        record.notice = text[:2000]
        if not result.ok:
            tally.errors += 1
        return text

    def _docs(self, args: dict, record: CallRecord, tally: Tally) -> str:
        """Fetch one documentation page as a parent-side tool; no kernel and no shell."""
        if self.docs is None:
            record.stop = BAD_ARGS
            return "[observation] docs() is not enabled for this run."
        url = str(args.get("url") or "").strip()
        if not url:
            record.stop = BAD_ARGS
            return "[observation] docs(url=...) needs a URL. Nothing ran."
        tally.calls += 1
        value = self.docs.fetch(url, int(args.get("max_chars") or 12_000))
        record.observation = {"tool": "docs", **{k: v for k, v in value.items()
                                                  if k in ("ok", "url", "title", "chars",
                                                           "total_chars", "truncated", "error")}}
        if not value.get("ok"):
            tally.errors += 1
            text = "[observation] docs failed: " + str((value.get("error") or {}).get("message"))
            record.notice = text[:2000]
            return text
        body = str(value.get("text") or "")
        text = (f"[docs] {value.get('title') or url} ({value.get('url')})\n"
                f"{body}\n[page] chars={value.get('chars')}/{value.get('total_chars')} "
                f"truncated={value.get('truncated')}; pass max_chars for more.")
        record.notice = text[:2000]
        return text
