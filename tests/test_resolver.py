import json
import unittest

import httpx

from resolve_pipeline import ArgmaxResolver, Candidate, Mention, WordLiftResolver, run
from resolve_pipeline.pipeline import context_window
from resolve_pipeline.types import NO_CANDIDATES, PROTOCOL_ERROR, RESOLVER_UNAVAILABLE

APPLE = Mention("Apple", 12, 17, "Organization", 0.9)
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
                "start": 12, "end": 17, "status": "resolved",
                "entity": {"id": "wd:Q312", "label": "Apple Inc.", "types": ["Organization"]}, "score": 0.97}]})

        r = self.client(handler).resolve(APPLE, TEXT, CANDS, language="en")
        self.assertEqual(seen["auth"], "Key k")
        self.assertEqual(seen["body"]["mentions"][0]["candidates"][0]["id"], "wd:Q312")
        self.assertEqual(seen["body"]["dataset_uri"], "wikidata://public")
        self.assertTrue(r.resolved)
        self.assertEqual((r.entity.id, r.score), ("wd:Q312", 0.97))

    def test_unresolved_response_keeps_reason(self):
        row = {"start": 12, "end": 17, "status": "unresolved", "entity": None, "reason": "no_suitable_candidate"}
        handler = lambda req: httpx.Response(200, json={"mentions": [row]})
        r = self.client(handler).resolve(APPLE, TEXT, CANDS)
        self.assertFalse(r.resolved)
        self.assertEqual(r.reason, "no_suitable_candidate")

    def test_empty_candidates_defer_to_engine_retrieval_unless_disabled(self):
        seen = {}

        def handler(request):
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"mentions": [{"start": 12, "end": 17, "status": "resolved",
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
            return httpx.Response(200, json={"mentions": [{"start": 12, "end": 17, "status": "resolved",
                                                             "entity": {"id": "https://x/apple", "label": "Apple",
                                                                        "types": ["Organization"],
                                                                        "same_as": ["http://www.wikidata.org/entity/Q312"]}}],
                                             "dataset_uri": "inline"})

        wl = WordLiftResolver("k", dataset=[{"id": "https://x/apple", "name": "Apple", "types": ["Organization"]}],
                              client=httpx.Client(transport=httpx.MockTransport(handler)))
        r = wl.resolve(APPLE, TEXT, CANDS, language="en")      # candidates are ignored: the dataset is the world
        self.assertEqual(seen["body"]["dataset_uri"], "inline")
        self.assertEqual(seen["body"]["dataset"]["entities"][0]["id"], "https://x/apple")
        self.assertNotIn("candidates", seen["body"]["mentions"][0])
        self.assertEqual((r.entity.id, r.entity.same_as), ("https://x/apple", ("http://www.wikidata.org/entity/Q312",)))
        mock = httpx.Client(transport=httpx.MockTransport(handler))
        graph = WordLiftResolver("k", dataset_uri=WordLiftResolver.WORDLIFT_GRAPH, client=mock)
        graph.resolve(APPLE, TEXT, [], language="en")
        self.assertEqual(seen["body"]["dataset_uri"], "wordlift://dataset/me")
        local_first = WordLiftResolver("k", dataset_uri=WordLiftResolver.LOCAL_FIRST, client=mock)
        r = local_first.resolve(APPLE, TEXT, CANDS, language="en")
        self.assertEqual(seen["body"]["dataset_uri"], "wordlift://dataset/me,wikidata://public")
        self.assertNotIn("candidates", seen["body"]["mentions"][0])      # a user world is in the list
        self.assertIsNone(r.diagnostics.get("dataset_uri"))   # the mock answers without a per-mention world

    def test_transport_failure_is_nil_not_a_guess(self):
        handler = lambda req: httpx.Response(503)
        r = self.client(handler).resolve(APPLE, TEXT, CANDS)
        self.assertFalse(r.resolved)
        self.assertEqual(r.reason, RESOLVER_UNAVAILABLE)


class IdentitySafetyTests(unittest.TestCase):
    """The two findings of the 2026-10-07 review: offsets and the fallback row."""

    def client(self, handler):
        return WordLiftResolver("k", base_url="https://example.test", client=httpx.Client(transport=httpx.MockTransport(handler)))

    def test_cropped_context_sends_the_span_rebased_and_returns_document_offsets(self):
        text = "x" * 500 + " Apple laptop"
        mention = Mention("Apple", 501, 506)
        seen = {}

        def handler(request):
            seen["body"] = json.loads(request.content)
            b = seen["body"]
            return httpx.Response(200, json={"mentions": [{"start": b["mentions"][0]["start"], "end": b["mentions"][0]["end"],
                                                             "status": "resolved", "entity": {"id": "Q312", "label": "Apple Inc."}}]})

        ctx = context_window(text, mention, radius=400)
        self.assertEqual((ctx.offset, len(ctx.text)), (101, 412))
        r = self.client(handler).resolve(mention, ctx, CANDS)
        sent = seen["body"]
        self.assertEqual(sent["text"][sent["mentions"][0]["start"]:sent["mentions"][0]["end"]], "Apple")
        self.assertEqual((sent["mentions"][0]["start"], sent["mentions"][0]["end"]), (400, 405))
        self.assertTrue(r.resolved)
        self.assertEqual((r.mention.start, r.mention.end), (501, 506))     # document offsets, as given

    def test_mentions_before_and_after_the_crop_and_a_repeated_form(self):
        text = "Paris in France or Paris in Texas. " * 30
        first, second = text.index("Paris"), text.index("Paris", 20)
        texas = text.rindex("Texas")
        for m in (Mention("Paris", first, first + 5), Mention("Paris", second, second + 5), Mention("Texas", texas, texas + 5)):
            ctx = context_window(text, m, radius=50)
            self.assertEqual(ctx.text[m.start - ctx.offset:m.end - ctx.offset], m.text)
            self.assertIsNotNone(ctx.local_span(m))

    def test_a_span_that_does_not_match_the_context_is_the_callers_error_not_a_request(self):
        calls = []
        handler = lambda req: calls.append(req) or httpx.Response(200, json={"mentions": []})
        with self.assertRaises(ValueError):                                          # the old bug, caught
            self.client(handler).resolve(Mention("Apple", 501, 506), "x" * 405, CANDS)
        self.assertEqual(calls, [])

    def test_the_context_is_a_str_for_retrievers_and_resolvers(self):
        ctx = context_window("x" * 500 + "Apple", Mention("Apple", 500, 505), radius=10)
        self.assertIsInstance(ctx, str)
        self.assertEqual((ctx.lower(), ctx.offset, json.dumps(ctx)), ("x" * 10 + "apple", 490, json.dumps(ctx.text)))

    def test_a_boolean_score_is_no_score(self):
        r = WordLiftResolver.parse(APPLE, {"mentions": [{"start": 12, "end": 17, "status": "resolved",
                                                         "entity": {"id": "Q312"}, "score": True}]}, TEXT)
        self.assertTrue(r.resolved)
        self.assertIsNone(r.score)

    def test_no_row_for_the_span_is_a_protocol_error_never_another_row(self):
        handler = lambda req: httpx.Response(200, json={"mentions": [{"start": 28, "end": 32, "status": "resolved",
                                                                        "entity": {"id": "Q220", "label": "Rome"}}]})
        r = self.client(handler).resolve(APPLE, TEXT, CANDS)
        self.assertEqual((r.resolved, r.reason), (False, PROTOCOL_ERROR))
        self.assertIsNone(r.entity)

    def test_two_rows_for_the_span_are_a_protocol_error(self):
        row = {"start": 12, "end": 17, "status": "resolved", "entity": {"id": "Q312", "label": "Apple Inc."}}
        handler = lambda req: httpx.Response(200, json={"mentions": [row, dict(row, entity={"id": "Q89"})]})
        r = self.client(handler).resolve(APPLE, TEXT, CANDS)
        self.assertEqual((r.resolved, r.reason), (False, PROTOCOL_ERROR))

    def test_malformed_bodies_are_protocol_errors_and_unknown_fields_are_ignored(self):
        for body in (None, [], {"mentions": None}, {"mentions": [{"start": 12, "end": 17, "status": "resolved", "entity": None}]},
                     {"mentions": [{"start": 12, "end": 17, "status": "resolved", "entity": {"label": "no id"}}]},
                     {"mentions": [{"start": 12, "end": 17, "status": "maybe"}]}):
            r = WordLiftResolver.parse(APPLE, body, TEXT)
            self.assertEqual((r.resolved, r.reason), (False, PROTOCOL_ERROR), body)
        ok = WordLiftResolver.parse(APPLE, {"mentions": [{"start": 12, "end": 17, "status": "resolved", "future": 1,
                                                          "entity": {"id": "Q312", "colour": "red"}, "score": None}], "extra": True}, TEXT)
        self.assertTrue(ok.resolved)
        self.assertIsNone(ok.score)
        text_body = lambda req: httpx.Response(200, text="<html>maintenance</html>", headers={"content-type": "text/html"})
        self.assertEqual(self.client(text_body).resolve(APPLE, TEXT, CANDS).reason, PROTOCOL_ERROR)

    def test_argmax_ignores_nonfinite_scores(self):
        r = ArgmaxResolver(min_score=0.5, min_margin=0.1).resolve(APPLE, TEXT, [Candidate("a", score=float("nan"))])
        self.assertEqual((r.resolved, r.reason), (False, NO_CANDIDATES))
        r = ArgmaxResolver(min_score=0.5).resolve(APPLE, TEXT, [Candidate("a", score=float("inf")), Candidate("b", score=0.9)])
        self.assertEqual(r.entity.id, "b")

    def test_owned_client_is_closed_and_an_injected_one_is_not(self):
        with WordLiftResolver("k") as wl:
            self.assertFalse(wl._client.is_closed)
        self.assertTrue(wl._client.is_closed)
        injected = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
        with WordLiftResolver("k", client=injected):
            pass
        self.assertFalse(injected.is_closed)


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
