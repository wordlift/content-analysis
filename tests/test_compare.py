import importlib.util
import json
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path

import httpx

_PATH = Path(__file__).resolve().parent.parent / "tools" / "compare.py"
_spec = importlib.util.spec_from_file_location("compare", _PATH)
compare = importlib.util.module_from_spec(_spec)
sys.modules["compare"] = compare
_spec.loader.exec_module(compare)

TEXT = "Liverpool beat Chelsea at Anfield."
GOLD = {"Liverpool": {"qid": "Q1130849"}, "Chelsea": {"qid": "Q9616"}, "Anfield": {"qid": "Q19607"}}
CASES = {"en": [{"text": TEXT, "lang": "en", "gold": GOLD}],
         "it": [{"text": "Lavazza e Illy sono marchi di caffè.", "lang": "it", "gold": {"Illy": {"qid": "Q969999"}}}]}


def doc(text=TEXT, lang="en", gold=None):
    gold = gold or CASES["en"][0]["gold"]
    spans = compare.place(text, gold)
    return compare.Doc("en", 0, lang, text, tuple(compare.Gold("en", 0, lang, n, *spans[n], g["qid"]) for n, g in gold.items()))


class Recorder:
    """A mocked transport routed by host and path; every request is kept."""

    def __init__(self, routes):
        self.routes, self.requests = routes, []

    def __call__(self, request):
        self.requests.append(request)
        for (host, path), handler in self.routes.items():
            if request.url.host == host and request.url.path.startswith(path):
                return handler(request)
        return httpx.Response(404, json={"detail": f"no route for {request.url}"})

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self))


def wikipedia_props(request):
    titles = request.url.params["titles"].split("|")
    known = {"Liverpool F.C.": "Q1130849", "Chelsea F.C.": "Q9616", "Anfield": "Q19607", "Illycaffè": "Q969999"}
    redirects = [{"from": "Liverpool FC", "to": "Liverpool F.C."}] if "Liverpool FC" in titles else []
    pages = [{"title": t, "pageprops": {"wikibase_item": known[t]}} for t in known]
    return httpx.Response(200, json={"query": {"redirects": redirects, "pages": pages}})


def services(recorder, out):
    return compare.Services(compare.Http(recorder.client(), sleep=lambda s: None), Path(out))


class PlaceTests(unittest.TestCase):
    def test_longer_names_first_and_never_inside_a_word(self):
        spans = compare.place("Banco Santander has its headquarters in Santander.", ["Santander", "Banco Santander"])
        self.assertEqual(spans, {"Banco Santander": (0, 15), "Santander": (40, 49)})
        self.assertEqual(compare.place("Der Kölner Dom ist das Wahrzeichen von Köln.", ["Köln"])["Köln"], (39, 43))

    def test_a_name_only_inside_words_falls_back_and_a_missing_name_raises(self):
        self.assertEqual(compare.place("Kölner Dom", ["Köln"]), {"Köln": (0, 4)})
        with self.assertRaises(ValueError):
            compare.place("Rome", ["Paris"])

    def test_load_docs_reads_every_set_with_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            for name, cases in CASES.items():
                Path(folder, f"{name}_gold.json").write_text(json.dumps(cases * 2))
            docs = compare.load_docs(Path(folder), limit=1)
        self.assertEqual([(d.dataset, d.lang, len(d.gold)) for d in docs], [("en", "en", 3), ("it", "it", 1)])
        self.assertEqual(docs[1].gold[0].start, 10)


