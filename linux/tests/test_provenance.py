"""Port of ProvenanceTests.swift."""

import json
import unittest

from latexcolab.core.provenance import Provenance


def sub(s: str, ranges):
    return [s[loc:loc + length] for loc, length in ranges]


class ProvenanceTests(unittest.TestCase):
    def test_ai_insertions_are_marked_and_survive_typing(self):
        original = "We shows that the method , works ."
        ai = "We show that the method, works."
        p = Provenance().remapped(original, ai, True)
        self.assertEqual(sub(ai, p.ranges), ["show", ",", "."])

        # The user then types a word: the AI marks move with the text.
        typed = "We show that the new method, works."
        p = p.remapped(ai, typed, False)
        self.assertEqual(sub(typed, p.ranges), ["show", ",", "."])

        h = p.highlights(original, typed)
        self.assertEqual(sub(typed, h.ai_in_draft), ["show", ",", "."])
        self.assertEqual(sub(typed, h.user_in_draft), ["new"])
        self.assertEqual(sub(original, h.ai_in_original), ["shows", ",", "."])
        self.assertEqual(h.user_in_original, [])
        self.assertEqual(h.ai_words, 1)
        self.assertEqual(h.ai_punctuation, 2)
        self.assertEqual(h.user_words, 1)

    def test_pure_ai_deletion_marks_document_words(self):
        original = "This is very very important."
        ai = "This is very important."
        p = Provenance().remapped(original, ai, True)
        self.assertEqual(p.ai_deletions, ["very"])
        h = p.highlights(original, ai)
        self.assertEqual(sub(original, h.ai_in_original), ["very"])
        self.assertEqual(h.ai_in_draft, [])

    def test_user_edits_stay_user_and_revert_clears_marks(self):
        original = "alpha beta gamma"
        user_draft = "alpha delta gamma"
        p = Provenance().remapped(original, user_draft, False)
        self.assertTrue(p.is_empty)
        h = p.highlights(original, user_draft)
        self.assertEqual(sub(user_draft, h.user_in_draft), ["delta"])
        self.assertEqual(sub(original, h.user_in_original), ["beta"])
        # AI then changes gamma → omega; user reverts everything.
        ai = "alpha delta omega"
        p = p.remapped(user_draft, ai, True)
        self.assertEqual(sub(ai, p.ranges), ["omega"])
        p = p.remapped(ai, original, False)
        self.assertEqual(p.ranges, [])

    def test_after_apply_shared_ai_words_show_in_both_panes(self):
        original = "We shows results."
        ai = "We show results."
        p = Provenance().remapped(original, ai, True)
        # After Apply the document equals the draft.
        h = p.highlights(ai, ai)
        self.assertEqual(sub(ai, h.ai_in_draft), ["show"])
        self.assertEqual(sub(ai, h.ai_in_original), ["show"])

    def test_json_round_trip(self):
        p = Provenance([(3, 4)], ["very"])
        back = Provenance.from_json(json.loads(json.dumps(p.to_json())))
        self.assertEqual(back, p)
        self.assertEqual(back.ranges, [(3, 4)])


if __name__ == "__main__":
    unittest.main()
