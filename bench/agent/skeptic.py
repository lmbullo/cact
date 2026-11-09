"""skeptic: a second, read-only opinion that must falsify a finish before acceptance.

WHAT IT IS
  The model judges its own work poorly: it stops when visible evidence looks green.
  Skeptic.review() spawns a read-only subagent with task, proposed answer, journal diff and last
  check text, and asks one question: name a concrete way this is incomplete. Answer is `PASS` or
  `FAIL: <reason>`. Parent turns FAIL into a repair instruction: distrust becomes a verification
  gate, not another chat turn in the same context.

ENTRY POINTS
  Skeptic(client, workspace, *, max_steps=6, max_total_tokens=60_000)
    .review(prompt, final, diff="", check_text="", changed=()) -> Verdict
  Verdict.ok / .text / .usage   gate decision + what the parent should say

INVARIANTS
  read-only: Subagent can inspect files and the diff, cannot edit or run the goal check. The
  deterministic Verifier stays the authority; the skeptic only looks for a reason it is
  insufficient (wrong file, stale test, unhandled case, claim the diff does not support).
  one review per candidate: loop caches the verdict by digest of (final, diff, changed); a
  rejected finish does not re-bill the same review next turn.
  malformed reply = conservative: no PASS -> FAIL, raw text as reason. Provider failure is not a
  pass: ok=False + transport reason; loop keeps the deterministic verdict.
  usage is returned and added to the parent run: the cost of distrust is visible.

WHERE TO CHANGE
  review question ..... _PROMPT
  decision grammar .... _parse
  child budgets ....... Skeptic.__init__
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from .subagent import Subagent

_PROMPT = """You are the skeptic on a coding-agent finish. Your job is to find one concrete reason the work is not complete, not to praise it. Inspect the files and the diff yourself.

Task:
{prompt}

Proposed answer:
{final}

Files changed this session:
{changed}

Journal diff:
{diff}

Last deterministic check:
{check}

Rules:
- The deterministic check may be incomplete, stale, or only cover the visible tests.
- Check the actual file contents and the call sites; do not trust the answer's summary.
- If you find a real gap, reply exactly `FAIL: <one sentence naming the file and the missing behavior>`.
- If you cannot find a concrete gap after inspecting the evidence, reply exactly `PASS`.
- Do not propose style changes or optional improvements; only correctness or an unmet requirement."""


@dataclass
class Verdict:
    """The skeptic's decision: `ok` gates the finish, `text` is the repair instruction."""

    ok: bool
    text: str
    usage: dict = field(default_factory=dict)
    cached: bool = False


class Skeptic:
    """A read-only reviewer that must falsify a finish before the loop accepts it."""

    def __init__(self, client, workspace: str | Path, *, max_steps: int = 6,
                 max_total_tokens: int = 60_000, max_wall_s: float = 120.0) -> None:
        self.client = client
        self.workspace = Path(workspace).resolve()
        self.max_steps = max_steps
        self.max_total_tokens = max_total_tokens
        self.max_wall_s = max_wall_s
        self._seen: dict[str, Verdict] = {}

    def review(self, prompt: str, final: str, diff: str = "", check_text: str = "",
               changed=()) -> Verdict:
        """Ask the reviewer to falsify `final`; cache by the evidence, never by the turn."""
        key = hashlib.sha256(
            ("\x00".join((prompt, final, diff, check_text, ",".join(sorted(changed))))).encode()
        ).hexdigest()
        if key in self._seen:
            verdict = self._seen[key]
            return Verdict(ok=verdict.ok, text=verdict.text, usage={}, cached=True)
        child = Subagent(self.client, self.workspace, max_steps=self.max_steps,
                         max_total_tokens=self.max_total_tokens, max_wall_s=self.max_wall_s)
        result = child.run(
            "skeptic",
            _PROMPT.format(prompt=prompt[:8000], final=final[:8000],
                           changed=", ".join(sorted(changed)) or "(none)",
                           diff=(diff or "(no diff)")[:12000],
                           check=(check_text or "(no check ran)")[:4000]),
        )
        verdict = self._parse(result.answer, result)
        self._seen[key] = verdict
        return verdict

    def _parse(self, answer: str, result) -> Verdict:
        text = (answer or "").strip()
        if not result.ok:
            return Verdict(False, f"the skeptic could not review the finish ({result.error or result.stop}); repair or rerun the check", result.usage)
        if text.upper().startswith("PASS"):
            return Verdict(True, text, result.usage)
        if text.upper().startswith("FAIL"):
            reason = text.split(":", 1)[1].strip() if ":" in text else text
            return Verdict(False, reason or "the skeptic found an unsupported claim", result.usage)
        return Verdict(False, "the skeptic did not answer PASS: " + text[:600], result.usage)
