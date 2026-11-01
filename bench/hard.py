"""The hard suite (H1-H4): tasks where the envelope has to earn its keep.

WHAT IT IS
  Four workspaces that punish a loop that only runs the visible tests:
    H1_spec      SPEC.md is the contract; the visible suite is green but incomplete.
    H2_bughunt   ten modules with latent bugs; SPEC.md states the invariants.
    H3_bigcsv    200k rows; four aggregates that require code, not reading.
    H4_refactor  rename an API across sixteen call-site modules; the old name must disappear.

  H1/H2/H4 are graded by a held-out unittest tree under `bench/hard_hidden/`. The agent can
  see pass/fail and the failing test names (that is the `[check]` line when health is on),
  but it cannot read or edit the tests. H3 is a text answer graded exactly.

ENTRY POINTS
  HardTask(id, prompt, grade, shape, source, mutates, check)
  HARD_TASKS / by_id(task_id)
  build()                      create `bench/hard_fixtures/` (idempotent; H3 CSV is large)
  hidden_check(task_id, ws)    -> Check from the held-out tests, the goal and the health plan
  grade_*                      the H3 text grader

INVARIANTS
  - Hidden tests live outside the workspace and are executed by `bench/hard_runner.py` with
    cwd=the workspace; the runner prints `FAILED test: message` lines so check.py parses them.
  - H1's visible suite passes on the broken workspace on purpose. A loop that trusts green
    tests and never reads SPEC.md fails.
  - H3's ground truth is computed from the generated CSV at import; the CSV itself is
    generated (gitignored), never committed.
  - build() only writes missing files, so an edited workspace is never silently reset.
"""
from __future__ import annotations

import os
import random
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .agent.check import Check, run_check

HERE = Path(__file__).parent
HARD = HERE / "hard_fixtures"
HIDDEN = HERE / "hard_hidden"
RUNNER = HERE / "hard_runner.py"


def _write(path: Path, text: str) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# --- H1: a spec the visible tests do not cover -------------------------------------------
H1_SPEC = '''# textkit contract

`slugify(text)` -> lowercase; every run of characters outside [a-z0-9] becomes one `-`;
leading and trailing `-` are removed. Input with no alphanumerics -> `""`.

`truncate(text, limit, suffix="...")` -> `text` unchanged when `len(text) <= limit`.
Otherwise the result ends with `suffix` and has length exactly `limit` when
`limit >= len(suffix)`. When `limit < len(suffix)`, return `suffix[:limit]`.

`parse_duration(text)` -> integer seconds. Accepts one or more `<n><unit>` components where
unit is `h`, `m` or `s`, e.g. `45s`, `2m`, `1h30m`, `2h15m10s`. Each component may appear at
most once and the units must descend h, m, s when combined. Empty or malformed input raises
`ValueError`.

`chunk(items, size)` -> a list of lists, in order, each of length `size` except the last.
Empty input -> `[]`. `size <= 0` raises `ValueError`.

`word_count(text)` -> the number of whitespace-separated tokens that contain at least one
alphanumeric character. Punctuation-only tokens are not words.
'''

H1_INIT = '''from .strings import slugify, truncate, parse_duration, chunk, word_count

__all__ = ["slugify", "truncate", "parse_duration", "chunk", "word_count"]
'''

H1_STRINGS = '''import re


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")


def truncate(text, limit, suffix="..."):
    text = str(text)
    if len(text) <= limit:
        return text
    return text[:limit] + suffix


def parse_duration(text):
    match = re.fullmatch(r"(\\d+)([hms])", str(text).strip())
    if not match:
        raise ValueError("bad duration: %r" % (text,))
    scale = {"h": 3600, "m": 60, "s": 1}[match.group(2)]
    return int(match.group(1)) * scale


def chunk(items, size):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def word_count(text):
    return len(str(text).split())
'''

H1_VISIBLE = '''import unittest
import sys, os
sys.path.insert(0, os.getcwd())
import textkit


class Visible(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(textkit.slugify("Hello, World!"), "hello-world")
        self.assertEqual(textkit.slugify("  A  B  "), "a-b")
        self.assertEqual(textkit.slugify("!!!"), "")


if __name__ == "__main__":
    unittest.main()
'''

H1_PROMPT = (
    "SPEC.md is the contract for the textkit package. The visible tests pass but cover "
    "only part of it. Implement every behavior described in SPEC.md. Do not modify the tests."
)


