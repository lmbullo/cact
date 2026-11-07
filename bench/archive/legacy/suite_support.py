"""The two primitives every benchmark module borrows: a tolerant number match and a gap-filling write.

WHAT IT IS
  Shared by the suite modules (`tasks.py`, `edge.py`, `hard.py`, `sandbox.py`) so the two
  behaviours below exist once:
  - `has_int(text, n)` decides whether an answer contains the integer `n` as a standalone
    number, so prose around it and punctuation beside it do not matter, while a longer number
    that merely contains `n` is never a match.
  - `write_once(path, text)` fills a fixture gap. The builders for `hard_fixtures/` and
    `sandbox_fixtures/` are idempotent: an existing file is graded input and must never be
    overwritten by a re-import.

ENTRY POINTS
  has_int(text, n) -> bool      a standalone integer, no digit or dot on either side
  write_once(path, text)        create `path` (and parents) when missing; leave it alone otherwise

INVARIANTS
  - Standard library only, no fixture knowledge: this module must stay importable from any
    suite module without creating a cycle, so it imports nothing from `bench`.
  - `write_once` is deliberately all-or-nothing per file: it never truncates, appends or
    rewrites, because a rebuilt-but-edited fixture would silently change the ground truth the
    graders compute at import time.
  - Tolerance changes belong here, not in a grader: one edit then moves every numeric suite.

WHERE TO CHANGE
  Answer tolerance ......... has_int()
  Fixture rebuild policy ... write_once()
  A suite-local behaviour .. the suite module (tasks.py / edge.py / hard.py / sandbox.py)
"""
from __future__ import annotations

import re
from pathlib import Path


def has_int(text: str, n: int) -> bool:
    """True when `n` appears as a standalone integer (no digit or dot on either side)."""
    return re.search(rf"(?<![\d.]){n}(?![\d])", text) is not None


def write_once(path: Path, text: str) -> None:
    """Create `path` and its parents only when the file is missing; an existing file is kept."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
