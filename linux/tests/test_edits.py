"""Port of EditsStoreTests.swift and HistoryTests.swift."""

import json
import os
import tempfile
import unittest

from latexcolab.core.edits import FILE_NAME, EditsStore, ParagraphEdit


class EditsStoreTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = self._dir.name

    def tearDown(self):
        self._dir.cleanup()

    def test_round_trip_and_lookup(self):
        store = EditsStore(self.tmp)
        store.load()
        self.assertEqual(len(store.edits), 0)
        e = ParagraphEdit(file="main.tex", original="Hello world.\n", draft="Hello, world.",
                          line_hint=12, max_words=5, instructions="be nice")
        store.upsert(e)
        store.save()
        self.assertTrue(os.path.exists(os.path.join(self.tmp, FILE_NAME)))

        again = EditsStore(self.tmp)
        again.load()
        self.assertEqual(len(again.edits), 1)
        self.assertEqual(again.find("main.tex", "  Hello world.  ").id, e.id)
        self.assertIsNone(again.find("other.tex", "Hello world."))
        self.assertEqual(again.pending_count, 1)

        applied = again.edits[0]
        applied.original = applied.draft
        applied.applied = True
        again.upsert(applied)
        self.assertEqual(again.pending_count, 0)
        self.assertEqual(len(again.edits), 1)
        again.remove(e.id)
        self.assertEqual(len(again.edits), 0)

    def test_json_shape(self):
        store = EditsStore(self.tmp)
        store.upsert(ParagraphEdit(file="a.tex", original="x", draft="y",
                                   line_hint=1, max_words=0))
        store.save()
        with open(store.path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn('"version" : 1', text)
        self.assertIn('"draft" : "y"', text)
        self.assertIn('"applied" : false', text)


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = self._dir.name

    def tearDown(self):
        self._dir.cleanup()

    def test_mark_applied_builds_history_and_round_trips(self):
        store = EditsStore(self.tmp)
        e = ParagraphEdit(file="main.tex", original="v1 text here", draft="v2 text here",
                          line_hint=10, max_words=5)
        e.mark_applied(note="first apply")
        self.assertEqual(e.original, "v2 text here")
        self.assertTrue(e.applied)
        self.assertEqual([v.text for v in e.history], ["v1 text here"])
        e.draft = "v3 text here"
        e.mark_applied()
        self.assertEqual([v.text for v in e.history], ["v1 text here", "v2 text here"])
        store.upsert(e)
        store.save()

        again = EditsStore(self.tmp)
        again.load()
        loaded = again.find_id(e.id)
        self.assertIsNotNone(loaded)
        self.assertEqual([v.text for v in loaded.history], ["v1 text here", "v2 text here"])
        self.assertEqual(loaded.history[0].note, "first apply")
        self.assertIsNotNone(loaded.applied_at)

    def test_old_json_without_history_still_loads(self):
        doc = {"version": 1, "edits": [{
            "id": "6F9619FF-8B86-D011-B42D-00C04FC964FF", "file": "a.tex",
            "original": "x", "draft": "y", "applied": False, "lineHint": 3, "maxWords": 7,
            "instructions": "", "createdAt": "2026-09-04T10:00:00Z",
            "updatedAt": "2026-09-04T10:00:00Z"}]}
        with open(os.path.join(self.tmp, FILE_NAME), "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        store = EditsStore(self.tmp)
        store.load()
        self.assertEqual(len(store.edits), 1)
        self.assertEqual(store.edits[0].history, [])
        self.assertIsNone(store.edits[0].applied_at)

    def test_match_falls_back_to_history_and_nearby_text(self):
        store = EditsStore(self.tmp)
        e = ParagraphEdit(
            file="main.tex",
            original="The quick brown fox jumps over the lazy dog near the river bank.",
            draft="The quick brown fox leaps over the lazy dog near the river bank.",
            line_hint=12, max_words=5)
        e.mark_applied()
        store.upsert(e)
        # Exact current text
        self.assertTrue(store.match("main.tex", e.original, 12).exact)
        # Reverted by hand to the old text → found via history
        via_history = store.match(
            "main.tex", "The quick brown fox jumps over the lazy dog near the river bank.", 40)
        self.assertEqual(via_history.edit.id, e.id)
        self.assertFalse(via_history.exact)
        # Hand-edited nearby paragraph with strong overlap
        fuzzy = store.match(
            "main.tex",
            "The quick brown fox leaps over the lazy dog near the river bank today.", 13)
        self.assertEqual(fuzzy.edit.id, e.id)
        self.assertFalse(fuzzy.exact)
        # Different file / far away / unrelated
        self.assertIsNone(store.match("other.tex", e.original, 12))
        self.assertIsNone(store.match("main.tex",
                                      "Completely unrelated words about cats and hats.", 12))

    def test_partial_records_do_not_adopt_paragraph_records(self):
        store = EditsStore(self.tmp)
        paragraph = ("The quick brown fox jumps over the lazy dog near the river bank. "
                     "It was a sunny day and the fox was happy.")
        store.upsert(ParagraphEdit(file="main.tex", original=paragraph, draft=paragraph,
                                   line_hint=12, max_words=5))
        sentence = "The quick brown fox jumps over the lazy dog near the river bank."
        self.assertIsNone(store.match("main.tex", sentence, 12, partial=True))
        store.upsert(ParagraphEdit(file="main.tex", original=sentence,
                                   draft=sentence + " Really.", line_hint=12, max_words=5,
                                   partial=True))
        self.assertTrue(store.match("main.tex", sentence, 12, partial=True).exact)
        self.assertFalse(store.match("main.tex", paragraph, 12).edit.partial)
        store.save()
        again = EditsStore(self.tmp)
        again.load()
        self.assertEqual(len([e for e in again.edits if e.partial]), 1)


if __name__ == "__main__":
    unittest.main()
