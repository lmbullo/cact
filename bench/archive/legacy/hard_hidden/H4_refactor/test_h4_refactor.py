import os
import sys
import unittest

sys.path.insert(0, os.getcwd())

import billing.api as api
from billing import (alpha, bravo, charlie, delta, echo, foxtrot, golf, hotel,
                     india, juliet, kilo, lima, mike, november, oscar, papa)

MODULES = [alpha, bravo, charlie, delta, echo, foxtrot, golf, hotel,
           india, juliet, kilo, lima, mike, november, oscar, papa]
NAMES = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel",
         "india", "juliet", "kilo", "lima", "mike", "november", "oscar", "papa"]


class CallSites(unittest.TestCase):
    def test_every_module(self):
        for module, name in zip(MODULES, NAMES):
            describe = getattr(module, "describe_" + name)
            self.assertEqual(describe("widget,10,USD"), "%s: widget 10 USD" % name)

    def test_padding_reaches_every_module(self):
        for module, name in zip(MODULES, NAMES):
            describe = getattr(module, "describe_" + name)
            self.assertEqual(describe("widget,10"), "%s: widget 10 " % name)


class NewApi(unittest.TestCase):
    def test_parse_row_exists(self):
        self.assertTrue(callable(getattr(api, "parse_row", None)))

    def test_old_name_is_gone(self):
        self.assertFalse(hasattr(api, "parse"))

    def test_missing_fields_are_padded(self):
        self.assertEqual(api.parse_row("a,10"),
                         {"name": "a", "cents": "10", "currency": ""})

    def test_strict_is_keyword_only(self):
        with self.assertRaises(TypeError):
            api.parse_row("a,10,USD", True)

    def test_strict_rejects_short_rows(self):
        with self.assertRaises(ValueError):
            api.parse_row("a,10", strict=True)
        self.assertEqual(api.parse_row("a,10,USD", strict=True)["currency"], "USD")


if __name__ == "__main__":
    unittest.main()
