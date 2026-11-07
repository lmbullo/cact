import unittest

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
