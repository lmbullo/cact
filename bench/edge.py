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


