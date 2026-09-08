"""Port of TextMatcherAndServiceTests.swift and ParagraphCompareTests.swift."""

import unittest

from latexcolab.core import compare as cmp
from latexcolab.core import lmstudio, textmatcher


class TextMatcherTests(unittest.TestCase):
    def test_strips_latex(self):
        t = textmatcher.tokens_latex(
            "We show \\emph{strong} results~\\cite{foo} in $x^2$. % comment")
        self.assertEqual(t, ["we", "show", "strong", "results", "in"])

    def test_best_match_finds_paragraph(self):
        main = ("\\section{Intro}\n"
                "Deep networks are hard to train without normalisation layers.\n\n"
                "We propose a simple trick that removes the need for warm-up entirely.")
        other = "Unrelated text about cats and dogs.\n\nMore about cats."
        m = textmatcher.best_match(
            "We propose a simple trick that re-\nmoves the need for warm-up",
            [("main.tex", main), ("o.tex", other)])
        self.assertEqual(m.file, "main.tex")
        self.assertEqual(m.paragraph.start_line, 4)
        self.assertIsNone(textmatcher.best_match("completely different words here",
                                                 [("main.tex", main)]))


class LMStudioServiceTests(unittest.TestCase):
    def test_prompt_mentions_limit_and_instructions(self):
        p = lmstudio.user_prompt(lmstudio.CleanupRequest("Hi", 7, "no em dashes", "m"))
        self.assertIn("at most 7 words", p)
        self.assertIn("no em dashes", p)
        self.assertIn("<paragraph>\nHi\n</paragraph>", p)
        zero = lmstudio.user_prompt(lmstudio.CleanupRequest("Hi", 0, "", "m"))
        self.assertIn("Do not add, delete or replace any word", zero)
        self.assertNotIn("Additional instructions", zero)
        self.assertIn("proofreader, not a rewriter", lmstudio.SYSTEM_PROMPT)

    def test_extract_paragraph(self):
        extract = lmstudio.extract_paragraph
        self.assertEqual(extract("```latex\nA \\emph{b}.\n```"), "A \\emph{b}.")
        self.assertEqual(extract("<paragraph>\nA b.\n</paragraph>\n"), "A b.")
        self.assertEqual(extract("<think>\nhmm\n</think>\n\nA b."), "A b.")
        self.assertEqual(extract("Here is the revised paragraph:\n\nA b."), "A b.")
        self.assertEqual(extract("Note:\n\nsee \\ref{x}"), "see \\ref{x}")

    def test_parse_response(self):
        obj = {
            "model": "qwen",
            "choices": [{"message": {"role": "assistant", "content": "Fixed text."}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 3},
        }
        r = lmstudio.parse_response(obj)
        self.assertEqual(r.text, "Fixed text.")
        self.assertEqual(r.prompt_tokens, 10)
        self.assertEqual(r.completion_tokens, 3)
        with self.assertRaises(lmstudio.LMStudioError):
            lmstudio.parse_response({"choices": []})

    def test_pick_model_prefers_loaded_chat_model(self):
        models = [
            lmstudio.LMModel("text-embedding-x", "embeddings", "loaded"),
            lmstudio.LMModel("a", "llm", "not-loaded"),
            lmstudio.LMModel("b", "vlm", "loaded"),
        ]
        self.assertEqual(lmstudio.pick_model(models), "b")
        self.assertEqual(lmstudio.pick_model(models[:2]), "a")
        self.assertIsNone(lmstudio.pick_model(models[:1]))

    def test_base_url_normalisation(self):
        self.assertEqual(lmstudio.LMStudioService("127.0.0.1:1234/v1/").base_url,
                         "http://127.0.0.1:1234")
        self.assertEqual(lmstudio.LMStudioService("").base_url, "http://127.0.0.1:1234")


class ParagraphCompareTests(unittest.TestCase):
    def test_parse_well_formed_json(self):
        raw = ('{"verdict": "minor", "summary": "Mostly the same.", '
               '"changes": ["\'shows\' → \'show\'"], "meaning_differences": [], '
               '"latex_issues": ["\\\\cite{x} dropped"]}')
        c = cmp.parse_comparison(raw, "m")
        self.assertEqual(c.verdict, cmp.VERDICT_MINOR)
        self.assertEqual(c.summary, "Mostly the same.")
        self.assertEqual(c.changes, ["'shows' → 'show'"])
        self.assertEqual(c.meaning_differences, [])
        self.assertEqual(c.latex_issues, ["\\cite{x} dropped"])
        self.assertEqual(c.model, "m")

    def test_parse_tolerates_thinking_fences_and_strings(self):
        raw = ("<think>let me look</think>\nHere you go:\n```json\n"
               '{"verdict": "Same meaning", "summary": "Identical claims.", '
               '"changes": "Only punctuation.", "meaning_differences": "none"}\n```')
        c = cmp.parse_comparison(raw, "m")
        self.assertEqual(c.verdict, cmp.VERDICT_SAME)
        self.assertEqual(c.changes, ["Only punctuation."])
        self.assertEqual(c.meaning_differences, [])
        self.assertEqual(c.headline, "Says the same thing")

    def test_parse_falls_back_to_raw_text(self):
        c = cmp.parse_comparison("I think the second one is stronger.", "m")
        self.assertEqual(c.verdict, cmp.VERDICT_UNKNOWN)
        self.assertEqual(c.summary, "I think the second one is stronger.")

    def test_prompt_contains_both_sides(self):
        p = cmp.compare_user_prompt(cmp.CompareRequest("A", "B", "m"))
        self.assertIn("<original>\nA\n</original>", p)
        self.assertIn("<edited>\nB\n</edited>", p)
        self.assertIn('"verdict"', cmp.COMPARE_SYSTEM_PROMPT)

    def test_integrity_catches_dropped_citation_ref_number_and_math(self):
        original = ("We show a 34\\% gain over the baseline~\\cite{smith2020,jones21} "
                    "(see \\cref{sec:method}, $x^2$). It may help.")
        edited = ("We show a 43\\% gain over the baseline~\\cite{smith2020} "
                  "(see \\cref{sec:method}). It helps.")
        texts = [i.text for i in cmp.integrity(original, edited)]
        self.assertIn("Citation `jones21` was removed", texts)
        self.assertIn("Number 34 became 43", texts)
        self.assertTrue(any(t.startswith("Math $x^2$ was removed") for t in texts), texts)
        self.assertTrue(any(t.startswith("Hedging words changed: 1 → 0") for t in texts), texts)
        self.assertFalse(any(t.startswith("Reference") for t in texts), texts)

    def test_integrity_quiet_for_cosmetic_edit(self):
        original = ("In this paper we shows that the method , described in \\cref{sec:m} , "
                    "outperform the baseline.")
        edited = ("In this paper we show that the method, described in \\cref{sec:m}, "
                  "outperforms the baseline.")
        self.assertEqual(cmp.integrity(original, edited), [])

    def test_integrity_flags_negation_and_length(self):
        texts = [i.text for i in cmp.integrity(
            "The method does not fail on large inputs and was tested extensively on "
            "three datasets.", "The method fails.")]
        self.assertTrue(any(t.startswith("Negation words changed: 1 → 0") for t in texts), texts)
        self.assertTrue(any("shorter" in t for t in texts), texts)


if __name__ == "__main__":
    unittest.main()