class ScoringTests(unittest.TestCase):
    def test_align_takes_the_same_span_then_the_largest_overlap(self):
        d = doc()
        found = [compare.Found(0, 14, "Liverpool beat", "Q1"), compare.Found(0, 9, "Liverpool", "Q1130849"),
                 compare.Found(15, 20, "Chels", "Q9616"), compare.Found(26, 33, "Anfield", None)]
        a = compare.align(d.gold, found)
        self.assertEqual([x.status for x in a], [compare.LINKED, compare.LINKED, compare.NO_ANSWER])
        self.assertEqual([x.qid for x in a[:2]], ["Q1130849", "Q9616"])
        self.assertEqual(compare.align(d.gold, [])[0].status, compare.NO_ANSWER)

    def test_tally_counts_unsupported_as_no_answer_and_errors_apart(self):
        d = doc()
        pairs = list(zip(d.gold, [compare.Answer(compare.LINKED, "wd:Q1130849"), compare.Answer(compare.LINKED, "Q1"),
                                  compare.Answer(compare.UNSUPPORTED)], strict=True))
        t = compare.tally(pairs + [(d.gold[0], compare.Answer(compare.ERROR))])
        self.assertEqual((t["correct"], t["wrong"], t["no_answer"], t["unsupported"], t["errors"]), (1, 1, 1, 1, 1))
        self.assertAlmostEqual(t["precision"], 0.5)
        self.assertAlmostEqual(t["recall"], 0.25)
        self.assertAlmostEqual(t["f1"], 1 / 3)
        self.assertEqual(compare.tally([])["f1"], 0.0)

    def test_head_to_head_and_paths(self):
        d = doc()
        ours = [compare.Answer(compare.LINKED, "Q1130849", detail={"path": "reranked"}),
                compare.Answer(compare.NO_ANSWER, detail={"path": "reranked"}),
                compare.Answer(compare.LINKED, "Q2", detail={"path": "fast"})]
        theirs = [compare.Answer(compare.LINKED, "Q24826"), compare.Answer(compare.LINKED, "Q9616"), compare.Answer(compare.LINKED, "Q3")]
        h = compare.head_to_head(list(zip(d.gold, ours, strict=True)), list(zip(d.gold, theirs, strict=True)))
        keys = ("only_ours", "only_theirs_we_none", "neither", "their_wrong", "their_wrong_we_fixed")
        self.assertEqual(tuple(h[k] for k in keys), (1, 1, 1, 2, 1))
        self.assertEqual(h["their_wrong_by_our_path"], {"reranked": 1, "fast": 1})
        paths = compare.by_path(list(zip(d.gold, ours, strict=True)))
        self.assertEqual(paths, {"fast": {"answered": 1, "correct": 0, "declined": 0},
                                 "reranked": {"answered": 1, "correct": 1, "declined": 1}})


