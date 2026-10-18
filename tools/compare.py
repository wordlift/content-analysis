#!/usr/bin/env python3
"""Run entity linking services on the same gold mentions and score them alike.

    python tools/compare.py --datasets datasets --out results

From the repository root after `make setup`. Each system runs when its
configuration is in the environment and is skipped otherwise:

    resolve          WL_KEY                                      gets the gold spans
    resolve-demo     nothing: the open demo, 10 requests a minute  gets the gold spans
    wikidata         nothing: wbsearchentities, first hit          gets the name alone
    entity-fishing   EF_URL, a running instance                    gets the gold spans
    dandelion        DANDELION_TOKEN (or DANDELION_API_KEY)        finds its own mentions
    google           GOOGLE_API_KEY, or GOOGLE_ACCESS_TOKEN        finds its own mentions
    azure            AZURE_LANGUAGE_ENDPOINT, AZURE_LANGUAGE_KEY   finds its own mentions

Scoring. An answer counts only when its Wikidata ID equals the gold ID.
Precision is correct out of answered, recall is correct out of every gold
mention, F1 their harmonic mean. A system that finds its own mentions gets the
benefit of the doubt: a link on the gold span counts, right or wrong; a link on
an overlapping span counts only when it is the gold ID ("o Porto" for "Porto"),
and is otherwise a reading of another mention ("north west of England" for
"England"), so no answer. A gold mention it skips counts as no answer, and so
does a mention in a language the service does not support (Azure: en, es). Each
service runs with its defaults (Dandelion's `min_confidence` 0.6). Answers
given as Wikipedia articles are mapped to Wikidata through that edition's page
props, redirects followed. The Wikidata search uses the document's language.

Every answer is cached under <out>/cache, so a re-run sends nothing and only
re-scores; a failed request is not cached and is sent again on the next run.
Outputs: summary.md (the tables), report.json (every number) and rows.jsonl
(one row per system and gold mention).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from resolve_pipeline.evaluate import canonical_id
from resolve_pipeline.resolver import WordLiftResolver
from resolve_pipeline.types import PROTOCOL_ERROR, RATE_LIMITED, RESOLVED, RESOLVER_UNAVAILABLE, Mention, Resolution

USER_AGENT = "resolve-compare/0.1 (https://github.com/wordlift/content-analysis)"
DEMO_URL = "https://resolve.wordlift.io/demo/resolve"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
DANDELION_URL = "https://api.dandelion.eu/datatxt/nex/v1/"
GOOGLE_URL = "https://language.googleapis.com/v1/documents:analyzeEntities"
AZURE_PATH = "/language/:analyze-text?api-version=2022-05-01"

LANGUAGES = {"en": "English", "it": "Italian", "fr": "French", "de": "German", "es": "Spanish", "pt": "Portuguese"}

LINKED, NO_ANSWER, UNSUPPORTED, ERROR = "linked", "no_answer", "unsupported", "error"
CORRECT, WRONG = "correct", "wrong"
# resolve() reasons that say the request failed, not that the engine declined.
FAILURES = {RESOLVER_UNAVAILABLE, RATE_LIMITED, PROTOCOL_ERROR}


# --- gold ---------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Gold:
    """One gold mention: where it is, and the one Wikidata ID it refers to."""
    dataset: str
    doc: int
    lang: str
    name: str
    start: int
    end: int
    qid: str


@dataclass(frozen=True)
class Doc:
    dataset: str
    index: int
    lang: str
    text: str
    gold: tuple[Gold, ...]


def place(text: str, names: Iterable[str]) -> dict[str, tuple[int, int]]:
    """Each name's span in `text`. Gold sets key mentions by name, so the span is inferred:
    longer names first, then the first occurrence that is neither inside a longer word nor
    inside a span already placed. In "Banco Santander has its headquarters in Santander"
    the city is the second "Santander"."""
    taken: list[tuple[int, int]] = []
    spans: dict[str, tuple[int, int]] = {}
    for name in sorted(names, key=lambda n: (-len(n), n)):
        fallback = None
        for match in re.finditer(re.escape(name), text):
            start, end = match.span()
            if any(start < t_end and t_start < end for t_start, t_end in taken):
                continue
            fallback = fallback or (start, end)
            if not ((start > 0 and text[start - 1].isalnum()) or (end < len(text) and text[end].isalnum())):
                spans[name] = (start, end)
                break
        else:
            if fallback is None:
                raise ValueError(f"{name!r} is not in {text!r}")
            spans[name] = fallback
        taken.append(spans[name])
    return spans


def load_docs(folder: Path, limit: int = 0) -> list[Doc]:
    """Every `<set>_gold.json` in `folder`, in name order. A case is {"text", "lang"} with either
    "gold": {name: {"qid"}} (the development sets: spans inferred by `place`) or
    "mentions": [{"text", "start", "end", "qid"}] (spans given, as tools/voxel.py writes them)."""
    docs = []
    for path in sorted(folder.glob("*_gold.json")):
        dataset = path.name.removesuffix("_gold.json")
        cases = json.loads(path.read_text(encoding="utf-8"))
        for i, case in enumerate(cases[:limit] if limit else cases):
            lang, text = case.get("lang") or dataset, case["text"]
            if "mentions" in case:
                for m in case["mentions"]:
                    if text[m["start"]:m["end"]] != m["text"]:
                        raise ValueError(f"{path.name} #{i}: {m['text']!r} is not at {m['start']}:{m['end']}")
                gold = tuple(Gold(dataset, i, lang, m["text"], m["start"], m["end"], str(canonical_id(m["qid"])))
                             for m in case["mentions"])
            else:
                spans = place(text, case["gold"])
                gold = tuple(Gold(dataset, i, lang, name, *spans[name], str(canonical_id(info["qid"])))
                             for name, info in case["gold"].items())
            docs.append(Doc(dataset, i, lang, text, gold))
    return docs


# --- answers ------------------------------------------------------------------------------------

@dataclass
class Answer:
    """What a system said about one gold mention."""
    status: str                         # linked | no_answer | unsupported | error
    qid: str | None = None
    found: str | None = None            # the span it linked, when it finds its own mentions
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Found:
    """A mention a service found on its own; `qid` None means found but not linked."""
    start: int
    end: int
    text: str
    qid: str | None
    detail: Mapping[str, Any] = field(default_factory=dict)


def align(gold: Iterable[Gold], found: list[Found]) -> list[Answer]:
    """The answer for each gold mention among the mentions a service found itself.

    The benefit of the doubt goes to the service. A link on the same span counts, right or
    wrong. A link on another span that overlaps counts when it is the gold ID, so a boundary
    difference ("o Porto" for "Porto") never costs a link. When it is another ID the service
    read another mention ("north west of England" for "England"): the gold mention gets no
    answer, and the other link is kept in the detail."""
    answers = []
    for g in gold:
        exact = next((f for f in found if (f.start, f.end) == (g.start, g.end) and f.qid), None)
        scored = [(min(g.end, f.end) - max(g.start, f.start), f) for f in found if f.qid]
        near = [f for _, f in sorted((p for p in scored if p[0] > 0), key=lambda p: (-p[0], p[1].start))]
        hit = exact or next((f for f in near if canonical_id(f.qid) == g.qid), None)
        if hit is not None:
            answers.append(Answer(LINKED, hit.qid, hit.text, dict(hit.detail)))
        elif near:
            answers.append(Answer(NO_ANSWER, found=near[0].text, detail={**near[0].detail, "other_span_qid": near[0].qid}))
        else:
            answers.append(Answer(NO_ANSWER))
    return answers


def outcome(gold: Gold, answer: Answer) -> str:
    if answer.status == LINKED:
        return CORRECT if canonical_id(answer.qid) == gold.qid else WRONG
    return answer.status


# --- transport ----------------------------------------------------------------------------------

class ServiceError(RuntimeError):
    """A request that did not produce an answer. `fatal`: the setup is wrong (a refused key)."""

    def __init__(self, message: str, fatal: bool = False):
        super().__init__(message)
        self.fatal = fatal


class Http:
    """One client, a minimum interval per host, and Retry-After honoured on 429 and 503."""

    RETRY = (429, 503)

    def __init__(self, client: httpx.Client, sleep: Callable[[float], None] = time.sleep, retries: int = 3):
        self.client = client
        self.sleep = sleep
        self.retries = retries
        self.intervals: dict[str, float] = {}
        self._last: dict[str, float] = {}

    def send(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        response = self._once(method, url, **kwargs)
        for attempt in range(self.retries):
            if response.status_code not in self.RETRY:
                break
            self.sleep(min(120.0, retry_after(response) or 5.0 * 2 ** attempt))
            response = self._once(method, url, **kwargs)
        return response

    def json(self, method: str, url: str, **kwargs: Any) -> Any:
        try:
            response = self.send(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise ServiceError(f"{type(exc).__name__}: {exc}") from exc
        if response.status_code >= 400:
            raise ServiceError(f"HTTP {response.status_code}: {error_text(response)}", fatal=response.status_code in (401, 403))
        try:
            return response.json()
        except ValueError as exc:
            raise ServiceError(f"HTTP {response.status_code}: not JSON") from exc

    def _once(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        host = urllib.parse.urlsplit(url).netloc
        wait = self._last.get(host, float("-inf")) + self.intervals.get(host, 0.0) - time.monotonic()
        if wait > 0:
            self.sleep(wait)
        self._last[host] = time.monotonic()
        return self.client.request(method, url, **kwargs)


def error_text(response: httpx.Response) -> str:
    """An error body on one line: the JSON message when there is one, else the text."""
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        inner = body.get("error")
        message = inner.get("message") if isinstance(inner, dict) else inner or body.get("detail") or body.get("message")
        if message:
            return " ".join(str(message).split())[:200]
    return " ".join(response.text.split())[:200]


def retry_after(response: httpx.Response) -> float | None:
    """Seconds to wait: the Retry-After header, or the demo's `retry_after_s`."""
    header = (response.headers.get("retry-after") or "").strip()
    if header.isdigit():
        return float(header)
    try:
        body = response.json()
    except ValueError:
        return None
    seconds = body.get("retry_after_s") if isinstance(body, dict) else None
    return float(seconds) if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) else None


