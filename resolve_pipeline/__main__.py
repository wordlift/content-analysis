"""`python -m resolve_pipeline "text"`: resolve a sentence with WordLift's engine.

    export WL_KEY=...
    python -m resolve_pipeline "Apple opened a new store in Rome." --language en
    python -m resolve_pipeline "Apple ..." --mention Apple --candidate wd:Q312 --candidate wd:Q89
    python -m resolve_pipeline "..." --dataset-uri wordlift://dataset/me,wikidata://public
    python -m resolve_pipeline "..." --dataset my-entities.json

Without --mention the engine detects mentions; without --candidate it retrieves
against the dataset (Wikidata by default; see docs/your-own-data.md for your
WordLift graph, an inline vocabulary and local-first). Output is one line per mention; exit code is 0 when the call
succeeded, 1 when the engine rejected the request (422: bad span, unknown identity form, unsupported dataset),
2 when the engine could not be reached.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import httpx

from .resolver import WordLiftResolver, is_user_world
from .types import RESOLVER_UNAVAILABLE

INVALID_REQUEST = "invalid_request"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m resolve_pipeline", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("text")
    ap.add_argument("--language", default=None, help="ISO 639-1; detected when omitted")
    ap.add_argument("--mention", help="resolve only this surface form (first occurrence)")
    ap.add_argument("--candidate", action="append", default=[], help="identity to offer for --mention (repeatable)")
    ap.add_argument("--dataset-uri", default=None,
                    help="world(s) to resolve against: wikidata://public, wordlift://dataset/me, or an ordered list")
    ap.add_argument("--dataset", metavar="FILE",
                    help="JSON file with an inline vocabulary: a list of entities or {\"entities\": [...]}")
    ap.add_argument("--include", action="append", default=[], choices=["candidates", "evidence"])
    ap.add_argument("--json", action="store_true", help="print the raw response")
    ap.add_argument("--key", default=os.environ.get("WL_KEY"), help="WordLift API key (default: $WL_KEY)")
    ap.add_argument("--base-url", default=WordLiftResolver.DEFAULT_BASE_URL)
    args = ap.parse_args(argv)
    if not args.key:
        ap.error("no API key: set WL_KEY or pass --key")

    body: dict = {"text": args.text, "language": args.language, "include": args.include}
    if args.dataset_uri:
        body["dataset_uri"] = args.dataset_uri
    if args.dataset:
        with open(args.dataset, encoding="utf-8") as fh:
            entities = json.load(fh)
        body["dataset"] = {"entities": entities} if isinstance(entities, list) else entities
    if args.candidate and is_user_world(args.dataset_uri or "", body.get("dataset")):
        ap.error("--candidate cannot be combined with --dataset or a user world in --dataset-uri: "
                 "the dataset is the candidate world (the engine answers 422)")
    if args.candidate and not args.mention:
        ap.error("--candidate needs --mention")
    if args.mention:
        start = args.text.find(args.mention)
        if start < 0:
            ap.error(f"--mention {args.mention!r} not found in text")
        span = {"text": args.mention, "start": start, "end": start + len(args.mention)}
        if args.candidate:
            span["candidates"] = [{"id": c} for c in args.candidate]
        body["mentions"] = [span]
    try:
        resp = httpx.post(f"{args.base_url.rstrip('/')}/v1/resolve", json=body, timeout=60,
                          headers={"Authorization": f"Key {args.key}", "Content-Type": "application/json"})
        if resp.status_code == 422:
            # The engine answered: the request is wrong, the engine is not unavailable.
            try:
                detail = resp.json().get("detail")
            except (ValueError, AttributeError):
                detail = resp.text[:200]
            print(f"{INVALID_REQUEST}: {json.dumps(detail, ensure_ascii=False)}", file=sys.stderr)
            return 1
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as exc:
        print(f"{RESOLVER_UNAVAILABLE}: HTTP {exc.response.status_code} {exc.response.text[:200]}", file=sys.stderr)
        return 2
    except (httpx.HTTPError, ValueError) as exc:
        print(f"{RESOLVER_UNAVAILABLE}: {type(exc).__name__}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    for m in data.get("mentions", []):
        e = m.get("entity") or {}
        world = f"  via {m['dataset_uri']}" if m.get("dataset_uri") and "," in (body.get("dataset_uri") or "") else ""
        if m["status"] == "resolved":
            score = m.get("score")
            shown = f"{score:.2f}" if isinstance(score, (int, float)) else "n/a"
            print(f"{m['text']:<16} {'resolved':<11} {e.get('id', ''):<12} {e.get('label', ''):<25} score {shown}{world}")
        else:
            print(f"{m['text']:<16} {'unresolved':<11} {'':<12} {m.get('reason', '')}{world}")
    print(f"language {data.get('language')} · {data.get('engine')} · {data.get('processing_time_ms')} ms", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