class TransportTests(unittest.TestCase):
    def test_retry_after_from_header_or_demo_body(self):
        self.assertEqual(compare.retry_after(httpx.Response(429, headers={"Retry-After": "7"})), 7.0)
        self.assertEqual(compare.retry_after(httpx.Response(429, json={"retry_after_s": 60})), 60.0)
        self.assertIsNone(compare.retry_after(httpx.Response(429, text="slow down")))

    def test_429_is_retried_and_errors_raise(self):
        replies = iter([httpx.Response(429, headers={"Retry-After": "2"}), httpx.Response(200, json={"ok": True})])
        slept = []
        http = compare.Http(httpx.Client(transport=httpx.MockTransport(lambda r: next(replies))), sleep=slept.append)
        self.assertEqual(http.json("GET", "https://example.org/"), {"ok": True})
        self.assertEqual(slept, [2.0])
        refused = compare.Http(httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401, text="no\n key"))))
        with self.assertRaises(compare.ServiceError) as caught:
            refused.json("GET", "https://example.org/")
        self.assertTrue(caught.exception.fatal)
        self.assertEqual(str(caught.exception), "HTTP 401: no key")
        google = httpx.Response(400, json={"error": {"code": 400, "message": "API key not\n valid."}})
        self.assertEqual(compare.error_text(google), "API key not valid.")
        self.assertEqual(compare.error_text(httpx.Response(422, json={"detail": "text_required"})), "text_required")
        broken = compare.Http(httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html>"))))
        with self.assertRaises(compare.ServiceError):
            broken.json("GET", "https://example.org/")

        def down(request):
            raise httpx.ConnectError("refused")
        with self.assertRaises(compare.ServiceError):
            compare.Http(httpx.Client(transport=httpx.MockTransport(down))).json("GET", "https://example.org/")

    def test_cache_stores_answers_not_failures(self):
        with tempfile.TemporaryDirectory() as out:
            cache = compare.Cache(Path(out, "x.json"))
            self.assertEqual(cache.get(["a"], lambda: 1), 1)
            with self.assertRaises(compare.ServiceError):
                cache.get(["b"], lambda: (_ for _ in ()).throw(compare.ServiceError("down")))
            again = compare.Cache(Path(out, "x.json"))
            self.assertEqual(again.get(["a"], lambda: 2), 1)
            self.assertNotIn(["b"], again)

    def test_wikipedia_urls_map_through_redirects_and_skip_other_hosts(self):
        rec = Recorder({("en.wikipedia.org", "/w/api.php"): wikipedia_props})
        with tempfile.TemporaryDirectory() as out:
            ids = services(rec, out).wikipedia
            qids = ids.qids(["http://en.wikipedia.org/wiki/Liverpool_FC", "https://example.org/wiki/X", "https://en.wikipedia.org/wiki/Nowhere"])
            self.assertEqual(qids, {"http://en.wikipedia.org/wiki/Liverpool_FC": "Q1130849", "https://example.org/wiki/X": None,
                                    "https://en.wikipedia.org/wiki/Nowhere": None})
            ids.qids(["http://en.wikipedia.org/wiki/Liverpool_FC"])
        self.assertEqual(len(rec.requests), 1)


class SystemTests(unittest.TestCase):
    def run_one(self, cls, env, routes, d=None):
        rec = Recorder({("en.wikipedia.org", "/w/api.php"): wikipedia_props, **routes})
        with tempfile.TemporaryDirectory() as out:
            system = cls.from_env(env, services(rec, out))
            answers = system.answer(d or doc())
        return answers, rec

    def test_unconfigured_systems_are_none(self):
        with tempfile.TemporaryDirectory() as out:
            s = services(Recorder({}), out)
            for cls in (compare.ResolveAPI, compare.EntityFishing, compare.Dandelion, compare.Google, compare.Azure):
                self.assertIsNone(cls.from_env({}, s), cls.name)

    def test_resolve_api_goes_to_the_account_route_with_the_gold_spans(self):
        def resolve(request):
            body = json.loads(request.content)
            rows = [{"text": m["text"], "start": m["start"], "end": m["end"], "status": "resolved", "score": 0.9,
                     "entity": {"id": "Q1130849", "label": "Liverpool F.C."}, "signals": {"path": "reranked"}} for m in body["mentions"]]
            rows[1] = {**rows[1], "status": "unresolved", "entity": None, "reason": "low_relevance"}
            return httpx.Response(200, json={"mentions": rows})
        answers, rec = self.run_one(compare.ResolveAPI, {"WL_KEY": "k"}, {
            ("api.wordlift.io", "/accounts/me"): lambda r: httpx.Response(200, json={"accountId": 42}),
            ("api.wordlift.io", "/accounts/42/resolve"): resolve})
        self.assertEqual([a.status for a in answers], [compare.LINKED, compare.NO_ANSWER, compare.LINKED])
        self.assertEqual(answers[0].detail["path"], "reranked")
        sent = json.loads(rec.requests[-1].content)
        self.assertEqual([(m["start"], m["end"]) for m in sent["mentions"]], [(0, 9), (15, 22), (26, 33)])

    def test_resolve_api_waits_on_rate_limit_and_raises_on_failure(self):
        replies = iter([httpx.Response(429, headers={"Retry-After": "3"}, json={"code": "too_many_requests"}),
                        httpx.Response(503, text="down")])
        with tempfile.TemporaryDirectory() as out:
            rec = Recorder({("api.wordlift.io", "/accounts/me"): lambda r: httpx.Response(200, json={"accountId": 1}),
                            ("api.wordlift.io", "/accounts/1/resolve"): lambda r: next(replies)})
            s = services(rec, out)
            slept = []
            s.http.sleep = slept.append
            system = compare.ResolveAPI.from_env({"WL_KEY": "k"}, s)
            with self.assertRaises(compare.ServiceError):
                system.answer(doc())
        self.assertEqual(slept, [3.0])

    def test_resolve_demo_sends_spans_and_parses_the_contract(self):
        def demo(request):
            body = json.loads(request.content)
            self.assertEqual(body["language"], "en")
            rows = [{**m, "status": "resolved", "entity": {"id": "Q9616"}} for m in body["mentions"]]
            return httpx.Response(200, json={"mentions": rows})
        answers, _ = self.run_one(compare.ResolveDemo, {}, {("resolve.wordlift.io", "/demo/resolve"): demo})
        self.assertEqual([a.qid for a in answers], ["Q9616"] * 3)

    def test_wikidata_first_hit_searches_in_the_document_language(self):
        def search(request):
            self.assertEqual((request.url.params["language"], request.url.params["limit"]), ("en", "1"))
            hits = [] if request.url.params["search"] == "Anfield" else [{"id": "Q24826", "label": "Liverpool"}]
            return httpx.Response(200, json={"search": hits})
        answers, _ = self.run_one(compare.WikidataFirstHit, {}, {("www.wikidata.org", "/w/api.php"): search})
        self.assertEqual([a.qid for a in answers], ["Q24826", "Q24826", None])

    def test_entity_fishing_disambiguates_the_given_spans_only(self):
        def disambiguate(request):
            self.assertIn(b'name="query"', request.content)
            query = json.loads(request.content.split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n--", 1)[0])
            self.assertEqual(query["mentions"], [])
            linked = [{**e, "wikidataId": "Q19607"} for e in query["entities"]]
            return httpx.Response(200, json={"entities": linked + [{"rawName": "x"}]})
        routes = {("localhost", "/service/disambiguate"): disambiguate}
        answers, rec = self.run_one(compare.EntityFishing, {"EF_URL": "http://localhost:8090/"}, routes)
        self.assertEqual([a.qid for a in answers], ["Q19607"] * 3)
        self.assertEqual(str(rec.requests[0].url), "http://localhost:8090/service/disambiguate")

    def test_dandelion_keeps_the_token_out_of_the_url(self):
        def nex(request):
            form = urllib.parse.parse_qs(request.content.decode())
            self.assertEqual(form["token"], ["t"])
            return httpx.Response(200, json={"annotations": [
                {"start": 0, "end": 9, "spot": "Liverpool", "uri": "http://en.wikipedia.org/wiki/Liverpool_FC", "confidence": 0.8},
                {"start": 26, "end": 33, "spot": "Anfield", "uri": "http://en.wikipedia.org/wiki/Anfield"}]})
        answers, rec = self.run_one(compare.Dandelion, {"DANDELION_API_KEY": "t"}, {("api.dandelion.eu", "/datatxt/nex/v1/"): nex})
        self.assertEqual([a.qid for a in answers], ["Q1130849", None, "Q19607"])
        self.assertNotIn("token", str(rec.requests[0].url))

    def test_google_reads_wikipedia_urls_with_code_point_offsets(self):
        def entities(request):
            self.assertEqual(request.headers["X-Goog-Api-Key"], "g")
            self.assertEqual(json.loads(request.content)["encodingType"], "UTF32")
            return httpx.Response(200, json={"entities": [
                {"name": "Chelsea F.C.", "metadata": {"wikipedia_url": "https://en.wikipedia.org/wiki/Chelsea_F.C."},
                 "mentions": [{"text": {"content": "Chelsea", "beginOffset": 15}, "type": "PROPER"}]},
                {"name": "Anfield", "mentions": [{"text": {"content": "Anfield", "beginOffset": 26}}, {"text": {"content": "?"}}]}]})
        routes = {("language.googleapis.com", "/v1/documents:analyzeEntities"): entities}
        answers, _ = self.run_one(compare.Google, {"GOOGLE_API_KEY": "g"}, routes)
        expected = [(compare.NO_ANSWER, None), (compare.LINKED, "Q9616"), (compare.NO_ANSWER, None)]
        self.assertEqual([(a.status, a.qid) for a in answers], expected)
        with tempfile.TemporaryDirectory() as out:
            token = compare.Google.from_env({"GOOGLE_ACCESS_TOKEN": "a", "GOOGLE_CLOUD_PROJECT": "p"}, services(Recorder({}), out))
        self.assertEqual(token.headers, {"Authorization": "Bearer a", "x-goog-user-project": "p"})

    def test_azure_maps_matches_and_raises_on_document_errors(self):
        def analyze(request):
            self.assertEqual(request.headers["Ocp-Apim-Subscription-Key"], "z")
            body = json.loads(request.content)
            self.assertEqual(body["parameters"]["stringIndexType"], "UnicodeCodePoint")
            return httpx.Response(200, json={"results": {"documents": [{"id": "1", "entities": [
                {"name": "Anfield", "url": "https://en.wikipedia.org/wiki/Anfield",
                 "matches": [{"text": "Anfield", "offset": 26, "length": 7}]}]}]}})
        env = {"AZURE_LANGUAGE_ENDPOINT": "https://x.cognitiveservices.azure.com/", "AZURE_LANGUAGE_KEY": "z"}
        answers, rec = self.run_one(compare.Azure, env, {("x.cognitiveservices.azure.com", "/language/:analyze-text"): analyze})
        self.assertEqual([a.qid for a in answers], [None, None, "Q19607"])
        self.assertEqual(rec.requests[0].url.params["api-version"], "2022-05-01")
        with self.assertRaises(compare.ServiceError):
            self.run_one(compare.Azure, env, {("x.cognitiveservices.azure.com", "/language"): lambda r: httpx.Response(200, json={
                "results": {"documents": [], "errors": [{"id": "1", "error": {"code": "UnsupportedLanguageCode"}}]}})})


class MainTests(unittest.TestCase):
    def routes(self):
        def resolve(request):
            body = json.loads(request.content)
            rows = [{**m, "status": "resolved", "entity": {"id": "Q1130849"}, "signals": {"path": "fast"}} for m in body["mentions"]]
            return httpx.Response(200, json={"mentions": rows})
        return {("api.wordlift.io", "/accounts/me"): lambda r: httpx.Response(200, json={"accountId": 7}),
                ("api.wordlift.io", "/accounts/7/resolve"): resolve,
                ("resolve.wordlift.io", "/demo/resolve"): resolve,
                ("www.wikidata.org", "/w/api.php"): lambda r: httpx.Response(200, json={"search": [{"id": "Q24826"}]}),
                ("language.googleapis.com", "/v1"): lambda r: httpx.Response(400, json={"error": {"message": "API key not valid"}})}

    def test_runs_what_is_configured_writes_outputs_and_a_rerun_sends_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            data, out = Path(folder, "data"), Path(folder, "out")
            data.mkdir()
            for name, cases in CASES.items():
                Path(data, f"{name}_gold.json").write_text(json.dumps(cases))
            env = {"WL_KEY": "k", "GOOGLE_API_KEY": "bad"}
            argv = ["--datasets", str(data), "--out", str(out)]
            rec = Recorder(self.routes())
            self.assertEqual(compare.main(argv, env=env, client=rec.client(), sleep=lambda s: None), 0)
            report = json.loads(Path(out, "report.json").read_text())
            self.assertEqual({k: v["status"] for k, v in report["systems"].items()},
                             {"resolve": "ran", "resolve-demo": "ran", "wikidata": "ran", "entity-fishing": "skipped",
                              "dandelion": "skipped", "google": "failed", "azure": "skipped"})
            self.assertEqual(report["systems"]["resolve"]["overall"]["correct"], 1)
            self.assertEqual(report["systems"]["resolve"]["paths"]["fast"]["answered"], 4)
            self.assertEqual(report["head_to_head"]["wikidata"]["their_wrong_we_fixed"], 1)
            summary = Path(out, "summary.md").read_text()
            self.assertIn("| resolve() (API) | 25.0 | 25.0 | 25.0 | 1 | 3 | 0 |", summary)
            self.assertIn("- Dandelion: set DANDELION_TOKEN", summary)
            rows = [json.loads(line) for line in Path(out, "rows.jsonl").read_text().splitlines()]
            self.assertEqual(len(rows), 3 * 4)
            sent = len([r for r in rec.requests if "googleapis" not in r.url.host])
            again = Recorder(self.routes())
            compare.main(argv + ["--systems", "resolve,resolve-demo,wikidata"], env=env, client=again.client(), sleep=lambda s: None)
            self.assertGreater(sent, 0)
            self.assertEqual([r.url.path for r in again.requests], [])

    def test_unknown_system_is_an_error(self):
        with self.assertRaises(SystemExit):
            compare.main(["--systems", "bing"], env={})

    def test_unsupported_languages_and_single_failures_are_recorded(self):
        docs = [doc(), doc()]
        calls = iter([compare.ServiceError("timeout"), [compare.Answer(compare.NO_ANSWER)] * 3])

        class Flaky(compare.System):
            name, languages = "flaky", frozenset({"en"})

            def answer(self, d):
                reply = next(calls)
                if isinstance(reply, Exception):
                    raise reply
                return reply
        logged = []
        pairs = compare.run_system(Flaky(), docs + [doc(lang="it")], logged.append)
        self.assertEqual([a.status for _, a in pairs], [compare.ERROR] * 3 + [compare.NO_ANSWER] * 3 + [compare.UNSUPPORTED] * 3)
        self.assertEqual(len(logged), 1)


if __name__ == "__main__":
    unittest.main()
