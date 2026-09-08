"""Port of macos/Tests/LaTeXColabAppTests/PartialApplyTests.swift.

Drives AppModel directly on a scratch project: sentence and selection sessions,
Apply of partial spans, history, and re-opening.
"""

import os
import tempfile
import unittest

from latexcolab.core.config import AppConfig
from latexcolab.core.paragraphs import lines_of, paragraph_containing_line
from latexcolab.core.selection import source_range_for_pdf_text
from latexcolab.core.structure import differences
from latexcolab.model import APPLY_OK, AppModel
from latexcolab.scheduling import DirectScheduler
from latexcolab.session import SCOPE_SELECTION, SCOPE_SENTENCE

TEX = """\\documentclass{article}
\\begin{document}

\\section{Introduction}
This is the first paragraph of the introduction. It spans
multiple source lines so that we can check how \\emph{SyncTeX reports}
line numbers for words in the middle of a paragraph.

This is the second paragraph. It is short.

\\end{document}"""


class PartialApplyTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = self._dir.name
        os.makedirs(os.path.join(self.tmp, "sections"))
        with open(os.path.join(self.tmp, "main.tex"), "w", encoding="utf-8") as fh:
            fh.write(TEX)

    def tearDown(self):
        self._dir.cleanup()

    def source(self) -> str:
        with open(os.path.join(self.tmp, "main.tex"), encoding="utf-8") as fh:
            return fh.read()

    def make_model(self) -> AppModel:
        config = AppConfig({}, path=os.path.join(self.tmp, "config.json"))
        model = AppModel(DirectScheduler(), config)
        model.config.auto_regenerate_after_apply = False
        model.open_project(self.tmp, remember=False)
        return model

    def test_sentence_session_apply_and_history(self):
        model = self.make_model()
        self.assertEqual(model.project_path, os.path.normpath(self.tmp))

        model.open_paragraph("main.tex", 5, scope=SCOPE_SENTENCE)
        s = model.paragraph_session
        self.assertIsNotNone(s)
        self.assertEqual(s.scope, SCOPE_SENTENCE)
        self.assertEqual(s.original, "This is the first paragraph of the introduction.")
        self.assertTrue(s.container.startswith("\\section{Introduction}"))
        self.assertEqual(s.context()[1][:9], " It spans")

        s.draft = "This is the FIRST paragraph of the introduction."
        self.assertEqual(model.apply_paragraph(s), APPLY_OK)
        src = self.source()
        self.assertIn("\\section{Introduction}\nThis is the FIRST paragraph of the "
                      "introduction. It spans\nmultiple", src)
        self.assertEqual([v.text for v in s.history],
                         ["This is the first paragraph of the introduction."])
        self.assertTrue(s.is_in_sync)
        self.assertEqual((s.range.start_line, s.range.end_line), (4, 7))

        # Re-opening the same sentence finds the record (partial) with its history.
        model.paragraph_session = None
        model.open_paragraph("main.tex", 5, scope=SCOPE_SENTENCE)
        again = model.paragraph_session
        self.assertEqual(again.original, "This is the FIRST paragraph of the introduction.")
        self.assertEqual(len(again.history), 1)
        self.assertTrue(again.applied)

        # The whole-paragraph session is separate and unaffected by the partial record.
        model.paragraph_session = None
        model.open_paragraph("main.tex", 5)
        whole = model.paragraph_session
        self.assertEqual(whole.scope, "paragraph")
        self.assertEqual(len(whole.history), 0)
        self.assertTrue(whole.original.startswith("\\section{Introduction}\nThis is the FIRST"))

    def test_selection_session_apply_keeps_braces_and_rest(self):
        model = self.make_model()
        src0 = self.source()
        para = paragraph_containing_line(src0, 6)
        # What a PDF selection of "SyncTeX reports line numbers" maps to.
        span = source_range_for_pdf_text("SyncTeX reports line numbers", para.text)
        self.assertIsNotNone(span)
        self.assertEqual(para.text[span[0]:span[0] + span[1]],
                         "\\emph{SyncTeX reports}\nline numbers")
        model.open_paragraph_session("main.tex", para, span, SCOPE_SELECTION)
        s = model.paragraph_session
        self.assertEqual(s.scope, SCOPE_SELECTION)
        s.draft = "\\emph{SyncTeX records}\nline numbers"
        self.assertEqual(model.apply_paragraph(s), APPLY_OK)
        src = self.source()
        self.assertIn("how \\emph{SyncTeX records}\nline numbers for words", src)
        self.assertIn("This is the second paragraph. It is short.", src)
        self.assertEqual(len(lines_of(src)), len(lines_of(src0)))

        # The structure guard that Apply consults for a partial span.
        self.assertEqual(differences(s.original, "SyncTeX records line numbers"), [])
        self.assertEqual(differences(s.original, "\\emph{SyncTeX records line numbers"),
                         ["leaves 1 more { than } unclosed"])

    def test_structural_change_needs_confirmation(self):
        from latexcolab.model import APPLY_NEEDS_CONFIRMATION

        model = self.make_model()
        model.open_paragraph("main.tex", 9)
        s = model.paragraph_session
        s.draft = s.original + "\n\\end{abstract}"
        self.assertEqual(model.apply_paragraph(s), APPLY_NEEDS_CONFIRMATION)
        self.assertEqual(s.pending_structural, ["adds \\end{abstract}"])
        self.assertNotIn("\\end{abstract}", self.source().replace("\\end{document}", ""))
        # Confirming writes it through.
        self.assertEqual(model.apply_paragraph(s, allow_structural=True), APPLY_OK)
        self.assertIn("\\end{abstract}", self.source())

    def test_drafts_persist_and_reload(self):
        model = self.make_model()
        model.open_paragraph("main.tex", 9)
        s = model.paragraph_session
        s.draft = "This is the SECOND paragraph. It is short."
        model.save_draft_now(s)
        self.assertEqual(model.pending_draft_count, 1)

        reopened = self.make_model()
        reopened.open_paragraph("main.tex", 9)
        again = reopened.paragraph_session
        self.assertEqual(again.draft, "This is the SECOND paragraph. It is short.")
        self.assertFalse(again.is_in_sync)
        self.assertEqual(reopened.pending_draft_count, 1)

        reopened.discard_draft(again)
        self.assertTrue(again.is_in_sync)
        self.assertEqual(reopened.pending_draft_count, 0)

    def test_editor_autosave_and_main_file_detection(self):
        model = self.make_model()
        self.assertEqual(model.main_file, "main.tex")
        self.assertEqual(model.editor_path, "main.tex")
        model.editor_text_changed(TEX + "\n% trailing note\n")
        self.assertTrue(model.editor_dirty)
        model.scheduler.flush()   # fires the 0.8 s autosave
        self.assertFalse(model.editor_dirty)
        self.assertIn("% trailing note", self.source())


if __name__ == "__main__":
    unittest.main()
