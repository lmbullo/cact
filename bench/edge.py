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


