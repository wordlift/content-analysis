"""Resolve against your own data: your WordLift graph, an inline vocabulary,
and local-first with Wikidata as the fallback. See docs/your-own-data.md.

    export WL_KEY=...            # WordLift API key
    python examples/resolve_with_your_data.py
"""
import os

from resolve_pipeline import Mention, WordLiftResolver, run

TEXT = "Andrea Volpini founded WordLift in Rome; the company works on Semantic SEO."
# The engine's recognizer proposes names, not concepts: pass the concept's span yourself.


def span(surface: str, label: str) -> Mention:
    start = TEXT.index(surface)
    return Mention(surface, start, start + len(surface), label)


MENTIONS = [span("Andrea Volpini", "Person"), span("WordLift", "Organization"),
            span("Rome", "Place"), span("Semantic SEO", "Thing")]

VOCABULARY = [
    {"id": "https://acme.example/kg/wordlift", "name": "WordLift", "aliases": ["WordLift Srl"],
     "description": "Company building knowledge graphs for SEO", "types": ["Organization"],
     "same_as": ["http://www.wikidata.org/entity/Q31998763"]},
    {"id": "https://acme.example/kg/semantic-seo", "name": "Semantic SEO",
     "description": "Search optimization based on entities and meaning", "types": ["Thing"]},
]


def show(title: str, resolver: WordLiftResolver) -> None:
    print(f"\n{title}")
    for r in run(TEXT, retrieve=lambda m, c: [], resolver=resolver, mentions=MENTIONS, language="en"):
        world = r.diagnostics.get("dataset_uri", "")
        if r.resolved:
            public = [s for s in r.entity.same_as if "wikidata" in s or "dbpedia" in s]
            print(f"  {r.mention.text:15} -> {r.entity.id}  via {world}  same_as={public}")
        else:
            print(f"  {r.mention.text:15} -> unresolved ({r.reason})  via {world}")


def main() -> None:
    key = os.environ.get("WL_KEY")
    if not key:
        raise SystemExit("set WL_KEY to your WordLift API key")
    show("1. Your WordLift graph (derived from the key)",
         WordLiftResolver(api_key=key, dataset_uri=WordLiftResolver.WORDLIFT_GRAPH))
    show("2. An inline vocabulary", WordLiftResolver(api_key=key, dataset=VOCABULARY))
    show("3. Local entity first, Wikidata for the rest",
         WordLiftResolver(api_key=key, dataset_uri=WordLiftResolver.LOCAL_FIRST))


if __name__ == "__main__":
    main()
