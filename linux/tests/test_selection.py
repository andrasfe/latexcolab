"""Port of SelectionMapperTests.swift."""

import unittest

from latexcolab.core.selection import (offset_of_line, sentence_range,
                                       sentence_range_for_pdf_word,
                                       source_range_for_pdf_text)

PARA = ("Every witness is source-certiﬁed and peer-matched against generated Java under\n"
        "the declared projection~\\cite{smith2020}, including \\emph{byte-exact} final\n"
        "output files (213/213). Post-study local search covers 2,860/3,806 source\n"
        "branch outcomes (75.1\\%); see Fig.~\\ref{fig:x} for details. The study\n"
        "demonstrates feasible evidence.")


def sub(text, rng):
    return None if rng is None else text[rng[0]:rng[0] + rng[1]]


class SelectionMapperTests(unittest.TestCase):
    def test_maps_pdf_selection_with_ligature_hyphenation_and_citation(self):
        pdf = ("source-certified and peer-matched against gen-\nerated Java under the "
               "declared projection [3], including byte-exact final output files (213/213).")
        r = source_range_for_pdf_text(pdf, PARA)
        self.assertEqual(
            sub(PARA, r),
            "source-certiﬁed and peer-matched against generated Java under\n"
            "the declared projection~\\cite{smith2020}, including \\emph{byte-exact} final\n"
            "output files (213/213).")

    def test_selection_starting_inside_emph_is_brace_balanced(self):
        r = source_range_for_pdf_text("byte-exact final output", PARA)
        self.assertEqual(sub(PARA, r), "\\emph{byte-exact} final\noutput")

    def test_rejects_unrelated_text(self):
        self.assertIsNone(source_range_for_pdf_text("completely different words about cats", PARA))
        self.assertIsNone(source_range_for_pdf_text("", PARA))

    def test_sentence_boundaries_respect_abbreviations_math_and_blank_lines(self):
        text = ("First sentence, e.g. with an abbreviation and $x = 3.5$ inside. "
                "Second one here! Third\nstarts here.\n\nNew paragraph sentence.")

        def sentence(needle):
            return sub(text, sentence_range(text, text.index(needle)))

        self.assertEqual(sentence("abbreviation"),
                         "First sentence, e.g. with an abbreviation and $x = 3.5$ inside.")
        self.assertEqual(sentence("Second"), "Second one here!")
        self.assertEqual(sentence("starts"), "Third\nstarts here.")
        self.assertEqual(sentence("New"), "New paragraph sentence.")

    def test_heading_lines_bound_sentences(self):
        text = ("\\section{Introduction}\nThis is the first paragraph. It spans lines.\n"
                "\\label{sec:intro}\nAnother sentence")
        self.assertEqual(sub(text, sentence_range(text, text.index("first"))),
                         "This is the first paragraph.")
        self.assertEqual(sub(text, sentence_range(text, text.index("spans"))),
                         "It spans lines.")
        self.assertEqual(sub(text, sentence_range(text, text.index("Another"))),
                         "Another sentence")
        self.assertEqual(sub(text, sentence_range(text, 3)), "\\section{Introduction}")

    def test_sentence_from_pdf_word_and_line_offset(self):
        offset_line4 = offset_of_line(4, PARA)
        r = sentence_range_for_pdf_word(PARA, "outcomes", offset_line4)
        self.assertEqual(sub(PARA, r),
                         "Post-study local search covers 2,860/3,806 source\n"
                         "branch outcomes (75.1\\%); see Fig.~\\ref{fig:x} for details.")
        last = sentence_range_for_pdf_word(PARA, "demonstrates", len(PARA))
        self.assertEqual(sub(PARA, last), "The study\ndemonstrates feasible evidence.")
        self.assertIsNone(sentence_range_for_pdf_word(PARA, "zzz", 0))

    def test_trailing_punctuation_follows_pdf_text(self):
        self.assertEqual(sub(PARA, source_range_for_pdf_text("feasible evidence.", PARA)),
                         "feasible evidence.")
        self.assertEqual(sub(PARA, source_range_for_pdf_text("feasible evidence", PARA)),
                         "feasible evidence")


if __name__ == "__main__":
    unittest.main()