# --- H2: ten latent bugs behind a green visible suite ------------------------------------
H2_SPEC = '''# ledger invariants

`tax.with_tax(cents, rate_bp)` -> `cents` plus tax, where `rate_bp` is basis points
(10000 bp = 100%). The tax is rounded half up: `(cents * rate_bp + 5000) // 10000`.

`dates.days_between(a, b)` -> the absolute number of calendar days between two `date`
objects. `days_between(d, d) == 0`.

`money.split(cents, n)` -> a list of `n` ints whose sum is `cents`; the first `cents % n`
entries are one cent larger than the rest. `n <= 0` raises `ValueError`.

`text.normalize(s)` -> lowercase; every run of characters outside `[a-z0-9]` becomes one
space; leading and trailing spaces are removed.

`stats.median(xs)` -> the middle value when the length is odd, the mean of the two middle
values (a float) when even. Empty input raises `ValueError`.

`sequence.unique(xs)` -> the distinct values in first-seen order (not sorted, not arbitrary).

`parse.ints(text)` -> the integer tokens in `text`, in order; tokens that are not integers
are ignored.

`window.chunks(seq, n)` -> every chunk of length `n`, in order, last one possibly short.
`n <= 0` raises `ValueError`.

`names.ext(name)` -> the extension after the last dot, or `""` when there is no dot.

`rounding.round_half_up(x)` -> the nearest integer, halves going up: `floor(x + 0.5)`.
'''

H2_FILES = {
    "ledger/__init__.py": '''from . import dates, money, stats, tax, text
''',
    "ledger/tax.py": '''def with_tax(cents, rate_bp):
    return cents + cents * rate_bp // 100
''',
    "ledger/dates.py": '''def days_between(a, b):
    return (b - a).days + 1
''',
    "ledger/money.py": '''def split(cents, n):
    return [cents // n] * n
''',
    "ledger/text.py": '''def normalize(s):
    return str(s).lower()
''',
    "ledger/stats.py": '''def median(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2]
''',
    "ledger/sequence.py": '''def unique(xs):
    return list(set(xs))
''',
    "ledger/parse.py": '''def ints(text):
    return [int(token) for token in str(text).split()]
''',
    "ledger/window.py": '''def chunks(seq, n):
    return [seq[i:i + n] for i in range(n)]
''',
    "ledger/names.py": '''def ext(name):
    return str(name).split(".")[-1]
''',
    "ledger/rounding.py": '''def round_half_up(x):
    return int(x)
''',
    "tests/test_visible.py": '''import unittest
import sys, os
sys.path.insert(0, os.getcwd())
from ledger.money import split


class Visible(unittest.TestCase):
    def test_split_even(self):
        self.assertEqual(split(9, 3), [3, 3, 3])


if __name__ == "__main__":
    unittest.main()
''',
}

H2_PROMPT = (
    "SPEC.md states the invariants for the ledger package. The visible tests are incomplete. "
    "Find the latent bugs and make every invariant in SPEC.md true. Do not modify the tests."
)


# --- H4: rename an API across every call site ---------------------------------------------
H4_SPEC = '''# billing API

`billing.api.parse_row(text, *, strict=False)` splits one row on commas into
`{"name": str, "cents": str, "currency": str}`.

- Non-strict: missing trailing fields become `""`.
- Strict: fewer than three fields raises `ValueError`.
- The old name `parse` no longer exists, and the old `amount` field is now `cents`.

Every module in `billing/` must call `parse_row` and read the `cents` field.
'''

H4_API = '''def parse(text):
    parts = text.split(",")
    fields = [part.strip() for part in parts]
    while len(fields) < 3:
        fields.append("")
    return {"name": fields[0], "amount": fields[1], "currency": fields[2]}
'''

H4_MODULE = '''from .api import parse


def describe_{name}(text):
    row = parse(text)
    return "{name}: %s %s %s" % (row["name"], row["amount"], row["currency"])
'''

H4_MODULES = [
    "alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel",
    "india", "juliet", "kilo", "lima", "mike", "november", "oscar", "papa",
]

H4_VISIBLE = '''import unittest
import sys, os
sys.path.insert(0, os.getcwd())
from billing.alpha import describe_alpha


class Visible(unittest.TestCase):
    def test_alpha(self):
        self.assertEqual(describe_alpha("widget,10,USD"), "alpha: widget 10 USD")


if __name__ == "__main__":
    unittest.main()
'''

H4_PROMPT = (
    "SPEC.md describes the new billing API. Rename `billing.api.parse` to `parse_row`, rename "
    "the `amount` field to `cents`, make `strict` keyword-only with default False (strict "
    "raises ValueError when fewer than three comma-separated fields are present), update every "
    "call site in the package, and delete the old name. Do not modify the tests."
)


def _h1(root: Path) -> None:
    _write(root / "SPEC.md", H1_SPEC)
    _write(root / "textkit" / "__init__.py", H1_INIT)
    _write(root / "textkit" / "strings.py", H1_STRINGS)
    _write(root / "tests" / "test_visible.py", H1_VISIBLE)


def _h2(root: Path) -> None:
    _write(root / "SPEC.md", H2_SPEC)
    for rel, body in H2_FILES.items():
        _write(root / rel, body)


def _h4(root: Path) -> None:
    _write(root / "SPEC.md", H4_SPEC)
    _write(root / "billing" / "__init__.py", "")
    _write(root / "billing" / "api.py", H4_API)
    for name in H4_MODULES:
        _write(root / "billing" / (name + ".py"), H4_MODULE.format(name=name))
    _write(root / "tests" / "test_visible.py", H4_VISIBLE)


