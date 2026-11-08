"""state: the durable working set the model sees in every card.

WHAT IT IS
  `RunState` is the part of a run that survives a long transcript: the task, the environment
  card, the plan, the changed paths, the last checks, and the open items. The loop owns one
  instance; `plan` updates it, every observation renders it, and a fold carries it forward.
  This is the first state-first block: it replaces "remember what the earlier turns said"
  with a small structured object the harness owns.

ENTRY POINTS
  RunState(task="", env="")      .task / .env / .plan / .changed / .checks / .evidence / .open
    .set_plan(items, done=None)  replace the plan, optionally marking 1-based indices done
    .mark(done)                  mark 1-based indices done without replacing the text
    .plan_lines() -> list[str]   the `[plan]` lines; empty when no plan is set
    .summary() -> dict           the trace-shaped view (plan text + done flags)
  plan_rows(items) -> list[PlanItem]   strings or {text, done} dicts -> plan items

INVARIANTS
  - The state is data, not prose. `plan_lines` is the only renderer; the state card and the
    fold both call it, so the plan cannot drift between the two.
  - `set_plan` replaces the text; `mark` only flips done flags. An index outside 1..len is
    ignored, never guessed.
  - `plan_lines` returns `[]` for an empty plan, so a card from a task that never planned is
    not padded with a reminder.
  - No I/O: the loop fills changed/checks from `Cell` records; this module does not read files.

WHERE TO CHANGE
  What the plan row carries ......... PlanItem, plan_rows
  What the card renders ............. plan_lines
  What the trace records ............ summary
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PlanItem:
    """One durable plan row: its text and whether the model marked it done."""

    text: str
    done: bool = False


@dataclass
class RunState:
    """The durable task state: plan, changed paths, checks, evidence and open items."""

    task: str = ""
    env: str = ""
    phase: str = "orient"
    plan: list[PlanItem] = field(default_factory=list)
    changed: set[str] = field(default_factory=set)
    checks: list[dict] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    open: list[str] = field(default_factory=list)

    def set_plan(self, items: list, done: list | None = None) -> None:
        """Replace the plan from strings/dicts, then apply optional 1-based `done` indices."""
        self.plan = plan_rows(items)
        if done:
            self.mark(done)

    def mark(self, done: list) -> None:
        """Mark 1-based plan indices done; an out-of-range or non-integer index is ignored."""
        for raw in done or ():
            try:
                index = int(raw) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= index < len(self.plan):
                self.plan[index].done = True

    def phase_line(self) -> str:
        """The one `[phase]` line: orient, plan, act, verify or report."""
        return "[phase] " + self.phase

    def plan_lines(self) -> list[str]:
        """The `[plan]` lines for the card; empty when no plan is set."""
        if not self.plan:
            return []
        lines = ["[plan]"]
        for index, item in enumerate(self.plan, 1):
            lines.append("%d. [%s] %s" % (index, "x" if item.done else " ", item.text))
        return lines

    def summary(self) -> dict:
        """The trace-shaped plan: text plus done flags, never a rendered card."""
        return {"phase": self.phase,
                "plan": [{"text": item.text, "done": item.done} for item in self.plan],
                "changed": sorted(self.changed), "checks": list(self.checks),
                "evidence": list(self.evidence), "open": list(self.open)}


def plan_rows(items: list) -> list[PlanItem]:
    """Strings or `{text, done}` dicts -> plan items; empty/invalid rows are dropped."""
    out: list[PlanItem] = []
    for item in items or ():
        if isinstance(item, dict):
            text = str(item.get("text") or item.get("item") or "").strip()
            if text:
                out.append(PlanItem(text=text, done=bool(item.get("done"))))
        else:
            text = str(item).strip()
            if text:
                out.append(PlanItem(text=text))
    return out
