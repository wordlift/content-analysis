import unittest

from resolve_pipeline.evaluate import ABSTAINED, CORRECT, RESOLUTION_ERROR, RETRIEVAL_MISS, evaluate

CASES = [{"text": "Apple opened a store in Rome.", "lang": "en",
          "gold": {"Apple": {"qid": "Q312"}, "Rome": {"qid": "Q220"}}},
         {"text": "Jordan won six rings.", "lang": "en", "gold": {"Jordan": {"qid": "Q41421"}}},
         {"text": "Bath is in Somerset.", "lang": "en", "gold": {"Bath": {"qid": "Q22889"}}}]

PREDS = [{"doc": 0, "mention": "Apple", "candidates": ["Q312", "Q89"], "prediction": "Q312"},
         {"doc": 0, "mention": "Rome", "candidates": ["Q220", "Q1"], "prediction": "Q1"},
         {"doc": 1, "mention": "Jordan", "candidates": ["Q810"], "prediction": None},
         {"doc": 2, "mention": "Bath", "candidates": ["Q22889"], "prediction": None}]


class AttributionTests(unittest.TestCase):
    def test_every_error_has_an_owner(self):
        r = evaluate(CASES, PREDS)
        self.assertEqual(r["counts"], {CORRECT: 1, RESOLUTION_ERROR: 1, RETRIEVAL_MISS: 1, ABSTAINED: 1})
        self.assertAlmostEqual(r["candidate_recall"], 3 / 4)
        self.assertAlmostEqual(r["resolution_coverage"], 2 / 3)
        self.assertAlmostEqual(r["accepted_precision"], 1 / 2)
        self.assertAlmostEqual(r["end_to_end_accuracy"], 1 / 4)


if __name__ == "__main__":
    unittest.main()
