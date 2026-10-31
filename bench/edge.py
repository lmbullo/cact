from __future__ import annotations

import csv
import hashlib
import re
import subprocess
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Callable

EDGE = Path(__file__).parent / "edge_fixtures"

CORE = '''def mean(values):
    return sum(values) / len(values)


def pct(part, whole):
    return part / whole * 100


def slug(text):
    return text
'''

TEST = '''import unittest

from calc.core import mean, pct, slug


class TestCore(unittest.TestCase):
    def test_mean_empty(self):
        self.assertEqual(mean([]), 0)

    def test_pct_rounding(self):
        self.assertEqual(pct(1, 3), 33.33)

    def test_slug(self):
        self.assertEqual(slug("Hello World"), "hello-world")


if __name__ == "__main__":
    unittest.main()
'''

CORE2 = '''def clamp(x, lo, hi):
    return x


def is_even(n):
    return n % 2 == 1


def split_pairs(text):
    return []


def mean(values):
    return sum(values) / len(values)


def pct(part, whole):
    return part / whole * 100


def slug(text):
    return text
'''

TEST2 = '''import unittest

from calc2.core import clamp, is_even, mean, pct, slug, split_pairs


class TestCore(unittest.TestCase):
    def test_clamp_low(self):
        self.assertEqual(clamp(-5, 0, 10), 0)

    def test_clamp_high(self):
        self.assertEqual(clamp(50, 0, 10), 10)

    def test_is_even(self):
        self.assertTrue(is_even(4))
        self.assertFalse(is_even(7))

    def test_split_pairs(self):
        self.assertEqual(split_pairs("a=1,b=2"), [("a", "1"), ("b", "2")])

    def test_mean(self):
        self.assertEqual(mean([2, 4, 6]), 4)

    def test_slug(self):
        self.assertEqual(slug("Hello World"), "hello-world")


if __name__ == "__main__":
    unittest.main()
'''

LEDGER = '''def checksum(order_id):
    h = 0
    for ch in str(order_id):
        h = (h * 31 + ord(ch)) % 1000003
    return h
'''

AMOUNTS = [
    "19.99", "4.25", "128.40", "7.05", "33.33", "88.88", "12.10", "250.00",
    "0.99", "45.67", "61.20", "99.95", "3.14", "500.50", "17.75",
]

DATA_CSV = 'id,note\n1,hello\n2,"line one\nline two"\n3,plain\n4,"comma, inside"\n'


def build() -> None:
    if EDGE.exists():
        return
    (EDGE / "calc").mkdir(parents=True)
    (EDGE / "config").mkdir(parents=True)
    (EDGE / "calc2").mkdir(parents=True)
    (EDGE / "data").mkdir(parents=True)
    (EDGE / "calc" / "__init__.py").write_text("")
    (EDGE / "calc" / "core.py").write_text(CORE)
    (EDGE / "test_core.py").write_text(TEST)
    (EDGE / "calc2" / "__init__.py").write_text("")
    (EDGE / "calc2" / "core.py").write_text(CORE2)
    (EDGE / "test_calc2.py").write_text(TEST2)
    (EDGE / "config" / "__init__.py").write_text("from .tight import LIMIT\n")
    (EDGE / "config" / "base.py").write_text("LIMIT = 40\n")
    (EDGE / "config" / "tight.py").write_text("LIMIT = 12\n")
    (EDGE / "app.py").write_text("from config import LIMIT\n\nprint(LIMIT)\n")
    (EDGE / "ledger.py").write_text(LEDGER)
    lines = ["id,amount"]
    for i, amount in enumerate(AMOUNTS, 1):
        lines.append(f"ord-{i:02d},{amount}")
    (EDGE / "orders.csv").write_text("\n".join(lines) + "\n")
    log: list[str] = []
    for i in range(20_000):
        if i % 1_000 == 0:
            kind = f"EVENT=checkpoint SEQ={i}"
        else:
            kind = "EVENT=tick"
        log.append(f"2025-03-01T{(i // 3600) % 24:02d}:{(i // 60) % 60:02d}:{i % 60:02d}Z {kind} host=node-{(i % 7)} payload=row-{i:06d}")
    (EDGE / "events.log").write_text("\n".join(log) + "\n")
    (EDGE / "data.csv").write_text(DATA_CSV)
    (EDGE / "index.txt").write_text("data/part-03.txt\n")
    (EDGE / "data" / "part-03.txt").write_text("REVISION=4711\nstatus=current\n")
    (EDGE / "legacy.txt").write_bytes(b"alpha\n\xff\xfe broken\nomega\n")


