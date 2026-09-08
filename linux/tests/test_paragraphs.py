"""Port of ParagraphLocatorTests.swift and FileTreeTests."""

import os
import tempfile
import unittest

from latexcolab.core import filetree
from latexcolab.core.paragraphs import (lines_of, locate, paragraph_containing_line,
                                        paragraphs, replacing)

SOURCE = """\\documentclass{article}
\\begin{document}

\\section{Intro}
First paragraph line one
line two.

Second paragraph.

\\end{document}"""


class ParagraphLocatorTests(unittest.TestCase):
    def test_paragraph_containing_line(self):
        p = paragraph_containing_line(SOURCE, 5)
        self.assertEqual(p.start_line, 4)
        self.assertEqual(p.end_line, 6)
        self.assertEqual(p.text, "\\section{Intro}\nFirst paragraph line one\nline two.")

    def test_blank_line_prefers_paragraph_above(self):
        # SyncTeX reports a paragraph's boxes on the blank line that ends it.
        p = paragraph_containing_line(SOURCE, 7)
        self.assertEqual((p.start_line, p.end_line), (4, 6))

    def test_leading_blank_line_falls_forward(self):
        p = paragraph_containing_line("\n\nHello\nworld", 1)
        self.assertEqual(p.start_line, 3)
        self.assertEqual(p.text, "Hello\nworld")

    def test_all_paragraphs(self):
        ps = paragraphs(SOURCE)
        self.assertEqual([p.start_line for p in ps], [1, 4, 8, 10])
        self.assertEqual(ps[2].text, "Second paragraph.")

    def test_replacing_changes_line_count(self):
        p = paragraph_containing_line(SOURCE, 8)
        out = replacing(p, SOURCE, "Second\nparagraph\nrewritten.")
        self.assertIn("Second\nparagraph\nrewritten.\n\n\\end{document}", out)
        self.assertEqual(len(lines_of(out)), len(lines_of(SOURCE)) + 2)

    def test_locate_after_shift(self):
        p = paragraph_containing_line(SOURCE, 8)
        shifted = "% new comment\n\n" + SOURCE
        found = locate(p.text, p, shifted)
        self.assertEqual(found.start_line, 10)
        self.assertIsNone(locate("not there", p, shifted))


class FileTreeTests(unittest.TestCase):
    def test_build_excludes_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, ".git"))
            os.makedirs(os.path.join(tmp, "sections"))
            for f in ["main.tex", "main.aux", "main.synctex.gz", "main.pdf",
                      ".DS_Store", "sections/intro.tex"]:
                with open(os.path.join(tmp, f), "w", encoding="utf-8") as fh:
                    fh.write("x")
            tree = filetree.build(tmp)
            self.assertEqual([n.name for n in tree], ["sections", "main.pdf", "main.tex"])
            self.assertEqual([n.id for n in filetree.flatten_files(tree)],
                             ["sections/intro.tex", "main.pdf", "main.tex"])
            self.assertEqual(filetree.find("sections/intro.tex", tree).name, "intro.tex")


if __name__ == "__main__":
    unittest.main()
