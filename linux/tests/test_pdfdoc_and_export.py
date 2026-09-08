"""PDF geometry, zip export and config — the pieces macOS gets from PDFKit,
/usr/bin/zip and Foundation."""

import json
import os
import shutil
import tempfile
import unittest
import zipfile

from latexcolab.core import pdfdoc, zipexport
from latexcolab.core.compiler import LaTeXCompiler, find_engine
from latexcolab.core.config import AppConfig

TEX = """\\documentclass{article}
\\begin{document}
\\section{Alpha}
The quick brown fox jumps over the lazy dog near the river bank.

Second paragraph with a distinctive marker word: zebracrossing.
\\end{document}"""


def _needs_tools(test):
    if not find_engine():
        test.skipTest("no LaTeX engine installed")
    if not pdfdoc.poppler_available():
        test.skipTest("poppler-utils not installed")


class PDFDocumentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="latexcolab-pdf-")
        with open(os.path.join(cls.dir, "main.tex"), "w", encoding="utf-8") as fh:
            fh.write(TEX)
        cls.engine = find_engine()
        cls.result = (LaTeXCompiler(cls.dir, "main.tex").compile()
                      if cls.engine else None)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def setUp(self):
        _needs_tools(self)
        self.assertTrue(self.result.ok, (self.result.log or "")[-1500:])
        self.doc = pdfdoc.PDFDocument(os.path.join(self.dir, "main.pdf"))

    def test_pages_and_word_boxes(self):
        self.assertEqual(self.doc.page_count, 1)
        page = self.doc.pages[0]
        self.assertAlmostEqual(page.width, 612, delta=2)
        self.assertAlmostEqual(page.height, 792, delta=2)
        self.assertGreater(len(page.words), 15)
        self.assertGreater(len(page.lines), 3)
        self.assertIn("zebracrossing.", [w.text for w in page.words])

    def test_hit_testing(self):
        page = self.doc.pages[0]
        word = next(w for w in page.words if w.text == "zebracrossing.")
        self.assertEqual(page.word_at(word.box.mid_x, word.box.mid_y).text, word.text)
        self.assertIsNone(page.word_at(5, 5))
        line = page.line_at(word.box.mid_x, word.box.mid_y)
        self.assertIsNotNone(line)
        self.assertIn(word.text, [w.text for w in page.words_of_line(line)])

    def test_band_text_and_selection_order(self):
        page = self.doc.pages[0]
        word = next(w for w in page.words if w.text == "zebracrossing.")
        self.assertIn("zebracrossing", page.text_in_band(word.box.mid_y))
        first = next(w for w in page.words if w.text == "The")
        last = next(w for w in page.words if w.text == "bank.")
        picked = page.words_between((first.box.x0, first.box.mid_y),
                                    (last.box.x1, last.box.mid_y))
        text = " ".join(w.text for w in picked)
        self.assertTrue(text.startswith("The quick brown fox"), text)
        self.assertTrue(text.endswith("bank."), text)
        # Reversed drag gives the same span.
        reverse = page.words_between((last.box.x1, last.box.mid_y),
                                     (first.box.x0, first.box.mid_y))
        self.assertEqual([w.text for w in reverse], [w.text for w in picked])

    def test_render_png(self):
        data = pdfdoc.render_png(self.doc.path, 0, 96)
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"), data[:8])
        self.assertGreater(len(data), 4000)


class ZipExportTests(unittest.TestCase):
    def test_export_skips_git_and_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = os.path.join(tmp, "paper")
            os.makedirs(os.path.join(project, ".git"))
            os.makedirs(os.path.join(project, "sections"))
            for name in ("main.tex", "main.aux", "main.synctex.gz", "main.pdf",
                         ".git/config", "sections/intro.tex", ".DS_Store"):
                with open(os.path.join(project, name), "w", encoding="utf-8") as fh:
                    fh.write("x")
            destination = os.path.join(tmp, "out.zip")
            zipexport.export(project, destination)
            with zipfile.ZipFile(destination) as zf:
                names = sorted(zf.namelist())
            self.assertIn("paper/main.tex", names)
            self.assertIn("paper/sections/intro.tex", names)
            self.assertIn("paper/main.pdf", names)
            self.assertFalse([n for n in names if ".git/" in n], names)
            self.assertFalse([n for n in names if n.endswith(".aux")], names)
            self.assertFalse([n for n in names if n.endswith(".synctex.gz")], names)
            self.assertFalse([n for n in names if n.endswith(".DS_Store")], names)

    def test_pure_python_fallback_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = os.path.join(tmp, "paper")
            os.makedirs(os.path.join(project, ".git"))
            for name in ("main.tex", "main.log", ".git/config"):
                with open(os.path.join(project, name), "w", encoding="utf-8") as fh:
                    fh.write("x")
            destination = os.path.join(tmp, "out.zip")
            zipexport._export_with_zipfile(project, destination, "paper")
            with zipfile.ZipFile(destination) as zf:
                self.assertEqual(zf.namelist(), ["paper/main.tex"])


class ConfigTests(unittest.TestCase):
    def test_round_trip_preserves_unknown_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"last_project": "/tmp/x", "web_only_setting": 42}, fh)
            cfg = AppConfig.load(path)
            self.assertEqual(cfg.last_project, "/tmp/x")
            self.assertEqual(cfg.temperature, 0.2)
            self.assertEqual(cfg.default_max_words, 20)
            self.assertTrue(cfg.auto_regenerate_after_apply)
            self.assertEqual(cfg.model, "")
            cfg.model = "qwen"
            cfg.temperature = 0.5
            cfg.set_main_file("paper.tex", "/tmp/x")
            cfg.save()

            again = AppConfig.load(path)
            self.assertEqual(again.raw["web_only_setting"], 42)
            self.assertEqual(again.model, "qwen")
            self.assertEqual(again.temperature, 0.5)
            self.assertEqual(again.main_file("/tmp/x"), "paper.tex")
            self.assertIsNone(again.main_file("/tmp/other"))
            self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")

    def test_missing_file_gives_defaults(self):
        cfg = AppConfig.load("/nonexistent/latexcolab/config.json")
        self.assertIsNone(cfg.last_project)
        self.assertEqual(cfg.lmstudio_url, "http://127.0.0.1:1234")
        self.assertEqual(cfg.latex_engine, "")


if __name__ == "__main__":
    unittest.main()
