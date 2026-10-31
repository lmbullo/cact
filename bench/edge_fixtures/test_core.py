import unittest

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
