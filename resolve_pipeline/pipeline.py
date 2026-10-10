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


#: The engine's request text limit (docs/openapi.resolve.json). Longer documents
#: are sent in chunks cut at paragraph or whitespace boundaries, never through a
#: mention; offsets stay document-relative in the results.
ENGINE_TEXT_LIMIT = 100_000


def chunk_document(text: str, mentions: list[Mention], limit: int = ENGINE_TEXT_LIMIT) -> list[tuple[int, int]]:
    """Character ranges that cover `text`, each at most `limit` long, none cutting a mention."""
    if len(text) <= limit:
        return [(0, len(text))]
    spans = sorted((m.start, m.end) for m in mentions)
    chunks: list[tuple[int, int]] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + limit)
        if end < len(text):
            cut = max(text.rfind("\n", start, end), text.rfind(" ", start, end))
            if cut > start:
                end = cut
            # never cut through a mention: step back to before the first mention that straddles the cut
            for ms, me in spans:
                if ms < end < me:
                    back = text.rfind(" ", start, ms)
                    end = back if back > start else ms
                    break
        chunks.append((start, end))
        start = end
    return chunks


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

    Candidates are retrieved per mention with its context window, as before.
    Resolution goes to the resolver once per document when it offers
    `resolve_many` (WordLiftResolver: one request carrying every span and the
    inline vocabulary once, instead of one request per mention), and
    mention by mention otherwise, so a custom Resolver written against the
    single-mention protocol keeps working. A document longer than the
    engine's text limit is sent in chunks that never cut a mention. Results
    keep the order of `mentions` and their document offsets.

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
    out: list[Resolution | None] = [None] * len(mentions)
    valid: list[int] = []
    for i, mention in enumerate(mentions):
        if text[mention.start:mention.end] != mention.text:
            if on_invalid_mention == "raise":
                raise ValueError(f"mention {mention.text!r} [{mention.start}, {mention.end}) does not lie in the text as written")
            out[i] = unresolved(mention, INVALID_MENTION, detail="mention does not lie in the text as written")
        else:
            valid.append(i)
    candidates = {i: retrieve(mentions[i], context_window(text, mentions[i], context_radius)) for i in valid}
    many = getattr(resolver, "resolve_many", None)
    if many is None:
        for i in valid:
            out[i] = resolver.resolve(mentions[i], context_window(text, mentions[i], context_radius), candidates[i], language=language)
        return [r for r in out if r is not None]
    for c_start, c_end in chunk_document(text, [mentions[i] for i in valid]):
        chunk_text = Context(text[c_start:c_end], c_start)
        idx = [i for i in valid if c_start <= mentions[i].start and mentions[i].end <= c_end]
        if not idx:
            continue
        results = many(chunk_text, [mentions[i] for i in idx], [candidates[i] for i in idx], language=language)
        if len(results) != len(idx):
            raise RuntimeError(f"resolver returned {len(results)} results for {len(idx)} mentions")
        for i, r in zip(idx, results, strict=True):
            out[i] = r
    return [r for r in out if r is not None]
