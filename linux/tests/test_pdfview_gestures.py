"""Input sequencing in the PDF view.

Selection comes from GtkGestureClick plus a GtkEventControllerMotion, so a
mouse produces press → motion* → release. These tests replay that exact order,
which the headless checks (calling ``_handle_click`` directly) could not cover.
"""

import os
import tempfile
import unittest

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from latexcolab.core import pdfdoc  # noqa: E402
from latexcolab.core.compiler import LaTeXCompiler, find_engine  # noqa: E402
from latexcolab.ui.pdfview import (MODE_PARAGRAPH, MODE_SELECTION,  # noqa: E402
                                   MODE_SENTENCE, PdfView)

TEX = """\\documentclass{article}
\\begin{document}
Every witness is source-certified and peer-matched against generated Java
under the declared projection, including byte-exact final output files.
Post-study local search covers many source branch outcomes.
\\end{document}"""


class PdfViewGestureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not find_engine():
            raise unittest.SkipTest("no LaTeX engine installed")
        if not pdfdoc.poppler_available():
            raise unittest.SkipTest("poppler-utils not installed")
        if not Gtk.init_check():
            raise unittest.SkipTest("no display")
        cls.dir = tempfile.mkdtemp(prefix="latexcolab-gestures-")
        with open(os.path.join(cls.dir, "main.tex"), "w", encoding="utf-8") as fh:
            fh.write(TEX)
        result = LaTeXCompiler(cls.dir, "main.tex").compile()
        if not result.ok:
            raise unittest.SkipTest("compile failed: " + (result.log or "")[-400:])
        cls.doc = pdfdoc.PDFDocument(os.path.join(cls.dir, "main.pdf"))

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(getattr(cls, "dir", ""), ignore_errors=True)

    def setUp(self):
        self.clicks = []
        self.view = PdfView(lambda *a: self.clicks.append(a))
        self.view.canvas.set_document(self.doc)
        self.view.canvas.scale = 1.0
        self.page = self.doc.pages[0]

    # -- helpers -----------------------------------------------------------

    def widget_point(self, word, at_end=False):
        box = word.box
        x = box.x1 - 1 if at_end else box.x0 + 1
        return self.view.canvas.page_to_widget(0, x, box.mid_y)

    def word(self, prefix):
        return next(w for w in self.page.words if w.text.lower().startswith(prefix))

    class _Gesture:
        """Stands in for GtkGestureClick; only the modifier state is consulted."""

        def __init__(self, state):
            self._state = state

        def get_current_event_state(self):
            return self._state

    def press(self, x, y, alt=False):
        state = Gdk.ModifierType.ALT_MASK if alt else 0
        self.view._on_pressed(self._Gesture(state), 1, x, y)

    def motion(self, x, y):
        self.view._on_motion(None, x, y)

    def release(self, x, y, n_press=1):
        self.view._on_released(None, n_press, x, y)

    def drag(self, start, end):
        self.press(*start)
        self.motion(*end)
        self.release(*end)

    # -- tests -------------------------------------------------------------

    def test_plain_click_opens_a_paragraph(self):
        x, y = self.widget_point(self.word("witness"))
        self.press(x, y)
        self.release(x, y)
        self.assertEqual(len(self.clicks), 1)
        self.assertEqual(self.clicks[0][4][0], MODE_PARAGRAPH)

    def test_alt_click_opens_a_sentence(self):
        x, y = self.widget_point(self.word("post-study"))
        self.press(x, y, alt=True)
        self.release(x, y)
        self.assertEqual(len(self.clicks), 1)
        mode = self.clicks[0][4]
        self.assertEqual(mode[0], MODE_SENTENCE)
        self.assertTrue(mode[1].lower().startswith("post-study"), mode[1])

    def test_motion_below_the_threshold_starts_no_selection(self):
        start = self.widget_point(self.word("every"))
        self.press(*start)
        self.motion(start[0] + 2, start[1] + 1)
        self.assertIsNone(self.view.selection)
        self.assertEqual(self.view.canvas.selection_boxes, [])

    def test_drag_builds_a_selection(self):
        self.drag(self.widget_point(self.word("every")),
                  self.widget_point(self.word("projection"), at_end=True))
        self.assertIsNotNone(self.view.selection)
        self.assertIn("witness", self.view.selection[0])
        self.assertGreater(len(self.view.canvas.selection_boxes), 3)
        # A drag is not a click.
        self.assertEqual(self.clicks, [])

    def test_alt_click_inside_a_selection_edits_the_selection(self):
        """The regression: the Alt-click's own press must not wipe the selection
        before the release handler gets to read it."""
        start = self.widget_point(self.word("every"))
        end = self.widget_point(self.word("projection"), at_end=True)
        self.drag(start, end)
        selected = self.view.selection[0]

        middle = ((start[0] + end[0]) / 2, start[1])
        self.press(middle[0], middle[1], alt=True)
        self.assertIsNotNone(self.view.selection,
                             "the press must not clear the selection")
        self.release(*middle)

        self.assertEqual(len(self.clicks), 1)
        mode = self.clicks[0][4]
        self.assertEqual(mode[0], MODE_SELECTION)
        self.assertEqual(mode[1], selected)

    def test_alt_click_outside_a_selection_falls_back_to_the_sentence(self):
        self.drag(self.widget_point(self.word("every")),
                  self.widget_point(self.word("projection"), at_end=True))
        x, y = self.widget_point(self.word("post-study"))
        self.press(x, y, alt=True)
        self.release(x, y)
        self.assertEqual(self.clicks[-1][4][0], MODE_SENTENCE)

    def test_context_menu_edit_selection_after_a_right_click(self):
        start = self.widget_point(self.word("every"))
        end = self.widget_point(self.word("projection"), at_end=True)
        self.drag(start, end)
        self.view._menu_point = ((start[0] + end[0]) / 2, start[1])
        self.view.context_edit(MODE_SELECTION)
        self.assertEqual(self.clicks[-1][4][0], MODE_SELECTION)
        # …and Edit Paragraph… ignores the selection entirely.
        self.view.context_edit(MODE_PARAGRAPH)
        self.assertEqual(self.clicks[-1][4][0], MODE_PARAGRAPH)

    def test_clear_selection_drops_the_highlight(self):
        self.drag(self.widget_point(self.word("every")),
                  self.widget_point(self.word("projection"), at_end=True))
        self.assertIsNotNone(self.view.selection)
        self.view.clear_selection()
        self.assertIsNone(self.view.selection)
        self.assertEqual(self.view.canvas.selection_boxes, [])
        # …and an Alt-click then falls back to the sentence.
        x, y = self.widget_point(self.word("post-study"))
        self.press(x, y, alt=True)
        self.release(x, y)
        self.assertEqual(self.clicks[-1][4][0], MODE_SENTENCE)

    def test_a_click_that_twitches_is_still_a_click(self):
        x, y = self.widget_point(self.word("witness"))
        self.press(x, y)
        self.motion(x + 2, y + 1)      # below the threshold
        self.release(x + 2, y + 1)
        self.assertEqual(len(self.clicks), 1)
        self.assertEqual(self.clicks[0][4][0], MODE_PARAGRAPH)
        self.assertIsNone(self.view.selection)


if __name__ == "__main__":
    unittest.main()