build()


def _orders() -> list[tuple[str, Decimal]]:
    rows = list(csv.DictReader((EDGE / "orders.csv").open(encoding="utf-8")))
    return [(r["id"], Decimal(r["amount"])) for r in rows]


def _checksum(order_id: str) -> int:
    ns: dict = {}
    exec(compile((EDGE / "ledger.py").read_text(encoding="utf-8"), "ledger.py", "exec"), ns)
    return ns["checksum"](order_id)


def _last_checkpoint() -> int:
    last = 0
    for line in (EDGE / "events.log").read_text(encoding="utf-8").splitlines():
        if "EVENT=checkpoint" in line:
            last = int(re.search(r"SEQ=(\d+)", line).group(1))
    return last


def _records() -> int:
    with (EDGE / "data.csv").open(encoding="utf-8", newline="") as fh:
        return sum(1 for _ in csv.reader(fh)) - 1


def _indirect() -> int:
    pointer = (EDGE / "index.txt").read_text(encoding="utf-8").strip()
    text = (EDGE / pointer).read_text(encoding="utf-8")
    return int(re.search(r"REVISION=(\d+)", text).group(1))


def _lines() -> int:
    return len((EDGE / "legacy.txt").read_bytes().decode("utf-8", errors="replace").splitlines())


TOTAL_CENTS = sum(int(amount * 100) for _, amount in _orders())
TOP_ID, TOP_AMOUNT = max(_orders(), key=lambda r: r[1])
TOP_CHECKSUM = _checksum(TOP_ID)
TOP3 = sorted(_orders(), key=lambda r: r[1], reverse=True)[:3]
LIMIT_RUNTIME = int(
    subprocess.run([sys.executable, "app.py"], cwd=EDGE, capture_output=True, text=True).stdout.strip()
)
CHECKPOINT_SEQ = _last_checkpoint()
RECORDS = _records()
REVISION = _indirect()
LEGACY_LINES = _lines()


def _has_int(text: str, n: int) -> bool:
    return re.search(rf"(?<![\d.]){n}(?![\d])", text) is not None


def _suite_ok(ws: Path, module: str, original: str) -> bool:
    probe = subprocess.run(
        [sys.executable, "-m", "unittest", "-q", module],
        cwd=ws, capture_output=True, text=True, timeout=180,
    )
    if probe.returncode != 0:
        return False
    return hashlib.sha256((ws / f"{module}.py").read_bytes()).hexdigest() == hashlib.sha256(original.encode()).hexdigest()


def _suite(ws: Path, module: str, original: str) -> tuple[bool, str]:
    probe = subprocess.run(
        [sys.executable, "-m", "unittest", "-q", module],
        cwd=ws, capture_output=True, text=True, timeout=180,
    )
    if probe.returncode != 0:
        tail = (probe.stderr or probe.stdout).strip().splitlines()
        return False, f"{module} fails: " + (tail[-1][:140] if tail else "no output")
    current = (ws / f"{module}.py").read_bytes()
    if hashlib.sha256(current).hexdigest() != hashlib.sha256(original.encode()).hexdigest():
        return False, "tests were modified"
    return True, "suite passes, tests untouched"


def grade_tests(answer: str, run) -> tuple[bool, str]:
    return _suite(Path(run.workspace), "test_core", TEST)


def grade_tests2(answer: str, run) -> tuple[bool, str]:
    return _suite(Path(run.workspace), "test_calc2", TEST2)


def grade_checkpoint(answer: str, run) -> tuple[bool, str]:
    return _has_int(answer, CHECKPOINT_SEQ), f"expected SEQ {CHECKPOINT_SEQ}; got {answer[:100]!r}"


