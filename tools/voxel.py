#!/usr/bin/env python3
"""Convert VoxEL to the gold format tools/compare.py reads.

    python tools/voxel.py --out results/voxel                 # strict version, downloaded
    python tools/compare.py --datasets results/voxel --out results/voxel-run

VoxEL (Rosales-Méndez, Hogan and Poblete, "VoxEL: A Benchmark Dataset for
Multilingual Entity Linking", ISWC 2018; CC BY 4.0;
https://users.dcc.uchile.cl/~hrosales/VoxEL.html) annotates the same 15
VoxEurop news articles in German, English, Spanish, French and Italian against
Wikipedia. The strict version keeps persons, organisations and places (204
annotations per language), the relaxed one 674. Neither WordLift nor the other
services built it, which is why it is the comparison's held-out test.

Each article becomes one document with its mentions as spans:
{"text", "lang", "source", "mentions": [{"text", "start", "end", "qid"}]}.
Offsets are the sentence's start in the article plus the annotation's start
in the sentence, and every span is checked against the text. Wikipedia
links map to Wikidata through the edition's page props (redirects followed),
the article's own language first, then English. An annotation with no
Wikidata item is left out and counted: in the 2018 release these are the
three per language VoxEL marks NotInLexico (no Wikipedia article).
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path

import httpx
from compare import USER_AGENT, Cache, Http, WikipediaIds

VOXEL_ZIP = "https://users.dcc.uchile.cl/~hrosales/VoxEL.zip"
LANGS = ("de", "en", "es", "fr", "it")

_BLOCK = re.compile(r"^<([^>]+)>\s*\n(.*?)(?=^<[^>]+>\s*\n|\Z)", re.S | re.M)
_STRING = re.compile(r'nif:isString\s+"""(.*?)"""', re.S)
_ANCHOR = re.compile(r'nif:anchorOf\s+"""(.*?)"""', re.S)
_INDEX = re.compile(r'nif:(beginIndex|endIndex)\s+"(\d+)"')
_REF = re.compile(r"nif:(referenceContext|broaderContext)\s+<([^>]+)>")
_SOURCE = re.compile(r"nif:sourceUrl\s+<([^>]+)>")
_LINK = re.compile(r"itsrdf:taIdentRef\s+<([^>]+)>")


def parse(ttl: str, lang: str) -> list[dict]:
    """Articles with their annotations as (start, end, anchor, [Wikipedia URLs]), offsets in the article."""
    articles: dict[str, dict] = {}
    sentences: dict[str, tuple[str, int]] = {}
    annotations = []
    for uri, body in _BLOCK.findall(ttl):
        idx = {k: int(v) for k, v in _INDEX.findall(body)}
        refs = dict(_REF.findall(body))
        if _SOURCE.search(body):
            articles[uri] = {"text": _STRING.search(body).group(1), "lang": lang, "source": _SOURCE.search(body).group(1), "spans": []}
        elif "broaderContext" in refs:
            sentences[uri] = (refs["broaderContext"], idx["beginIndex"])
        elif _ANCHOR.search(body) and "referenceContext" in refs:
            annotations.append((refs["referenceContext"], idx["beginIndex"], idx["endIndex"], _ANCHOR.search(body).group(1),
                                _LINK.findall(body)))
    for sentence, begin, end, anchor, links in annotations:
        article_uri, offset = sentences[sentence]
        article = articles[article_uri]
        start, stop = offset + begin, offset + end
        if article["text"][start:stop] != anchor:
            raise ValueError(f"{lang}: {anchor!r} is not at {start}:{stop} in {article_uri}")
        article["spans"].append((start, stop, anchor, links))
    return list(articles.values())


def ordered_links(links: list[str], lang: str) -> list[str]:
    """The annotation's links, its own language's Wikipedia first, then English."""
    own = [u for u in links if f"//{lang}.wikipedia.org/" in u]
    english = [u for u in links if "//en.wikipedia.org/" in u]
    return own + english


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, required=True, help="where <voxel-lang>_gold.json files go")
    ap.add_argument("--zip", type=Path, help="a local VoxEL.zip instead of the download")
    ap.add_argument("--version", choices=["strict", "relaxed"], default="strict")
    args = ap.parse_args(argv)
    if args.zip:
        raw = args.zip.read_bytes()
    else:
        request = urllib.request.Request(VOXEL_ZIP, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=120) as response:
            raw = response.read()
    prefix = "s" if args.version == "strict" else "r"
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        texts = {lang: z.read(f"{prefix}VoxEL-{lang}.ttl").decode("utf-8") for lang in LANGS}

    args.out.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=60.0, headers={"User-Agent": USER_AGENT}) as client:
        ids = WikipediaIds(Http(client), Cache(args.out / "cache" / "wikipedia.json"))
        dropped: dict[str, int] = defaultdict(int)
        for lang in LANGS:
            articles = parse(texts[lang], lang)
            urls = {u for a in articles for *_, links in a["spans"] for u in ordered_links(links, lang)}
            qids = ids.qids(urls)
            cases = []
            for a in articles:
                mentions = []
                for start, stop, anchor, links in sorted(a["spans"]):
                    qid = next((qids[u] for u in ordered_links(links, lang) if qids.get(u)), None)
                    if qid:
                        mentions.append({"text": anchor, "start": start, "end": stop, "qid": qid})
                    else:
                        dropped[lang] += 1
                        print(f"  {lang}: dropped {anchor!r} {links}", file=sys.stderr)
                cases.append({"text": a["text"], "lang": lang, "source": a["source"], "mentions": mentions})
            path = args.out / f"voxel-{lang}_gold.json"
            path.write_text(json.dumps(cases, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            n = sum(len(c["mentions"]) for c in cases)
            print(f"{lang}: {len(cases)} articles, {n} mentions, {dropped[lang]} without a Wikidata item -> {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
