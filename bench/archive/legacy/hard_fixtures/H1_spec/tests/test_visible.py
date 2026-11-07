import unittest
import sys, os
sys.path.insert(0, os.getcwd())
import textkit


class Visible(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(textkit.slugify("Hello, World!"), "hello-world")
        self.assertEqual(textkit.slugify("  A  B  "), "a-b")
        self.assertEqual(textkit.slugify("!!!"), "")


if __name__ == "__main__":
    unittest.main()