class Cache:
    """A system's answers on disk, keyed by the request that produced them."""

    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        self.misses = 0

    @staticmethod
    def key(request: Any) -> str:
        return hashlib.sha256(json.dumps(request, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def __contains__(self, request: Any) -> bool:
        return self.key(request) in self.data

    def __getitem__(self, request: Any) -> Any:
        return self.data[self.key(request)]

    def put(self, request: Any, value: Any) -> Any:
        self.data[self.key(request)] = value
        self.misses += 1
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
        return value

    def get(self, request: Any, fetch: Callable[[], Any]) -> Any:
        """The cached answer, or `fetch()` stored; a fetch that raises stores nothing."""
        return self[request] if request in self else self.put(request, fetch())


class WikipediaIds:
    """Wikipedia article URL to Wikidata ID, through the edition's page props, redirects followed."""

    BATCH = 50

    def __init__(self, http: Http, cache: Cache):
        self.http = http
        self.cache = cache

    @staticmethod
    def article(url: str) -> tuple[str, str] | None:
        parts = urllib.parse.urlsplit(url)
        if not parts.netloc.endswith(".wikipedia.org") or not parts.path.startswith("/wiki/"):
            return None
        title = urllib.parse.unquote(parts.path.removeprefix("/wiki/")).replace("_", " ")
        return parts.netloc.split(".")[0], title

    def qids(self, urls: Iterable[str]) -> dict[str, str | None]:
        articles = {url: self.article(url) for url in set(urls)}
        todo: dict[str, set[str]] = defaultdict(set)
        for key in articles.values():
            if key is not None and list(key) not in self.cache:
                todo[key[0]].add(key[1])
        for lang, titles in todo.items():
            ordered = sorted(titles)
            for i in range(0, len(ordered), self.BATCH):
                self._fetch(lang, ordered[i:i + self.BATCH])
        return {url: (self.cache[list(key)] if key is not None else None) for url, key in articles.items()}

    def _fetch(self, lang: str, titles: list[str]) -> None:
        url = f"https://{lang}.wikipedia.org/w/api.php"
        self.http.intervals.setdefault(urllib.parse.urlsplit(url).netloc, 0.1)
        body = self.http.json("GET", url, params={"action": "query", "prop": "pageprops", "ppprop": "wikibase_item", "redirects": "1",
                                                  "titles": "|".join(titles), "format": "json", "formatversion": "2"})
        query = body.get("query") or {}
        normalized = {n["from"]: n["to"] for n in query.get("normalized", [])}
        redirects = {r["from"]: r["to"] for r in query.get("redirects", [])}
        pages = {p["title"]: (p.get("pageprops") or {}).get("wikibase_item") for p in query.get("pages", [])}
        for title in titles:
            name = normalized.get(title, title)
            self.cache.put([lang, title], pages.get(redirects.get(name, name)))


# --- systems ------------------------------------------------------------------------------------

@dataclass
class Services:
    """What systems share: the transport, a cache per system, the Wikipedia mapping."""
    http: Http
    out: Path

    def __post_init__(self) -> None:
        self.wikipedia = WikipediaIds(self.http, self.cache("wikipedia"))

    def cache(self, name: str) -> Cache:
        return Cache(self.out / "cache" / f"{name}.json")


class System:
    """A service under test: one Answer per gold mention of a document, in order."""
    name = ""
    title = ""
    needs = ""                                  # what to set when it is skipped
    languages: frozenset[str] | None = None     # None: every language
    max_chars: int | None = None                # a longer document is not supported
    max_mentions: int | None = None             # nor one with more gold mentions
    gets_spans = True                           # False: it finds its own mentions

    @classmethod
    def from_env(cls, env: Mapping[str, str], services: Services) -> System | None:
        raise NotImplementedError

    def answer(self, doc: Doc) -> list[Answer]:
        raise NotImplementedError


def resolution_row(r: Resolution) -> dict[str, Any]:
    signals = r.diagnostics.get("signals") or {}
    return {"status": r.status, "id": r.entity.id if r.entity else None, "label": r.entity.label if r.entity else None,
            "score": r.score, "reason": r.reason, "path": signals.get("path")}


def resolution_answer(row: dict[str, Any]) -> Answer:
    detail = {k: row[k] for k in ("label", "score", "reason", "path") if row.get(k) is not None}
    return Answer(LINKED, row["id"], detail=detail) if row["status"] == RESOLVED else Answer(NO_ANSWER, detail=detail)


def checked(results: list[Resolution]) -> list[dict[str, Any]]:
    failed = [r for r in results if r.status != RESOLVED and r.reason in FAILURES]
    if failed:
        raise ServiceError(f"{failed[0].reason} {failed[0].diagnostics}")
    return [resolution_row(r) for r in results]


def mentions_of(doc: Doc) -> list[Mention]:
    return [Mention(g.name, g.start, g.end) for g in doc.gold]


class ResolveAPI(System):
    name, title, needs = "resolve", "resolve() (API)", "WL_KEY"

    def __init__(self, resolver: WordLiftResolver, cache: Cache, sleep: Callable[[float], None]):
        self.resolver, self.cache, self.sleep = resolver, cache, sleep

    @classmethod
    def from_env(cls, env: Mapping[str, str], services: Services) -> System | None:
        if not env.get("WL_KEY"):
            return None
        resolver = WordLiftResolver(env["WL_KEY"], client=services.http.client, timeout=60.0)
        return cls(resolver, services.cache(cls.name), services.http.sleep)

    def answer(self, doc: Doc) -> list[Answer]:
        mentions = mentions_of(doc)
        request = [doc.text, doc.lang, [[m.text, m.start, m.end] for m in mentions]]
        return [resolution_answer(row) for row in self.cache.get(request, lambda: self._call(doc, mentions))]

    def _call(self, doc: Doc, mentions: list[Mention]) -> list[dict[str, Any]]:
        for attempt in range(3):
            results = self.resolver.resolve_many(doc.text, mentions, [[] for _ in mentions], language=doc.lang)
            limited = [r for r in results if r.reason == RATE_LIMITED]
            if not limited or attempt == 2:
                break
            self.sleep(float(limited[0].diagnostics.get("retry_after") or 30))
        return checked(results)


class ResolveDemo(System):
    name, title, needs = "resolve-demo", "resolve() (open demo)", ""
    max_chars, max_mentions = 600, 16           # the demo's limits

    def __init__(self, http: Http, cache: Cache):
        self.http, self.cache = http, cache
        http.intervals[urllib.parse.urlsplit(DEMO_URL).netloc] = 6.5   # the demo allows 10 requests a minute

    @classmethod
    def from_env(cls, env: Mapping[str, str], services: Services) -> System | None:
        return cls(services.http, services.cache(cls.name))

    def answer(self, doc: Doc) -> list[Answer]:
        mentions = mentions_of(doc)
        body = {"text": doc.text, "language": doc.lang, "mentions": [{"text": m.text, "start": m.start, "end": m.end} for m in mentions]}

        def call() -> list[dict[str, Any]]:
            raw = self.http.json("POST", DEMO_URL, json=body)
            return checked([WordLiftResolver.parse(m, raw) for m in mentions])

        return [resolution_answer(row) for row in self.cache.get(body, call)]


class WikidataFirstHit(System):
    name, title, needs = "wikidata", "Wikidata search, first hit", ""

    def __init__(self, http: Http, cache: Cache):
        self.http, self.cache = http, cache
        http.intervals.setdefault(urllib.parse.urlsplit(WIKIDATA_API).netloc, 0.1)

    @classmethod
    def from_env(cls, env: Mapping[str, str], services: Services) -> System | None:
        return cls(services.http, services.cache(cls.name))

    def answer(self, doc: Doc) -> list[Answer]:
        answers = []
        for g in doc.gold:
            params = {"action": "wbsearchentities", "search": g.name, "language": doc.lang, "uselang": doc.lang,
                      "type": "item", "limit": "1", "format": "json"}
            hits = self.cache.get(params, lambda p=params: self.http.json("GET", WIKIDATA_API, params=p).get("search") or [])
            first = hits[0] if hits else None
            answers.append(Answer(LINKED, first["id"], detail={"label": first.get("label"), "description": first.get("description")})
                           if first else Answer(NO_ANSWER))
        return answers


class EntityFishing(System):
    name, title, needs = "entity-fishing", "Entity-Fishing", "EF_URL, the base URL of a running instance (http://localhost:8090)"
    languages = frozenset("en fr de it es ar zh ru ja pt fa sv uk bn hi".split())

    def __init__(self, base_url: str, http: Http, cache: Cache):
        self.url = base_url.rstrip("/") + "/service/disambiguate"
        self.http, self.cache = http, cache

    @classmethod
    def from_env(cls, env: Mapping[str, str], services: Services) -> System | None:
        return cls(env["EF_URL"], services.http, services.cache(cls.name)) if env.get("EF_URL") else None

    def answer(self, doc: Doc) -> list[Answer]:
        # `mentions: []` with offsets in `entities`: disambiguate the given spans only.
        query = {"text": doc.text, "language": {"lang": doc.lang}, "mentions": [], "nbest": False, "customisation": "generic",
                 "entities": [{"rawName": g.name, "offsetStart": g.start, "offsetEnd": g.end} for g in doc.gold]}
        raw = self.cache.get(query, lambda: self.http.json("POST", self.url, files={"query": (None, json.dumps(query))}))
        found = [Found(e["offsetStart"], e["offsetEnd"], e.get("rawName", ""), e.get("wikidataId"),
                       {"score": e.get("confidence_score"), "wikipedia": e.get("wikipediaExternalRef")})
                 for e in raw.get("entities") or [] if "offsetStart" in e and "offsetEnd" in e]
        return align(doc.gold, found)


class Dandelion(System):
    name, title, needs = "dandelion", "Dandelion", "DANDELION_TOKEN"
    languages = frozenset({"de", "en", "es", "fr", "it", "pt", "ru"})
    gets_spans = False

    def __init__(self, token: str, http: Http, cache: Cache, wikipedia: WikipediaIds):
        self.token, self.http, self.cache, self.wikipedia = token, http, cache, wikipedia

    @classmethod
    def from_env(cls, env: Mapping[str, str], services: Services) -> System | None:
        token = env.get("DANDELION_TOKEN") or env.get("DANDELION_API_KEY")
        return cls(token, services.http, services.cache(cls.name), services.wikipedia) if token else None

    def answer(self, doc: Doc) -> list[Answer]:
        form = {"text": doc.text, "lang": doc.lang}
        raw = self.cache.get(form, lambda: self.http.json("POST", DANDELION_URL, data={**form, "token": self.token}))
        annotations = raw.get("annotations") or []
        qids = self.wikipedia.qids(a["uri"] for a in annotations if a.get("uri"))
        return align(doc.gold, [Found(a["start"], a["end"], a.get("spot", ""), qids.get(a.get("uri", "")),
                                      {"label": a.get("title"), "score": a.get("confidence"), "url": a.get("uri")})
                                for a in annotations])


class Google(System):
    name, title = "google", "Google Cloud Natural Language"
    needs = "GOOGLE_API_KEY, or GOOGLE_ACCESS_TOKEN (gcloud auth print-access-token)"
    gets_spans = False

    def __init__(self, headers: dict[str, str], http: Http, cache: Cache, wikipedia: WikipediaIds):
        self.headers, self.http, self.cache, self.wikipedia = headers, http, cache, wikipedia

    @classmethod
    def from_env(cls, env: Mapping[str, str], services: Services) -> System | None:
        if env.get("GOOGLE_API_KEY"):
            headers = {"X-Goog-Api-Key": env["GOOGLE_API_KEY"]}
        elif env.get("GOOGLE_ACCESS_TOKEN"):
            headers = {"Authorization": f"Bearer {env['GOOGLE_ACCESS_TOKEN']}"}
            if env.get("GOOGLE_CLOUD_PROJECT"):
                headers["x-goog-user-project"] = env["GOOGLE_CLOUD_PROJECT"]
        else:
            return None
        return cls(headers, services.http, services.cache(cls.name), services.wikipedia)

    def answer(self, doc: Doc) -> list[Answer]:
        # v1, not v2: only v1 returns metadata.wikipedia_url. UTF32 offsets are code points, as in Python.
        body = {"document": {"type": "PLAIN_TEXT", "content": doc.text, "language": doc.lang}, "encodingType": "UTF32"}
        raw = self.cache.get(body, lambda: self.http.json("POST", GOOGLE_URL, json=body, headers=self.headers))
        pairs = [(e, m) for e in raw.get("entities") or [] for m in e.get("mentions") or []]
        urls = {(e.get("metadata") or {}).get("wikipedia_url") for e, _ in pairs} - {None}
        qids = self.wikipedia.qids(urls)
        found = []
        for e, m in pairs:
            text = m.get("text") or {}
            start, content = text.get("beginOffset", -1), text.get("content", "")
            url = (e.get("metadata") or {}).get("wikipedia_url")
            if start >= 0:
                found.append(Found(start, start + len(content), content, qids.get(url) if url else None,
                                   {"label": e.get("name"), "url": url, "type": e.get("type"), "mention_type": m.get("type")}))
        return align(doc.gold, found)


class Azure(System):
    name, title, needs = "azure", "Azure Entity Linking", "AZURE_LANGUAGE_ENDPOINT and AZURE_LANGUAGE_KEY"
    languages = frozenset({"en", "es"})
    gets_spans = False

    def __init__(self, endpoint: str, key: str, http: Http, cache: Cache, wikipedia: WikipediaIds):
        self.url = endpoint.rstrip("/") + AZURE_PATH
        self.key, self.http, self.cache, self.wikipedia = key, http, cache, wikipedia

    @classmethod
    def from_env(cls, env: Mapping[str, str], services: Services) -> System | None:
        if not (env.get("AZURE_LANGUAGE_ENDPOINT") and env.get("AZURE_LANGUAGE_KEY")):
            return None
        return cls(env["AZURE_LANGUAGE_ENDPOINT"], env["AZURE_LANGUAGE_KEY"], services.http, services.cache(cls.name), services.wikipedia)

    def answer(self, doc: Doc) -> list[Answer]:
        body = {"kind": "EntityLinking", "parameters": {"modelVersion": "latest", "stringIndexType": "UnicodeCodePoint"},
                "analysisInput": {"documents": [{"id": "1", "language": doc.lang, "text": doc.text}]}}
        raw = self.cache.get(body, lambda: self._call(body))
        entities = raw["results"]["documents"][0].get("entities") or []
        qids = self.wikipedia.qids(e["url"] for e in entities if e.get("url"))
        return align(doc.gold, [Found(m["offset"], m["offset"] + m["length"], m.get("text", ""), qids.get(e.get("url", "")),
                                      {"label": e.get("name"), "score": m.get("confidenceScore"), "url": e.get("url")})
                                for e in entities for m in e.get("matches") or []])

    def _call(self, body: dict[str, Any]) -> Any:
        raw = self.http.json("POST", self.url, json=body, headers={"Ocp-Apim-Subscription-Key": self.key})
        results = raw.get("results") or {}
        if results.get("errors") or not results.get("documents"):
            raise ServiceError(f"Azure: {results.get('errors') or 'no documents in the response'}")
        return raw


SYSTEMS: list[type[System]] = [ResolveAPI, ResolveDemo, WikidataFirstHit, EntityFishing, Dandelion, Google, Azure]


# --- running and scoring ------------------------------------------------------------------------

def run_system(system: System, docs: list[Doc], log: Callable[[str], None]) -> list[tuple[Gold, Answer]]:
    """Every gold mention with the system's answer. Raises ServiceError when the setup is wrong:
    a refused key, the first three requests all failing, or every request failing."""
    pairs: list[tuple[Gold, Answer]] = []
    sent = failed = 0
    last: ServiceError | None = None
    for doc in docs:
        too_long = ((system.max_chars is not None and len(doc.text) > system.max_chars)
                    or (system.max_mentions is not None and len(doc.gold) > system.max_mentions))
        if too_long or (system.languages is not None and doc.lang not in system.languages):
            pairs += [(g, Answer(UNSUPPORTED)) for g in doc.gold]
            continue
        sent += 1
        try:
            answers = system.answer(doc)
        except ServiceError as exc:
            failed, last = failed + 1, exc
            if exc.fatal or failed == sent == 3:
                raise
            log(f"  {system.name}: {doc.dataset} #{doc.index} failed: {exc}")
            answers = [Answer(ERROR, detail={"error": str(exc)[:300]}) for _ in doc.gold]
        pairs += list(zip(doc.gold, answers, strict=True))
    if last is not None and failed == sent:
        raise last
    return pairs


def tally(pairs: list[tuple[Gold, Answer]]) -> dict[str, Any]:
    counts = Counter(outcome(g, a) for g, a in pairs)
    answered = counts[CORRECT] + counts[WRONG]
    precision = counts[CORRECT] / answered if answered else 0.0
    recall = counts[CORRECT] / len(pairs) if pairs else 0.0
    return {"mentions": len(pairs), "correct": counts[CORRECT], "wrong": counts[WRONG],
            "no_answer": counts[NO_ANSWER] + counts[UNSUPPORTED], "unsupported": counts[UNSUPPORTED], "errors": counts[ERROR],
            "precision": precision, "recall": recall,
            "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0}


def by_path(pairs: list[tuple[Gold, Answer]]) -> dict[str, dict[str, int]]:
    """resolve() only: answers and correct answers per decision path (`signals.path`)."""
    paths: dict[str, Counter[str]] = defaultdict(Counter)
    for g, a in pairs:
        result = outcome(g, a)
        paths[a.detail.get("path") or "none"][result] += 1
    return {path: {"answered": c[CORRECT] + c[WRONG], "correct": c[CORRECT], "declined": c[NO_ANSWER]} for path, c in sorted(paths.items())}


def head_to_head(ours: list[tuple[Gold, Answer]], theirs: list[tuple[Gold, Answer]]) -> dict[str, Any]:
    """Mention by mention, the reference system (resolve()) against another one."""
    counts: Counter[str] = Counter()
    their_wrong_by_our_path: Counter[str] = Counter()
    for (g, a), (_, b) in zip(ours, theirs, strict=True):
        mine, other = outcome(g, a), outcome(g, b)
        if mine == CORRECT and other != CORRECT:
            counts["only_ours"] += 1
        elif other == CORRECT and mine != CORRECT:
            counts["only_theirs_we_wrong" if mine == WRONG else "only_theirs_we_none"] += 1
        elif mine != CORRECT:
            counts["neither"] += 1
        if other == WRONG:
            counts["their_wrong"] += 1
            counts["their_wrong_we_fixed"] += int(mine == CORRECT)
            their_wrong_by_our_path["declined" if mine == NO_ANSWER else a.detail.get("path") or "none"] += 1
    return {**counts, "their_wrong_by_our_path": dict(their_wrong_by_our_path)}


def language_order(docs: list[Doc]) -> list[str]:
    present = {d.lang for d in docs}
    return [lang for lang in LANGUAGES if lang in present] + sorted(present - set(LANGUAGES))


def pct(x: float) -> str:
    return f"{100 * x:.1f}"


def summary_md(report: dict[str, Any], langs: list[str]) -> str:
    ran = {name: s for name, s in report["systems"].items() if s["status"] == "ran"}
    run = report["run"]
    lines = [f"# Entity linking compared, {run['date']}", "",
             f"{run['documents']} documents, {run['mentions']} gold mentions, languages {', '.join(langs)}. "
             "These are development sets, not held-out data.", "",
             "| System | Precision | Recall | F1 | Correct | Wrong | No answer |", "| --- | --- | --- | --- | --- | --- | --- |"]
    notes = []
    for s in ran.values():
        o = s["overall"]
        cells = [pct(o["precision"]), pct(o["recall"]), pct(o["f1"]), o["correct"], o["wrong"], o["no_answer"]]
        lines.append(f"| {s['title']} | " + " | ".join(map(str, cells)) + " |")
        if o["unsupported"]:
            notes.append(f"{s['title']}: {o['unsupported']} mentions it does not support (language or document size) count as no answer.")
        if o["errors"]:
            notes.append(f"{s['title']}: {o['errors']} mentions failed and count as neither; re-run to retry them before quoting this row.")
    lines += ["", *notes] if notes else []
    lines += ["", "F1 by language:", "", "| System | " + " | ".join(LANGUAGES.get(x, x) for x in langs) + " |",
              "| --- |" + " --- |" * len(langs)]
    for s in ran.values():
        cells = [pct(s["by_language"][x]["f1"]) if s["by_language"][x]["unsupported"] < s["by_language"][x]["mentions"] else "not supported"
                 for x in langs]
        lines.append(f"| {s['title']} | " + " | ".join(cells) + " |")
    for s in ran.values():
        if "paths" in s:
            lines += ["", f"{s['title']} by decision path:", "",
                      "| Path | Answered | Correct | Accuracy | Declined |", "| --- | --- | --- | --- | --- |"]
            for path, p in s["paths"].items():
                accuracy = pct(p["correct"] / p["answered"]) if p["answered"] else ""
                lines.append(f"| {path} | {p['answered']} | {p['correct']} | {accuracy} | {p['declined']} |")
    if report["head_to_head"]:
        lines += ["", f"Head to head with {ran[report['reference']]['title']}, per mention:", "",
                  "| Against | Only resolve() right | Only it right, resolve() wrong | Only it right, resolve() declined | Neither right "
                  "| Its wrong answers | Of those, resolve() right |",
                  "| --- | --- | --- | --- | --- | --- | --- |"]
        for name, h in report["head_to_head"].items():
            keys = ("only_ours", "only_theirs_we_wrong", "only_theirs_we_none", "neither", "their_wrong", "their_wrong_we_fixed")
            lines.append(f"| {ran[name]['title']} | " + " | ".join(str(h.get(k, 0)) for k in keys) + " |")
    others = {name: s for name, s in report["systems"].items() if s["status"] != "ran"}
    if others:
        lines += ["", "Not run:", ""] + [f"- {s['title']}: {s['note']}" for s in others.values()]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None, client: httpx.Client | None = None,
         sleep: Callable[[float], None] = time.sleep) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--datasets", type=Path, default=Path("datasets"), help="folder of <set>_gold.json files")
    ap.add_argument("--out", type=Path, default=Path("results"), help="where the outputs and the answer cache go")
    ap.add_argument("--systems", default="", help="comma-separated names; default: every configured system")
    ap.add_argument("--limit", type=int, default=0, help="only the first N documents of each set (a smoke test)")
    args = ap.parse_args(argv)
    env = os.environ if env is None else env
    log = lambda line: print(line, file=sys.stderr)

    wanted = {s.strip() for s in args.systems.split(",") if s.strip()}
    unknown = wanted - {s.name for s in SYSTEMS}
    if unknown:
        ap.error(f"unknown systems: {', '.join(sorted(unknown))}")
    docs = load_docs(args.datasets, args.limit)
    own_client = client is None
    client = client or httpx.Client(timeout=60.0, headers={"User-Agent": USER_AGENT})
    services = Services(Http(client, sleep), args.out)
    langs = language_order(docs)
    datasets = sorted({d.dataset for d in docs})
    report: dict[str, Any] = {
        "run": {"date": date.today().isoformat(), "documents": len(docs), "mentions": sum(len(d.gold) for d in docs),
                "datasets": {name: hashlib.sha256((args.datasets / f"{name}_gold.json").read_bytes()).hexdigest() for name in datasets}},
        "systems": {}, "head_to_head": {}, "reference": None}
    results: dict[str, list[tuple[Gold, Answer]]] = {}
    try:
        for cls in SYSTEMS:
            if wanted and cls.name not in wanted:
                continue
            entry: dict[str, Any] = {"title": cls.title}
            report["systems"][cls.name] = entry
            system = cls.from_env(env, services)
            if system is None:
                entry.update(status="skipped", note=f"set {cls.needs}")
                log(f"{cls.name}: skipped, set {cls.needs}")
                continue
            log(f"{cls.name}: {len(docs)} documents")
            try:
                pairs = run_system(system, docs, log)
            except ServiceError as exc:
                entry.update(status="failed", note=str(exc)[:300])
                log(f"{cls.name}: failed, {exc}")
                continue
            results[cls.name] = pairs
            entry.update(status="ran", gets_spans=system.gets_spans, overall=tally(pairs),
                         by_language={x: tally([p for p in pairs if p[0].lang == x]) for x in langs})
            if isinstance(system, (ResolveAPI, ResolveDemo)):
                entry["paths"] = by_path(pairs)
    finally:
        if own_client:
            client.close()

    reference = next((name for name in ("resolve", "resolve-demo") if name in results), None)
    report["reference"] = reference
    if reference:
        report["head_to_head"] = {name: head_to_head(results[reference], pairs) for name, pairs in results.items()
                                  if name not in ("resolve", "resolve-demo")}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with (args.out / "rows.jsonl").open("w", encoding="utf-8") as f:
        for name, pairs in results.items():
            for g, a in pairs:
                row = {"system": name, "set": g.dataset, "doc": g.doc, "lang": g.lang, "mention": g.name, "start": g.start, "end": g.end,
                       "gold": g.qid, "outcome": outcome(g, a), "qid": a.qid, "found": a.found, **a.detail}
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    (args.out / "summary.md").write_text(summary_md(report, langs), encoding="utf-8")
    log(f"wrote {args.out / 'summary.md'}, report.json, rows.jsonl")
    return 0


if __name__ == "__main__":
    sys.exit(main())
