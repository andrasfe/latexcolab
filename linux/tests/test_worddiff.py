"""Port of macos/Tests/LaTeXColabCoreTests/WordDiffTests.swift."""

import unittest

from latexcolab.core.worddiff import DiffKind, changed_word_count, diff, highlight_ranges


def sub(s: str, ranges):
    return [s[loc:loc + length] for loc, length in ranges]


class WordDiffTests(unittest.TestCase):
    def test_identical(self):
        segs = diff("The cat sat.", "The cat sat.")
        self.assertTrue(all(s.kind is DiffKind.EQUAL for s in segs))
        self.assertEqual(changed_word_count(segs), 0)

    def test_substitution_counts_once(self):
        segs = diff("The cat sat on the mat.", "The dog sat on the rug.")
        self.assertEqual(changed_word_count(segs), 2)
        old, new = highlight_ranges(segs)
        self.assertEqual(len(new), 2)
        self.assertEqual(len(old), 2)
        self.assertEqual(sub("The dog sat on the rug.", new), ["dog", "rug"])
        self.assertEqual(sub("The cat sat on the mat.", old), ["cat", "mat"])

    def test_whitespace_reflow_is_not_a_change(self):
        self.assertEqual(changed_word_count(diff("one two\nthree four", "one two three\nfour")), 0)

    def test_punctuation_and_commands_are_not_words(self):
        segs = diff("Hello world", "Hello, \\emph{world}!")
        self.assertEqual(changed_word_count(segs), 0)
        self.assertTrue(any(s.kind is DiffKind.INSERTED and s.text == "\\emph" for s in segs))

    def test_insertion_and_deletion(self):
        self.assertEqual(changed_word_count(diff("a b c", "a b c d e")), 2)
        self.assertEqual(changed_word_count(diff("a b c d e", "a e")), 3)
        self.assertEqual(changed_word_count(diff("", "x y")), 2)


if __name__ == "__main__":
    unittest.main()
