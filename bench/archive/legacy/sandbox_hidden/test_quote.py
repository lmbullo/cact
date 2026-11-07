import unittest

from pricing import quote


class QuoteContract(unittest.TestCase):
    def test_north_charge(self):
        self.assertEqual(quote(100, "north"), 400)

    def test_south_charge(self):
        self.assertEqual(quote(100, "south"), 550)

    def test_west_charge(self):
        self.assertEqual(quote(100, "west"), 675)

    def test_free_shipping_at_threshold(self):
        self.assertEqual(quote(4500, "west"), 0)

    def test_free_shipping_above_threshold(self):
        self.assertEqual(quote(9000, "south"), 0)

    def test_unknown_zone(self):
        with self.assertRaises(ValueError):
            quote(100, "east")


if __name__ == "__main__":
    unittest.main()