def grade_limit(answer: str, run) -> tuple[bool, str]:
    numbers = [int(x) for x in re.findall(r"(?<![\d.])\d+(?![\d])", answer or "")]
    ok = bool(numbers) and numbers[0] == LIMIT_RUNTIME
    return ok, f"expected {LIMIT_RUNTIME}; got {answer[:100]!r}"


def grade_cents(answer: str, run) -> tuple[bool, str]:
    return _has_int(answer, TOTAL_CENTS), f"expected {TOTAL_CENTS}; got {answer[:100]!r}"


def grade_join(answer: str, run) -> tuple[bool, str]:
    ok = TOP_ID in answer and _has_int(answer, TOP_CHECKSUM)
    return ok, f"expected {TOP_ID} + {TOP_CHECKSUM}; got {answer[:100]!r}"


def grade_compound(answer: str, run) -> tuple[bool, str]:
    missing = [
        (oid, _checksum(oid)) for oid, amount in TOP3
        if not (oid in answer and _has_int(answer, _checksum(oid)))
    ]
    return not missing, f"missing {missing}; got {answer[:120]!r}"


def grade_records(answer: str, run) -> tuple[bool, str]:
    return _has_int(answer, RECORDS), f"expected {RECORDS} records; got {answer[:100]!r}"


def grade_indirect(answer: str, run) -> tuple[bool, str]:
    return _has_int(answer, REVISION), f"expected {REVISION}; got {answer[:100]!r}"


def grade_bytes(answer: str, run) -> tuple[bool, str]:
    return _has_int(answer, LEGACY_LINES), f"expected {LEGACY_LINES} lines; got {answer[:100]!r}"


@dataclass
class EdgeTask:
    id: str
    prompt: str
    grade: Callable[[str, object], tuple[bool, str]]
    shape: str
    source: Path = field(default=EDGE)
    mutates: bool = False
    check: Callable[[Path], bool] | None = None


EDGE_TASKS: list[EdgeTask] = [
    EdgeTask(
        "E1_tests",
        "The test suite in this project fails. Make it pass. Do not modify the tests; fix the code.",
        grade_tests,
        "multi-turn repair, verification",
        mutates=True,
        check=lambda ws: _suite_ok(ws, "test_core", TEST),
    ),
    EdgeTask(
        "E2_log",
        "events.log is append-only and records periodic checkpoints. Report the SEQ of the most recent checkpoint.",
        grade_checkpoint,
        "large artifact, offset",
    ),
    EdgeTask(
        "E3_shadow",
        "app.py prints the limit it uses. What number does it print?",
        grade_limit,
        "import graph, disambiguation",
    ),
    EdgeTask(
        "E4_cents",
        "Report the total of every amount in orders.csv, expressed as an integer number of cents.",
        grade_cents,
        "exact arithmetic",
    ),
    EdgeTask(
        "E5_join",
        "Which order in orders.csv has the largest amount, and what does ledger.checksum return for its id?",
        grade_join,
        "data + code join",
    ),
    EdgeTask(
        "E6_repair",
        "The test suite for calc2 fails. Make it pass. Do not modify the tests; fix the code.",
        grade_tests2,
        "batch repair, step budget",
        mutates=True,
        check=lambda ws: _suite_ok(ws, "test_calc2", TEST2),
    ),
    EdgeTask(
        "E7_compound",
        "Report the three largest amounts in orders.csv, each with the checksum of its order id.",
        grade_compound,
        "compound answer",
    ),
    EdgeTask(
        "E8_records",
        "How many records does data.csv contain?",
        grade_records,
        "quoted csv, naive vs exact",
    ),
    EdgeTask(
        "E9_indirect",
        "index.txt names the current revision file. Report the revision number stored there.",
        grade_indirect,
        "pointer hop",
    ),
    EdgeTask(
        "E10_bytes",
        "How many lines does legacy.txt contain?",
        grade_bytes,
        "invalid utf-8",
    ),
]


def by_id(task_id: str) -> EdgeTask:
    for task in EDGE_TASKS:
        if task.id == task_id:
            return task
    raise KeyError(task_id)
