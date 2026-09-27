import json
import unittest

import httpx

from resolve_pipeline import ArgmaxResolver, Candidate, Mention, WordLiftResolver, run
from resolve_pipeline.types import NO_CANDIDATES, RESOLVER_UNAVAILABLE

APPLE = Mention("Apple", 13, 18, "Organization", 0.9)
TEXT = "I bought an Apple laptop in Rome."
CANDS = [Candidate("wd:Q312", "Apple Inc.", score=0.93), Candidate("wd:Q89", "apple", score=0.41)]


class ArgmaxTests(unittest.TestCase):
    def test_accepts_clear_winner(self):
        r = ArgmaxResolver(min_score=0.6, min_margin=0.1).resolve(APPLE, TEXT, CANDS)
        self.assertTrue(r.resolved)
        self.assertEqual(r.entity.id, "wd:Q312")

    def test_nil_on_empty_low_or_tied(self):
        self.assertEqual(ArgmaxResolver().resolve(APPLE, TEXT, []).reason, NO_CANDIDATES)
        self.assertFalse(ArgmaxResolver(min_score=0.95).resolve(APPLE, TEXT, CANDS).resolved)
        tied = [Candidate("a", score=0.8), Candidate("b", score=0.79)]
        self.assertFalse(ArgmaxResolver(min_margin=0.05).resolve(APPLE, TEXT, tied).resolved)


class WordLiftClientTests(unittest.TestCase):
    def client(self, handler):
        return WordLiftResolver("k", base_url="https://example.test", client=httpx.Client(transport=httpx.MockTransport(handler)))

    def test_payload_and_resolved_response(self):
        seen = {}

        def handler(request):
            seen["auth"] = request.headers["Authorization"]
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"mentions": [{
                "start": 13, "end": 18, "status": "resolved",
                "entity": {"id": "wd:Q312", "label": "Apple Inc.", "types": ["Organization"]}, "score": 0.97}]})

        r = self.client(handler).resolve(APPLE, TEXT, CANDS, language="en")
        self.assertEqual(seen["auth"], "Key k")
        self.assertEqual(seen["body"]["mentions"][0]["candidates"][0]["id"], "wd:Q312")
        self.assertEqual(seen["body"]["dataset_uri"], "wikidata://public")
        self.assertTrue(r.resolved)
        self.assertEqual((r.entity.id, r.score), ("wd:Q312", 0.97))

    def test_unresolved_response_keeps_reason(self):
        handler = lambda req: httpx.Response(200, json={"mentions": [{"start": 13, "end": 18, "status": "unresolved", "entity": None, "reason": "no_suitable_candidate"}]})
        r = self.client(handler).resolve(APPLE, TEXT, CANDS)
        self.assertFalse(r.resolved)
        self.assertEqual(r.reason, "no_suitable_candidate")

    def test_empty_candidates_defer_to_engine_retrieval_unless_disabled(self):
        seen = {}

        def handler(request):
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"mentions": [{"start": 13, "end": 18, "status": "resolved",
                                                             "entity": {"id": "Q312", "label": "Apple Inc."}}]})

        r = self.client(handler).resolve(APPLE, TEXT, [], language="en")
        self.assertTrue(r.resolved)
        self.assertNotIn("candidates", seen["body"]["mentions"][0])
        strict = WordLiftResolver("k", base_url="https://example.test", engine_retrieval=False,
                                  client=httpx.Client(transport=httpx.MockTransport(handler)))
        self.assertEqual(strict.resolve(APPLE, TEXT, []).reason, NO_CANDIDATES)

    def test_inline_dataset_is_sent_and_ids_come_back_as_given(self):
        seen = {}

        def handler(request):
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"mentions": [{"start": 13, "end": 18, "status": "resolved",
                                                             "entity": {"id": "https://x/apple", "label": "Apple", "types": ["Organization"],
                                                                        "same_as": ["http://www.wikidata.org/entity/Q312"]}}],
                                             "dataset_uri": "inline"})

        wl = WordLiftResolver("k", dataset=[{"id": "https://x/apple", "name": "Apple", "types": ["Organization"]}],
                              client=httpx.Client(transport=httpx.MockTransport(handler)))
        r = wl.resolve(APPLE, TEXT, CANDS, language="en")      # candidates are ignored: the dataset is the world
        self.assertEqual(seen["body"]["dataset_uri"], "inline")
        self.assertEqual(seen["body"]["dataset"]["entities"][0]["id"], "https://x/apple")
        self.assertNotIn("candidates", seen["body"]["mentions"][0])
        self.assertEqual((r.entity.id, r.entity.same_as), ("https://x/apple", ("http://www.wikidata.org/entity/Q312",)))
        graph = WordLiftResolver("k", dataset_uri=WordLiftResolver.WORDLIFT_GRAPH, client=httpx.Client(transport=httpx.MockTransport(handler)))
        graph.resolve(APPLE, TEXT, [], language="en")
        self.assertEqual(seen["body"]["dataset_uri"], "wordlift://dataset/me")
        local_first = WordLiftResolver("k", dataset_uri=WordLiftResolver.LOCAL_FIRST, client=httpx.Client(transport=httpx.MockTransport(handler)))
        r = local_first.resolve(APPLE, TEXT, CANDS, language="en")
        self.assertEqual(seen["body"]["dataset_uri"], "wordlift://dataset/me,wikidata://public")
        self.assertNotIn("candidates", seen["body"]["mentions"][0])      # a user world is in the list
        self.assertIsNone(r.diagnostics.get("dataset_uri"))   # the mock answers without a per-mention world

    def test_transport_failure_is_nil_not_a_guess(self):
        handler = lambda req: httpx.Response(503)
        r = self.client(handler).resolve(APPLE, TEXT, CANDS)
        self.assertFalse(r.resolved)
        self.assertEqual(r.reason, RESOLVER_UNAVAILABLE)


class PipelineTests(unittest.TestCase):
    def test_run_with_explicit_mentions(self):
        calls = []

        def retrieve(mention, context):
            calls.append((mention.text, context))
            return CANDS

        out = run(TEXT, retrieve=retrieve, resolver=ArgmaxResolver(), mentions=[APPLE], language="en")
        self.assertEqual(len(out), 1)
        self.assertEqual(calls[0][0], "Apple")
        self.assertIn("Apple laptop", calls[0][1])
        self.assertEqual(out[0].entity.id, "wd:Q312")


if __name__ == "__main__":
    unittest.main()
