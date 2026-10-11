"""extract -> retrieve -> resolve over one document."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from .resolver import BatchResolver, Resolver, as_batch, complete, mention_not_in_text
from .types import INVALID_MENTION, Candidate, Context, Mention, Resolution, context_window, unresolved

__all__ = ["run", "context_window", "Retriever"]

Retriever = Callable[[Mention, str], list[Candidate]]


def run(
    text: str,
    *,
    retrieve: Retriever,
    resolver: Resolver | BatchResolver,
    mentions: list[Mention] | None = None,
    language: str = "",
    model: Any = None,
    labels: list[str] | None = None,
    threshold: float = 0.5,
    context_radius: int = 400,
    on_invalid_mention: Literal["raise", "unresolved"] = "raise",
) -> list[Resolution]:
    """Resolve every mention in `text`.

    Pass `mentions` to skip extraction (for example spans from your own NER or
    gold labels). Otherwise `model` must be a loaded GLiNER model from
    `extract.load_model()`.

    Candidates are retrieved per mention with `context_radius` characters
    either side of it. Resolution then happens once per document through the
    batch protocol (`as_batch`): `WordLiftResolver` sends every span and the
    inline vocabulary in one request per 200 mentions and per 100,000
    characters, and a resolver that only offers `resolve()` is adapted to be
    called mention by mention with the same window. Results keep the order of
    `mentions` and their document offsets.

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
    document = Context(text)
    out: list[Resolution | None] = [None] * len(mentions)
    valid: list[int] = []
    for i, mention in enumerate(mentions):
        if document.local_span(mention) is None:
            if on_invalid_mention == "raise":
                raise mention_not_in_text(mention)
            out[i] = unresolved(mention, INVALID_MENTION, detail="mention does not lie in the text as written")
        else:
            valid.append(i)
    candidates = [retrieve(mentions[i], context_window(document, mentions[i], context_radius)) for i in valid]
    results = as_batch(resolver, context_radius).resolve_many(document, [mentions[i] for i in valid], candidates, language=language)
    if len(results) != len(valid):
        raise RuntimeError(f"resolver returned {len(results)} results for {len(valid)} mentions")
    for i, r in zip(valid, results, strict=True):
        out[i] = r
    return complete(out)
