"""Port of EditBudgetTests.swift and the budget half of StructureTests.swift."""

import unittest

from latexcolab.core.budget import EditSuggestion, apply_suggestion, constrain, measure

ORIGINAL = ("In this paper we shows that the proposed method , which is described in "
            "\\cref{sec:method} , outperform the baseline by a large margin "
            "( see \\cite{smith2020} ) .")


class EditBudgetTests(unittest.TestCase):
    def test_free_fixes_always_kept(self):
        rewrite = ("In this paper we show that the proposed method, which is described in "
                   "\\cref{sec:method}, outperforms the baseline by a large margin "
                   "(see \\cite{smith2020}).")
        r = constrain(ORIGINAL, rewrite, 0)
        self.assertEqual(r.text, rewrite)
        self.assertEqual(r.dropped, [])
        self.assertEqual(r.words_used, 0)
        self.assertGreater(r.free_fixes, 0)

    def test_rewordings_beyond_budget_are_reverted(self):
        rewrite = ("In this paper we demonstrate that the proposed approach, which is "
                   "described in \\cref{sec:method}, outperforms the baseline by a large "
                   "margin (see \\cite{smith2020}).")
        one = constrain(ORIGINAL, rewrite, 1)
        self.assertEqual(one.words_used, 1)
        self.assertEqual(len(one.dropped), 1)
        self.assertIn("described in \\cref{sec:method}, outperforms", one.text)
        self.assertNotEqual("demonstrate" in one.text, "approach" in one.text)

        zero = constrain(ORIGINAL, rewrite, 0)
        self.assertEqual(zero.words_used, 0)
        # "method ," → "approach," entangles the space fix with the rewording, so the
        # whole hunk is reverted; the independent punctuation fixes are still applied.
        self.assertIn("we shows that the proposed method , which", zero.text)
        self.assertIn("\\cref{sec:method}, outperforms the baseline by a large margin "
                      "(see \\cite{smith2020}).", zero.text)
        self.assertEqual([s.label for s in zero.dropped],
                         ["“shows” → “demonstrate”", "“method” → “approach”"])
        self.assertEqual([s.cost for s in zero.dropped], [1, 1])

        allowed = constrain(ORIGINAL, rewrite, 5)
        self.assertEqual(allowed.text, rewrite)
        self.assertEqual(allowed.words_used, 2)

    def test_whole_sentence_insertion_counts_every_word(self):
        rewrite = ORIGINAL + " This is an entirely new sentence added by the model."
        r = constrain(ORIGINAL, rewrite, 5)
        self.assertEqual(r.text, ORIGINAL)
        self.assertEqual(len(r.dropped), 1)
        self.assertEqual(r.dropped[0].cost, 10)
        self.assertTrue(r.dropped[0].label.startswith("insert “This is an entirely"))
        self.assertEqual(constrain(ORIGINAL, rewrite, 10).text, rewrite)

    def test_smallest_rewordings_first(self):
        orig = "alpha beta gamma delta epsilon zeta"
        rewrite = "one two gamma delta three zeta"
        r = constrain(orig, rewrite, 2)
        self.assertEqual(r.text, "alpha beta gamma delta three zeta")
        self.assertEqual(r.words_used, 1)

    def test_negation_is_never_a_free_fix(self):
        r = constrain("It is not fast.", "It is now fast.", 0)
        self.assertEqual(r.text, "It is not fast.")
        self.assertEqual(len(r.dropped), 1)

    def test_typos_and_inflections_are_free(self):
        r = constrain("we recieve the datas and it show", "we receive the data and it shows", 0)
        self.assertEqual(r.text, "we receive the data and it shows")
        self.assertEqual(r.words_used, 0)

    def test_authors_line_breaks_survive_reflow(self):
        orig = "first line of text\nsecond line of text"
        r = constrain(orig, "first line of text second line of text", 10)
        self.assertEqual(r.text, orig)

    def test_measure_and_manual_apply(self):
        m = measure(ORIGINAL,
                    "In this paper we demonstrate that the proposed method, which is "
                    "described in \\cref{sec:method}, outperform the baseline by a large "
                    "margin (see \\cite{smith2020}).")
        self.assertEqual(m.word_changes, 1)
        self.assertEqual(m.rewordings, 1)
        self.assertGreater(m.free_fixes, 0)
        s = EditSuggestion(0, "shows", "demonstrate", 1)
        self.assertEqual(apply_suggestion(s, "we shows it"), "we demonstrate it")
        self.assertIsNone(apply_suggestion(s, "nothing here"))

    def test_budget_never_applies_structural_changes(self):
        original = "Producing synthetic data is a challenge that migrations face today."
        rewrite = original + "\n\\end{abstract}"
        r = constrain(original, rewrite, 50)
        self.assertEqual(r.text, original)
        self.assertEqual(len(r.dropped), 1)
        self.assertTrue(r.dropped[0].is_structural)
        self.assertTrue(r.dropped[0].label.startswith("structural change: insert"))

        # A dropped citation is structural too, even though it costs no words.
        cite = constrain("as shown~\\cite{smith}.", "as shown.", 50)
        self.assertEqual(cite.text, "as shown~\\cite{smith}.")
        self.assertTrue(cite.dropped[0].is_structural)

        # Harmless command fixes stay free.
        free = constrain("10 \\% of cases", "10\\,\\% of cases", 0)
        self.assertEqual(free.text, "10\\,\\% of cases")


if __name__ == "__main__":
    unittest.main()
