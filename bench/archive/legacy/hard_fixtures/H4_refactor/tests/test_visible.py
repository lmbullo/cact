import unittest
import sys, os
sys.path.insert(0, os.getcwd())
from billing.alpha import describe_alpha


class Visible(unittest.TestCase):
    def test_alpha(self):
        self.assertEqual(describe_alpha("widget,10,USD"), "alpha: widget 10 USD")


if __name__ == "__main__":
    unittest.main()
