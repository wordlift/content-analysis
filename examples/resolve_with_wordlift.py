"""Resolve mentions against your own candidate world with WordLift's engine.

    export WL_KEY=...            # WordLift API key
    python examples/resolve_with_wordlift.py

The retriever below stands in for a lookup in your knowledge graph or
vocabulary: it returns the identities you consider possible for a mention.
The engine then answers with one of them, or `unresolved`.
"""
import os

from resolve_pipeline import Candidate, Mention, WordLiftResolver, run

TEXT = "Apple opened a new store in Rome, a short walk from the Trevi Fountain."
MENTIONS = [Mention("Apple", 0, 5, "Organization"), Mention("Rome", 28, 32, "City"),
            Mention("Trevi Fountain", 56, 70, "Place")]

MY_GRAPH = {
    "Apple": [Candidate("wd:Q312", "Apple Inc.", "American technology company"),
              Candidate("wd:Q89", "apple", "fruit of the apple tree"),
              Candidate("wd:Q213710", "Apple Records", "British record label")],
    "Rome": [Candidate("wd:Q220", "Rome", "capital of Italy"),
             Candidate("wd:Q3945", "Rome, Georgia", "city in the United States")],
    # No candidates for the fountain: the engine retrieves against Wikidata itself.
}


def retrieve(mention: Mention, context: str) -> list[Candidate]:
    return MY_GRAPH.get(mention.text, [])


def main() -> None:
    key = os.environ.get("WL_KEY")
    if not key:
        raise SystemExit("set WL_KEY to your WordLift API key")
    resolver = WordLiftResolver(api_key=key)
    for r in run(TEXT, retrieve=retrieve, resolver=resolver, mentions=MENTIONS, language="en"):
        if r.resolved:
            print(f"{r.mention.text:15} -> {r.entity.id:12} {r.entity.label}  (score {r.score})")
        else:
            print(f"{r.mention.text:15} -> unresolved ({r.reason})")


if __name__ == "__main__":
    main()
