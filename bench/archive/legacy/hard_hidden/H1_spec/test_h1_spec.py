import os
import sys
import unittest

sys.path.insert(0, os.getcwd())

import textkit


class Slugify(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(textkit.slugify("Hello, World!"), "hello-world")

    def test_runs(self):
        self.assertEqual(textkit.slugify("  A  B  "), "a-b")

    def test_empty(self):
        self.assertEqual(textkit.slugify("!!!"), "")


class Truncate(unittest.TestCase):
    def test_short_is_unchanged(self):
        self.assertEqual(textkit.truncate("abc", 5), "abc")

    def test_exact_length(self):
        result = textkit.truncate("abcdefghij", 7)
        self.assertEqual(len(result), 7)
        self.assertTrue(result.endswith("..."))

    def test_limit_smaller_than_suffix(self):
        self.assertEqual(textkit.truncate("abcdefghij", 2), "..")
        self.assertEqual(textkit.truncate("abcdefghij", 3), "...")


class ParseDuration(unittest.TestCase):
    def test_single_units(self):
        self.assertEqual(textkit.parse_duration("45s"), 45)
        self.assertEqual(textkit.parse_duration("2m"), 120)
        self.assertEqual(textkit.parse_duration("1h"), 3600)

    def test_combined(self):
        self.assertEqual(textkit.parse_duration("1h30m"), 5400)
        self.assertEqual(textkit.parse_duration("2h15m10s"), 8110)

    def test_malformed(self):
        for bad in ("", "10x", "1h1h", "30m1h"):
            with self.assertRaises(ValueError):
                textkit.parse_duration(bad)


class Chunk(unittest.TestCase):
    def test_shape(self):
        self.assertEqual(textkit.chunk([1, 2, 3, 4, 5], 2), [[1, 2], [3, 4], [5]])
        self.assertEqual(textkit.chunk([], 3), [])

    def test_bad_size(self):
        with self.assertRaises(ValueError):
            textkit.chunk([1], 0)


class WordCount(unittest.TestCase):
    def test_words(self):
        self.assertEqual(textkit.word_count("one two, three!"), 3)
        self.assertEqual(textkit.word_count("-- ..."), 0)


if __name__ == "__main__":
    unittest.main()
