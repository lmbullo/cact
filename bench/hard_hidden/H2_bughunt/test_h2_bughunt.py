import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.getcwd())

from ledger import dates, money, stats, tax, text


class Tax(unittest.TestCase):
    def test_rate(self):
        self.assertEqual(tax.with_tax(1000, 1250), 1125)

    def test_half_up(self):
        self.assertEqual(tax.with_tax(1, 5000), 2)

    def test_zero(self):
        self.assertEqual(tax.with_tax(1000, 0), 1000)


class Dates(unittest.TestCase):
    def test_forward(self):
        self.assertEqual(dates.days_between(date(2024, 1, 1), date(2024, 1, 10)), 9)

    def test_absolute(self):
        self.assertEqual(dates.days_between(date(2024, 1, 10), date(2024, 1, 1)), 9)

    def test_same(self):
        self.assertEqual(dates.days_between(date(2024, 1, 1), date(2024, 1, 1)), 0)


class Money(unittest.TestCase):
    def test_remainder_is_kept(self):
        parts = money.split(10, 3)
        self.assertEqual(sum(parts), 10)
        self.assertEqual(sorted(parts), [3, 3, 4])

    def test_even(self):
        self.assertEqual(money.split(9, 3), [3, 3, 3])

    def test_bad_n(self):
        with self.assertRaises(ValueError):
            money.split(10, 0)


class Text(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(text.normalize("  Hello,   World!! "), "hello world")
        self.assertEqual(text.normalize("A--B"), "a b")


class Stats(unittest.TestCase):
    def test_even(self):
        self.assertEqual(stats.median([1, 2, 3, 4]), 2.5)

    def test_odd(self):
        self.assertEqual(stats.median([5]), 5)
        self.assertEqual(stats.median([3, 1, 2]), 2)

    def test_empty(self):
        with self.assertRaises(ValueError):
            stats.median([])


if __name__ == "__main__":
    unittest.main()
