"""extract -> retrieve -> resolve over one document."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .resolver import Resolver
from .types import INVALID_MENTION, Candidate, Context, Mention, Resolution, unresolved

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
    on_invalid_mention: str = "raise",
) -> list[Resolution]:
    """Resolve every mention in `text`.

    Pass `mentions` to skip extraction (for example spans from your own NER or
    gold labels). Otherwise `model` must be a loaded GLiNER model from
    `extract.load_model()`.

    A mention that does not lie in `text` as written is a caller error: with
    `on_invalid_mention="raise"` (the default) `run` raises ValueError before
    any request is sent; with `"unresolved"` the mention comes back as
    `unresolved` with reason `invalid_mention` and the batch carries on, which
    is what callers replaying gold labels or an external NER usually want.
    """
    if on_invalid_mention not in ("raise", "unresolved"):
        raise ValueError("on_invalid_mention must be 'raise' or 'unresolved'")
    if mentions is None:
        if model is None:
            raise ValueError("either `mentions` or a loaded GLiNER `model` is required")
        from .extract import extract
        mentions = extract(model, text, labels, threshold, language)
    out: list[Resolution] = []
    for mention in mentions:
        if text[mention.start:mention.end] != mention.text:
            if on_invalid_mention == "raise":
                raise ValueError(f"mention {mention.text!r} [{mention.start}, {mention.end}) does not lie in the text as written")
            out.append(unresolved(mention, INVALID_MENTION, detail="mention does not lie in the text as written"))
            continue
        context = context_window(text, mention, context_radius)
        candidates = retrieve(mention, context)
        out.append(resolver.resolve(mention, context, candidates, language=language))
    return out
