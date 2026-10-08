"""extract -> retrieve -> resolve over one document."""
from __future__ import annotations

from typing import Any, Callable

from .resolver import Resolver
from .types import Candidate, Context, Mention, Resolution

Retriever = Callable[[Mention, str], list[Candidate]]


def context_window(text: str, mention: Mention, radius: int = 400) -> Context:
    """The text around a mention, with the offset that keeps the mention's span valid."""
    start = max(0, mention.start - radius)
    return Context(text[start: mention.end + radius], start)


def run(
    text: str,
    *,
    retrieve: Retriever,
    resolver: Resolver,
    mentions: list[Mention] | None = None,
    language: str = "",
    model: Any = None,
    labels: list[str] | None = None,
    threshold: float = 0.5,
    context_radius: int = 400,
) -> list[Resolution]:
    """Resolve every mention in `text`.

    Pass `mentions` to skip extraction (for example spans from your own NER or
    gold labels). Otherwise `model` must be a loaded GLiNER model from
    `extract.load_model()`.
    """
    if mentions is None:
        if model is None:
            raise ValueError("either `mentions` or a loaded GLiNER `model` is required")
        from .extract import extract
        mentions = extract(model, text, labels, threshold, language)
    out: list[Resolution] = []
    for mention in mentions:
        context = context_window(text, mention, context_radius)
        candidates = retrieve(mention, context)
        out.append(resolver.resolve(mention, context, candidates, language=language))
    return out
