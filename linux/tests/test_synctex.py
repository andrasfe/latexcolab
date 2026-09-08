"""Port of SyncTeXTests.swift, with the PDFKit end-to-end check redone on poppler."""

import gzip
import os
import tempfile
import unittest

from latexcolab.core import pdfdoc
from latexcolab.core.compiler import LaTeXCompiler, find_engine
from latexcolab.core.paragraphs import paragraph_containing_line
from latexcolab.core.synctex import SyncTeXScanner, locate_file_for_pdf, read_text_file

SP = 65781.76


class SyncTeXTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = self._dir.name
        with open(os.path.join(self.tmp, "main.tex"), "w", encoding="utf-8") as fh:
            fh.write("line1\nline2\n")
        os.makedirs(os.path.join(self.tmp, "sections"))
        with open(os.path.join(self.tmp, "sections", "body.tex"), "w", encoding="utf-8") as fh:
            fh.write("body\n")

    def tearDown(self):
        self._dir.cleanup()

    @property
    def sample(self) -> str:
        """Two line boxes on page 1: one from main.tex line 11 (with a glue record on
        line 9), one from sections/body.tex line 3."""
        return f"""SyncTeX Version:1
Input:1:./main.tex
Input:2:{self.tmp}/./sections/body
Output:pdf
Magnification:1000
Unit:1
X Offset:0
Y Offset:0
Content:
!631
{{1
[1,16:4736286,46220574:26673152,41484288,0
(1,11:8799518,19318411:22609920,455111,127431
g1,9:9000000,19318411
x1,10:20000000,19318411
)
(2,3:8799518,23844943:22609920,655359,183500
g2,3:8799518,23844943
)
]
}}1
Postamble:
Count:3"""

    def test_parses_inputs_and_boxes(self):
        s = SyncTeXScanner(self.sample, self.tmp)
        self.assertEqual(s.inputs[1], "./main.tex")
        self.assertEqual(len(s.boxes), 3)
        self.assertEqual(len(s.points), 3)
        self.assertAlmostEqual(s.boxes[1].x, 8799518 / SP, places=3)
        self.assertEqual(s.boxes[1].nesting, 1)
        self.assertEqual(os.path.basename(s.file_for_tag(1)), "main.tex")
        self.assertEqual(os.path.basename(s.file_for_tag(2)), "body.tex")

    def test_edit_query_picks_nearest_point_record(self):
        s = SyncTeXScanner(self.sample, self.tmp)
        y = 19318411 / SP
        near9 = s.edit_query(1, 9_100_000 / SP, y)
        self.assertEqual(near9.line, 9)
        self.assertEqual(os.path.basename(near9.path), "main.tex")
        near10 = s.edit_query(1, 19_000_000 / SP, y)
        self.assertEqual(near10.line, 10)

    def test_edit_query_other_file_and_tolerance(self):
        s = SyncTeXScanner(self.sample, self.tmp)
        y = 23844943 / SP
        hit = s.edit_query(1, 200, y + 5)
        self.assertEqual(hit.line, 3)
        self.assertEqual(os.path.basename(hit.path), "body.tex")
        self.assertIsNone(s.edit_query(1, 200, y + 200))
        self.assertIsNone(s.edit_query(2, 200, y))

    def test_display_query_finds_lines_for_source_range(self):
        s = SyncTeXScanner(self.sample, self.tmp)
        # main.tex lines 9–10 carry point records inside the first line box.
        hit = s.display_query(os.path.join(self.tmp, "main.tex"), 8, 10)
        self.assertIsNotNone(hit)
        page, rect = hit
        self.assertEqual(page, 1)
        self.assertAlmostEqual(rect.min_x, 8799518 / SP, places=2)
        self.assertAlmostEqual(rect.max_y, (19318411 + 127431) / SP, places=2)
        # body.tex line 3 via the "blank line after" extension (2…2 → 2…3)
        body = s.display_query(os.path.join(self.tmp, "sections", "body.tex"), 2, 2)
        self.assertIsNotNone(body)
        self.assertEqual(body[0], 1)
        self.assertIsNone(s.display_query(os.path.join(self.tmp, "main.tex"), 40, 45))
        self.assertIsNone(s.display_query(os.path.join(self.tmp, "nope.tex"), 1, 5))

    def test_gzip_round_trip(self):
        plain = os.path.join(self.tmp, "sample.synctex")
        with open(plain, "w", encoding="utf-8") as fh:
            fh.write(self.sample)
        with open(plain, "rb") as src, gzip.open(plain + ".gz", "wb") as dst:
            dst.write(src.read())
        self.assertEqual(read_text_file(plain + ".gz"), self.sample)
        self.assertEqual(os.path.basename(locate_file_for_pdf(
            os.path.join(self.tmp, "sample.pdf"))), "sample.synctex.gz")


class RealCompileTests(unittest.TestCase):
    """End-to-end with a real engine when one is installed (skipped otherwise)."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = self._dir.name

    def tearDown(self):
        self._dir.cleanup()

    def test_real_compile_and_query(self):
        engine = find_engine()
        if not engine:
            self.skipTest("no LaTeX engine installed")
        if not pdfdoc.poppler_available():
            self.skipTest("poppler-utils not installed")
        tex = """\\documentclass{article}
\\begin{document}

\\section{Introduction}
This is the first paragraph of the introduction. It spans
multiple source lines so that we can check how SyncTeX reports
line numbers for words in the middle of a paragraph.

This is the second paragraph. It is short.

\\end{document}"""
        with open(os.path.join(self.tmp, "main.tex"), "w", encoding="utf-8") as fh:
            fh.write(tex)
        result = LaTeXCompiler(self.tmp, "main.tex").compile()
        self.assertTrue(result.ok, f"engine {engine}\n" + result.log[-2000:])
        sync_file = locate_file_for_pdf(os.path.join(self.tmp, "main.pdf"))
        self.assertIsNotNone(sync_file)
        scanner = SyncTeXScanner.from_file(sync_file, self.tmp)
        doc = pdfdoc.PDFDocument(os.path.join(self.tmp, "main.pdf"))

        def paragraph_for_text(needle: str) -> str:
            """Locate words with poppler, then map through SyncTeX like the app does."""
            first = needle.split()[0].lower().strip(".,")
            for page in doc.pages:
                for i, w in enumerate(page.words):
                    if w.text.lower().strip(".,") != first:
                        continue
                    box = w.box
                    for extra in page.words[i + 1:i + len(needle.split())]:
                        box = box.union(extra.box)
                    loc = scanner.edit_query(page.index + 1, box.mid_x, box.mid_y)
                    if loc is None:
                        continue
                    self.assertEqual(os.path.basename(loc.path), "main.tex")
                    with open(loc.path, encoding="utf-8") as fh:
                        source = fh.read()
                    para = paragraph_containing_line(source, loc.line)
                    self.assertIsNotNone(para)
                    return para.text
            self.fail(f"'{needle}' not found in the PDF")

        self.assertEqual(paragraph_for_text("second paragraph"),
                         "This is the second paragraph. It is short.")
        self.assertTrue(paragraph_for_text("line numbers").endswith(
            "line numbers for words in the middle of a paragraph."))
        self.assertTrue(paragraph_for_text("first paragraph").startswith(
            "\\section{Introduction}"))


if __name__ == "__main__":
    unittest.main()
