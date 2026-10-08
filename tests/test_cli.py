"""`python -m resolve_pipeline`: options, request body, output and exit codes. Mocked transport, no key."""
import contextlib
import io
import json
import tempfile
import unittest
from unittest import mock

import httpx

from resolve_pipeline.__main__ import main

TEXT = "I bought an Apple laptop in Rome."
OK = {"mentions": [
    {"text": "Apple", "start": 12, "end": 17, "status": "resolved", "score": 0.97,
     "entity": {"id": "Q312", "label": "Apple Inc."}, "dataset_uri": "wikidata://public"},
    {"text": "Rome", "start": 28, "end": 32, "status": "resolved", "score": None, "entity": {"id": "Q220", "label": "Rome"}},
    {"text": "laptop", "start": 18, "end": 24, "status": "unresolved", "entity": None, "reason": "low_relevance"},
], "language": "en", "engine": "test", "processing_time_ms": 1}


def cli(argv, handler=lambda req: httpx.Response(200, json=OK), env_key="k"):
    """Run the CLI with a mocked transport; returns (exit code, stdout, stderr, requests seen)."""
    seen = []

    def post(url, **kwargs):
        def record(request):
            seen.append(request)
            return handler(request)
        with httpx.Client(transport=httpx.MockTransport(record)) as client:
            return client.post(url, **kwargs)

    out, err = io.StringIO(), io.StringIO()
    with mock.patch("resolve_pipeline.__main__.httpx.post", post), \
         mock.patch.dict("os.environ", {"WL_KEY": env_key} if env_key else {}, clear=True), \
         contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(argv)
        except SystemExit as exc:           # argparse errors
            code = exc.code
    return code, out.getvalue(), err.getvalue(), seen


class CliRequest(unittest.TestCase):
    def test_mention_and_candidates_become_the_span_sent(self):
        code, _, _, seen = cli([TEXT, "--mention", "Apple", "--candidate", "wd:Q312", "--candidate", "wd:Q89",
                                "--language", "en", "--include", "evidence", "--base-url", "https://example.test/"])
        self.assertEqual(code, 0)
        request = seen[0]
        self.assertEqual(str(request.url), "https://example.test/v1/resolve")
        self.assertEqual(request.headers["Authorization"], "Key k")
        body = json.loads(request.content)
        self.assertEqual(body["mentions"], [{"text": "Apple", "start": 12, "end": 17,
                                             "candidates": [{"id": "wd:Q312"}, {"id": "wd:Q89"}]}])
        self.assertEqual((body["language"], body["include"]), ("en", ["evidence"]))

    def test_an_inline_list_is_sent_as_entities(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump([{"id": "https://x/apple", "name": "Apple"}], fh)
        code, _, _, seen = cli([TEXT, "--dataset", fh.name])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(seen[0].content)["dataset"], {"entities": [{"id": "https://x/apple", "name": "Apple"}]})


class CliOptions(unittest.TestCase):
    def assert_refused(self, argv, message, env_key="k"):
        code, _, err, seen = cli(argv, env_key=env_key)
        self.assertEqual((code, seen), (2, []), err)            # refused before any request
        self.assertIn(message, err)

    def test_no_key(self):
        self.assert_refused([TEXT], "no API key", env_key=None)

    def test_candidates_are_refused_with_any_user_world(self):
        for world in ("wordlift://dataset/me", "wordlift://dataset/me, wikidata://public", "inline"):
            self.assert_refused([TEXT, "--mention", "Apple", "--candidate", "Q312", "--dataset-uri", world], "user world")
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump([], fh)
        self.assert_refused([TEXT, "--mention", "Apple", "--candidate", "Q312", "--dataset", fh.name], "user world")

    def test_candidates_need_a_mention_that_is_in_the_text(self):
        self.assert_refused([TEXT, "--candidate", "Q312"], "--candidate needs --mention")
        self.assert_refused([TEXT, "--mention", "Paris"], "not found in text")


class CliOutput(unittest.TestCase):
    def test_one_line_per_mention_and_a_missing_score_prints(self):
        code, out, err, _ = cli([TEXT])
        self.assertEqual(code, 0)
        apple, rome, laptop = out.splitlines()
        self.assertIn("Q312", apple)
        self.assertIn("score 0.97", apple)
        self.assertIn("score n/a", rome)
        self.assertIn("low_relevance", laptop)
        self.assertNotIn("via", out)                           # a single world is not repeated per line
        self.assertIn("language en · test · 1 ms", err)

    def test_the_answering_world_is_shown_for_an_ordered_list(self):
        _, out, _, _ = cli([TEXT, "--dataset-uri", "wordlift://dataset/me,wikidata://public"])
        self.assertIn("via wikidata://public", out.splitlines()[0])

    def test_json_prints_the_raw_response(self):
        code, out, _, _ = cli([TEXT, "--json"])
        self.assertEqual((code, json.loads(out)), (0, OK))


class CliFailures(unittest.TestCase):
    def test_failures_exit_2_as_resolver_unavailable(self):
        def unreachable(request):
            raise httpx.ConnectError("down", request=request)
        for handler, detail in ((lambda req: httpx.Response(503, text="busy"), "HTTP 503 busy"),
                                (unreachable, "ConnectError"),
                                (lambda req: httpx.Response(200, text="<html>"), "JSONDecodeError")):
            code, out, err, _ = cli([TEXT], handler)
            self.assertEqual((code, out), (2, ""), detail)
            self.assertIn(f"resolver_unavailable: {detail}", err)


if __name__ == "__main__":
    unittest.main()
