import unittest
import sys, os
sys.path.insert(0, os.getcwd())
from ledger.money import split


class Visible(unittest.TestCase):
    def test_split_even(self):
        self.assertEqual(split(9, 3), [3, 3, 3])


if __name__ == "__main__":
    unittest.main()
