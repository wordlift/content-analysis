import json
import unittest

import httpx

from resolve_pipeline import ArgmaxResolver, Candidate, InvalidRequestError, Mention, WordLiftResolver, run
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

    def test_a_rejected_request_raises_invalid_request_not_unavailability(self):
        detail = [{"loc": ["body", "mentions", 0, "candidates"], "msg": "not allowed with a user dataset"}]
        for handler, expected in ((lambda req: httpx.Response(422, json={"detail": detail}), detail),
                                  (lambda req: httpx.Response(422, text="bad span"), "bad span")):
            with self.assertRaises(InvalidRequestError) as caught:
                self.client(handler).resolve(APPLE, TEXT, CANDS)
            self.assertEqual(caught.exception.detail, expected)
            self.assertIsInstance(caught.exception, ValueError)     # one family with the mismatched span


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


class InvalidMentionTests(unittest.TestCase):
    def test_run_raises_on_a_mention_that_is_not_in_the_text(self):
        from resolve_pipeline.types import INVALID_MENTION
        text = "Apple opened a store in Rome."
        bad = Mention("Rome", 0, 4)
        with self.assertRaises(ValueError):
            run(text, retrieve=lambda m, c: [], resolver=ArgmaxResolver(), mentions=[bad], language="en")
        out = run(text, retrieve=lambda m, c: [], resolver=ArgmaxResolver(), mentions=[Mention("Apple", 0, 5), bad],
                  language="en", on_invalid_mention="unresolved")
        self.assertEqual([r.status for r in out], ["unresolved", "unresolved"])
        self.assertEqual(out[1].reason, INVALID_MENTION)
        self.assertEqual(out[0].mention.text, "Apple")
        with self.assertRaises(ValueError):
            run(text, retrieve=lambda m, c: [], resolver=ArgmaxResolver(), mentions=[bad], on_invalid_mention="skip")


class BatchResolutionTests(unittest.TestCase):
    """Issue #4: one request per document, the vocabulary once, order and offsets kept."""

    TEXT = "Apple opened a store in Rome. Tim Cook flew to Paris. Apple again."

    def spans(self):
        return [Mention("Apple", 0, 5), Mention("Rome", 24, 28), Mention("Tim Cook", 30, 38),
                Mention("Paris", 47, 52), Mention("Apple", 54, 59)]

    @staticmethod
    def resolver(handler):
        return WordLiftResolver("k", base_url="https://example.test", client=httpx.Client(transport=httpx.MockTransport(handler)))

    def engine(self, calls, status=200, headers=None, rows=None):
        def handler(request):
            body = json.loads(request.content)
            calls.append(body)
            if status != 200:
                problem = {"type": "https://docs.wordlift.io/problems/too_many_requests", "status": status,
                           "code": "too_many_requests", "detail": "full"}
                return httpx.Response(status, json=problem, headers=headers or {})
            out = rows if rows is not None else [
                {"text": m["text"], "start": m["start"], "end": m["end"], "status": "resolved",
                 "entity": {"id": f"Q{m['start']}", "label": m["text"]}, "score": 0.9} for m in body["mentions"]]
            return httpx.Response(200, json={"mentions": out}, headers=headers or {})
        return handler

    def test_one_request_carries_every_span_and_the_vocabulary_once(self):
        calls = []
        vocab = [{"id": "v:apple", "name": "Apple", "description": "x" * 1000}]
        handler = self.engine(calls, headers={"X-Wordlift-Consumption": "2"})
        resolver = WordLiftResolver("k", base_url="https://example.test", dataset=vocab,
                                    client=httpx.Client(transport=httpx.MockTransport(handler)))
        from resolve_pipeline import run
        res = run(self.TEXT, retrieve=lambda m, c: [], resolver=resolver, mentions=self.spans(), language="en")
        self.assertEqual(len(calls), 1)                                   # was five requests
        self.assertEqual(len(calls[0]["mentions"]), 5)
        self.assertEqual(calls[0]["dataset"], {"entities": vocab})       # sent once, not five times
        self.assertEqual([r.mention.text for r in res], ["Apple", "Rome", "Tim Cook", "Paris", "Apple"])
        self.assertEqual([r.entity.id for r in res], ["Q0", "Q24", "Q30", "Q47", "Q54"])  # order and offsets kept
        self.assertTrue(all(r.diagnostics["credits"] == "2" for r in res))

    def test_batches_are_bounded(self):
        calls = []
        resolver = self.resolver(self.engine(calls))
        resolver.MAX_MENTIONS_PER_REQUEST = 2
        res = resolver.resolve_many(self.TEXT, self.spans(), [[]] * 5, language="en")
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(res), 5)

    def test_rate_limit_is_distinguishable_and_carries_retry_after(self):
        resolver = self.resolver(self.engine([], status=429, headers={"Retry-After": "5"}))
        res = resolver.resolve_many(self.TEXT, self.spans()[:2], [[], []])
        self.assertEqual([r.reason for r in res], ["rate_limited", "rate_limited"])
        self.assertEqual(res[0].diagnostics, {"status": 429, "code": "too_many_requests", "retry_after": "5"})

    def test_a_refused_key_stops_the_batch(self):
        from resolve_pipeline.resolver import AuthorizationError
        resolver = self.resolver(self.engine([], status=401))
        with self.assertRaises(AuthorizationError):
            resolver.resolve_many(self.TEXT, self.spans()[:1], [[]])

    def test_a_missing_row_is_a_protocol_error_for_that_mention_only(self):
        rows = [{"text": "Apple", "start": 0, "end": 5, "status": "resolved", "entity": {"id": "Q312"}}]
        resolver = self.resolver(self.engine([], rows=rows))
        res = resolver.resolve_many(self.TEXT, self.spans()[:2], [[], []])
        self.assertEqual(res[0].entity.id, "Q312")
        self.assertEqual(res[1].reason, "protocol_error")

    def test_a_single_mention_resolver_still_works_through_run(self):
        from resolve_pipeline import run
        res = run(self.TEXT, retrieve=lambda m, c: [Candidate("wd:Q1", "x", score=0.9)], resolver=ArgmaxResolver(),
                  mentions=self.spans(), language="en")
        self.assertEqual(len(res), 5)

    def test_long_documents_are_chunked_without_cutting_a_mention(self):
        from resolve_pipeline.pipeline import chunk_document
        text = ("word " * 30000).strip()                  # 149,999 chars
        boundary = 99_995
        mention = Mention(text[boundary - 3:boundary + 6], boundary - 3, boundary + 6)
        chunks = chunk_document(text, [mention], limit=100_000)
        self.assertEqual(chunks[0][0], 0)
        self.assertEqual(chunks[-1][1], len(text))
        for a, b in chunks:
            self.assertLessEqual(b - a, 100_000)
            self.assertFalse(a < mention.start < b < mention.end)  # no chunk ends inside the mention
        calls = []
        resolver = self.resolver(self.engine(calls))
        from resolve_pipeline import run
        spans = [Mention("word", 0, 4), mention, Mention("word", len(text) - 4, len(text))]
        res = run(text, retrieve=lambda m, c: [], resolver=resolver, mentions=spans)
        self.assertEqual(len(calls), 2)
        self.assertEqual([r.mention.start for r in res], [0, mention.start, len(text) - 4])
        for call in calls:
            self.assertLessEqual(len(call["text"]), 100_000)
