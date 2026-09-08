"""Port of StructureTests.swift (structure, engine picking, log parsing)."""

import unittest

from latexcolab.core import compiler
from latexcolab.core.structure import differences


class StructureTests(unittest.TestCase):
    def test_differences_detect_added_end(self):
        original = "Producing synthetic data is a challenge. Our prior paper \\emph{X} did Y."
        self.assertEqual(differences(original, original + "\n\\end{abstract}"),
                         ["adds \\end{abstract}"])
        self.assertEqual(differences(original, original.replace("challenge", "problem")), [])

    def test_differences_braces_sectioning_math(self):
        self.assertEqual(differences("a \\textbf{b} c", "a \\textbf{b c"),
                         ["leaves 1 more { than } unclosed"])
        self.assertEqual(differences("a b", "\\section{New} a b"),
                         ["adds a sectioning command"])
        self.assertEqual(differences("$x$ and $y$", "$x$ and y$"),
                         ["changes the number of $ math delimiters to an odd count"])
        self.assertEqual(differences("\\begin{itemize}\\item a\\end{itemize}",
                                     "\\begin{itemize}\\item a"),
                         ["removes \\end{itemize}"])
        # Comments and escaped braces are ignored.
        self.assertEqual(differences("50\\% done % \\end{x}", "50\\% done"), [])


class EngineTests(unittest.TestCase):
    def test_engine_preference_across_directories(self):
        dirs = ["/opt/texlive/2024/bin/x86_64-linux", "/home/me/.TinyTeX/bin/x86_64-linux"]
        existing = {
            "/opt/texlive/2024/bin/x86_64-linux/tectonic",
            "/home/me/.TinyTeX/bin/x86_64-linux/latexmk",
            "/home/me/.TinyTeX/bin/x86_64-linux/pdflatex",
        }
        self.assertEqual(compiler.pick(dirs, exists=lambda p: p in existing),
                         "/home/me/.TinyTeX/bin/x86_64-linux/latexmk")
        self.assertEqual(compiler.pick(dirs, ["tectonic"], exists=lambda p: p in existing),
                         "/opt/texlive/2024/bin/x86_64-linux/tectonic")
        self.assertIsNone(compiler.pick(dirs, ["lualatex"], exists=lambda p: p in existing))

    def test_first_error_extraction(self):
        first = compiler.MissingFileDetector.first_error
        self.assertEqual(
            first("blah\nmain.tex:61: LaTeX Error: \\begin{document} ended by "
                  "\\end{abstract}.\nmore"),
            "main.tex:61: LaTeX Error: \\begin{document} ended by \\end{abstract}.")
        self.assertEqual(first("! Undefined control sequence.\nl.5 \\foo"),
                         "Undefined control sequence.")
        self.assertEqual(first("error: main.tex:61: LaTeX Error: x"),
                         "main.tex:61: LaTeX Error: x")
        self.assertIsNone(first("Output written on main.pdf"))
        # Multi-pass log: the error of the last run wins.
        two = ("! LaTeX Error: File `microtype.sty' not found.\n--- retrying compile ---\n"
               "main.tex:61: LaTeX Error: \\begin{document} ended by \\end{abstract}.\n")
        self.assertEqual(first(two),
                         "main.tex:61: LaTeX Error: \\begin{document} ended by "
                         "\\end{abstract}.")

    def test_tlmgr_and_environment_failure_detection(self):
        refusal = ("tlmgr: Local TeX Live (2025) is older than remote repository (2026).\n"
                   "Cross release updates are only supported with\n"
                   "  update-tlmgr-latest(.sh/.exe) --update\n"
                   "tlmgr: Terminating; please see warning above!")
        detector = compiler.MissingFileDetector
        self.assertTrue(detector.tlmgr_needs_self_update(refusal))
        self.assertTrue(detector.tlmgr_failed(refusal))
        self.assertFalse(detector.tlmgr_needs_self_update(
            "tlmgr: package repository https://x\n[1/1] install: microtype [50k]\n"
            "running mktexlsr ...\ndone."))
        self.assertTrue(compiler.is_environment_failure(
            "! LaTeX Error: File `microtype.sty' not found."))
        self.assertTrue(compiler.is_environment_failure(
            "! Fatal Package fontspec Error: The fontspec package requires either "
            "XeTeX or LuaTeX."))
        self.assertFalse(compiler.is_environment_failure(
            "main.tex:61: LaTeX Error: \\begin{document} ended by \\end{abstract}."))
        self.assertFalse(compiler.is_environment_failure("! Missing $ inserted."))

    def test_missing_files_and_rerun(self):
        detector = compiler.MissingFileDetector
        self.assertEqual(
            detector.missing_files("! LaTeX Error: File `microtype.sty' not found.\n"
                                   "! I can't find file `ecrm1000'."),
            ["microtype.sty", "ecrm1000.tfm"])
        self.assertTrue(detector.needs_rerun(
            "LaTeX Warning: Label(s) may have changed. Rerun to get cross-references right."))
        self.assertFalse(detector.needs_rerun("Output written on main.pdf"))


if __name__ == "__main__":
    unittest.main()