H3_ROWS = 200_000
H3_SEED = 20260101
H3_REGIONS = ["North", "South", "East", "West", "Central"]
H3_STATUS = ["paid", "paid", "paid", "refund", "void"]


def _h3(root: Path) -> Path:
    path = root / "sales.csv"
    if path.exists():
        return path
    root.mkdir(parents=True, exist_ok=True)
    rng = random.Random(H3_SEED)
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write("id,region,rep,amount_cents,status\n")
        for row_id in range(H3_ROWS):
            region = H3_REGIONS[rng.randrange(len(H3_REGIONS))]
            rep = "r%02d" % rng.randrange(40)
            amount = rng.randrange(100, 500_000)
            status = H3_STATUS[rng.randrange(len(H3_STATUS))]
            fh.write(f"{row_id},{region},{rep},{amount},{status}\n")
    return path


H3_PROMPT = (
    "sales.csv has {rows} rows. Report four values in one ANSWER line, separated by `|`:\n"
    "1. the sum of amount_cents for rows whose status is 'refund'\n"
    "2. the region with the largest total amount_cents over rows with status 'paid'\n"
    "3. the number of distinct rep values\n"
    "4. the number of rows whose id is divisible by 997"
).format(rows=H3_ROWS)


def _h3_truth(path: Path) -> dict:
    import csv
    refund = 0
    paid: dict[str, int] = {}
    reps: set[str] = set()
    mod = 0
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            amount = int(row["amount_cents"])
            reps.add(row["rep"])
            if row["status"] == "refund":
                refund += amount
            elif row["status"] == "paid":
                paid[row["region"]] = paid.get(row["region"], 0) + amount
            if int(row["id"]) % 997 == 0:
                mod += 1
    region = max(paid, key=lambda key: (paid[key], key))
    return {"refund": refund, "region": region, "reps": len(reps), "mod": mod}


def build() -> None:
    for task_id, writer in (("H1_spec", _h1), ("H2_bughunt", _h2), ("H4_refactor", _h4)):
        writer(HARD / task_id)
    _h3(HARD / "H3_bigcsv")


def hidden_check(task_id: str, ws: Path) -> Check:
    hidden = HIDDEN / task_id
    cmd = f"{sys.executable} {RUNNER} {hidden}"
    return run_check(cmd, str(ws), timeout=120.0)


def _grade_hidden(task_id: str) -> Callable[[str, object], tuple[bool, str]]:
    def grade(answer: str, run) -> tuple[bool, str]:
        ws = Path(getattr(run, "workspace", ""))
        if not str(ws) or not ws.exists():
            return False, "no workspace to grade"
        check = hidden_check(task_id, ws)
        return check.ok, check.text(1200)
    return grade


def grade_bigcsv(answer: str, run) -> tuple[bool, str]:
    text = answer or ""
    match = re.search(r"(?im)^\s*ANSWER:\s*(.+?)\s*$", text)
    body = match.group(1) if match else text
    numbers = [int(item) for item in re.findall(r"(?<![\d.])-?\d+(?![\d.])", body)]
    ok = (
        len(numbers) >= 3
        and numbers[0] == H3_TRUTH["refund"]
        and numbers[1] == H3_TRUTH["reps"]
        and numbers[2] == H3_TRUTH["mod"]
        and H3_TRUTH["region"].lower() in body.lower()
    )
    return ok, (
        f"expected refund={H3_TRUTH['refund']} region={H3_TRUTH['region']} "
        f"reps={H3_TRUTH['reps']} mod={H3_TRUTH['mod']}; got {body[:200]!r}"
    )


@dataclass
class HardTask:
    id: str
    prompt: str
    grade: Callable[[str, object], tuple[bool, str]]
    shape: str
    source: Path
    mutates: bool = False
    check: Callable[[Path], Check] | None = None


build()
H3_TRUTH = _h3_truth(HARD / "H3_bigcsv" / "sales.csv")


HARD_TASKS: list[HardTask] = [
    HardTask(
        "H1_spec",
        H1_PROMPT,
        _grade_hidden("H1_spec"),
        "contract over stale tests",
        HARD / "H1_spec",
        mutates=True,
        check=lambda ws: hidden_check("H1_spec", ws),
    ),
    HardTask(
        "H2_bughunt",
        H2_PROMPT,
        _grade_hidden("H2_bughunt"),
        "ten latent bugs, one spec",
        HARD / "H2_bughunt",
        mutates=True,
        check=lambda ws: hidden_check("H2_bughunt", ws),
    ),
    HardTask(
        "H3_bigcsv",
        H3_PROMPT,
        grade_bigcsv,
        "200k rows, four aggregates",
        HARD / "H3_bigcsv",
    ),
    HardTask(
        "H4_refactor",
        H4_PROMPT,
        _grade_hidden("H4_refactor"),
        "rename across sixteen modules",
        HARD / "H4_refactor",
        mutates=True,
        check=lambda ws: hidden_check("H4_refactor", ws),
    ),
]


def by_id(task_id: str) -> HardTask:
    for task in HARD_TASKS:
        if task.id == task_id:
            return task
    raise KeyError(task_id)
