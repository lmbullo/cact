import unittest

from pricing import shipping


class Visible(unittest.TestCase):
    def test_shipping_base(self):
        self.assertEqual(shipping(500, "north"), 400)

    def test_shipping_south_and_weight(self):
        self.assertEqual(shipping(700, "south"), 552)

    def test_shipping_rejects_unknown_zone(self):
        with self.assertRaises(ValueError):
            shipping(500, "east")


if __name__ == "__main__":
    unittest.main()
