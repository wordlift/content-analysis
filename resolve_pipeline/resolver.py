"""The resolve() seam: a protocol, an open reference implementation, and the
client for WordLift's engine.

A resolver answers one question for one mention: which of the supplied
candidates, if any, is the identity this mention refers to in this context.
Returning `unresolved` is a valid answer.
"""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, Protocol, runtime_checkable

import httpx

from .types import (
    LOW_RELEVANCE,
    NO_CANDIDATES,
    NO_SUITABLE_CANDIDATE,
    PROTOCOL_ERROR,
    RATE_LIMITED,
    RESOLVER_UNAVAILABLE,
    Candidate,
    Context,
    Entity,
    Mention,
    Resolution,
    context_window,
    resolved,
    unresolved,
)

PUBLIC_WORLD = "wikidata://public"
WORDLIFT_GRAPH = "wordlift://dataset/me"
INLINE = "inline"


def worlds(dataset_uri: str) -> list[str]:
    return [w.strip() for w in (dataset_uri or "").split(",") if w.strip()]


def is_user_world(dataset_uri: str, dataset: Any = None) -> bool:
    """True when the dataset is the candidate world and per-mention candidates must not be sent."""
    return dataset is not None or any(w in (WORDLIFT_GRAPH, INLINE) for w in worlds(dataset_uri))


class AuthorizationError(ValueError):
    """The engine refused the key (401 or 403): no mention of this batch can be resolved."""

    def __init__(self, status: int, detail: Any):
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


class InvalidRequestError(ValueError):
    """The engine rejected the request (422): bad span, unknown identity form,
    candidates with a user dataset, a malformed inline dataset. The request is
    wrong, the engine is not unavailable; `detail` is the engine's explanation."""

    def __init__(self, detail: Any):
        super().__init__(f"resolve() rejected the request: {detail}")
        self.detail = detail


def mention_not_in_text(mention: Mention) -> ValueError:
    """The caller's error for a mention whose span does not hold its text; raised before any request."""
    return ValueError(f"mention {mention.text!r} [{mention.start}, {mention.end}) does not lie in the text as written")


def complete(results: list[Resolution | None]) -> list[Resolution]:
    """Every mention has its Resolution, or the resolver broke the protocol; a hole is never dropped silently."""
    missing = [i for i, r in enumerate(results) if r is None]
    if missing:
        raise RuntimeError(f"no resolution for mentions {missing}")
    return [r for r in results if r is not None]


def local_span(mention: Mention, context: str) -> tuple[int, int]:
    """The mention's span in the text sent; a mention that is not there as written is the caller's error."""
    local = Context.of(context).local_span(mention)
    if local is None:
        raise mention_not_in_text(mention)
    return local


@runtime_checkable
class Resolver(Protocol):
    def resolve(
        self,
        mention: Mention,
        context: str,
        candidates: list[Candidate],
        *,
        language: str = "",
    ) -> Resolution: ...


@runtime_checkable
class BatchResolver(Protocol):
    """A resolver that decides all mentions of one text in one go.

    `candidates[i]` belongs to `mentions[i]`; the result has one Resolution per
    mention, in the same order, with explicit `unresolved` outcomes. `text` may
    be a Context (a chunk of a longer document) whose offset the mentions'
    spans are relative to.
    """

    def resolve_many(
        self,
        text: str,
        mentions: list[Mention],
        candidates: list[list[Candidate]],
        *,
        language: str = "",
    ) -> list[Resolution]: ...


class PerMention:
    """A single-mention Resolver seen through the batch protocol.

    Each mention is resolved on its own, with the context window of
    `context_radius` characters either side of it, which is what `run()` sent
    before resolution was batched. `as_batch()` applies it to any resolver that
    does not offer `resolve_many` itself.
    """

    def __init__(self, resolver: Resolver, context_radius: int = 400):
        self.resolver = resolver
        self.context_radius = context_radius

    def resolve(self, mention: Mention, context: str, candidates: list[Candidate], *, language: str = "") -> Resolution:
        return self.resolver.resolve(mention, context, candidates, language=language)

    def resolve_many(
        self, text: str, mentions: list[Mention], candidates: list[list[Candidate]], *, language: str = ""
    ) -> list[Resolution]:
        return [self.resolver.resolve(m, context_window(text, m, self.context_radius), c, language=language)
                for m, c in zip(mentions, candidates, strict=True)]


def as_batch(resolver: Resolver | BatchResolver, context_radius: int = 400) -> BatchResolver:
    """The resolver itself when it batches, otherwise the per-mention adapter."""
    if isinstance(resolver, BatchResolver):
        return resolver
    return PerMention(resolver, context_radius)


class ArgmaxResolver:
    """Open reference resolver: take the best-scored candidate when it clears a
    score floor and a margin over the runner-up, otherwise return NIL.

    This is the "argmax over candidates" baseline the pipeline can run without
    any external service. It has no notion of evidence beyond the retriever's
    own score, so it should be read as a floor, not a target.
    """

    def __init__(self, min_score: float = 0.5, min_margin: float = 0.0):
        self.min_score = min_score
        self.min_margin = min_margin

    def resolve(self, mention: Mention, context: str, candidates: list[Candidate], *, language: str = "") -> Resolution:
        # A score that is not a finite number is no score: it would pass every
        # comparison-based gate (NaN compares false to everything).
        candidates = [c for c in candidates if c.score is None or math.isfinite(c.score)]
        if not candidates:
            return unresolved(mention, NO_CANDIDATES)
        ranked = sorted(candidates, key=lambda c: c.score if c.score is not None else float("-inf"), reverse=True)
        top = ranked[0]
        if top.score is None or top.score < self.min_score:
            return unresolved(mention, LOW_RELEVANCE, top_id=top.id, top_score=top.score)
        runner_up = ranked[1].score if len(ranked) > 1 and ranked[1].score is not None else None
        if runner_up is not None and top.score - runner_up < self.min_margin:
            return unresolved(mention, NO_SUITABLE_CANDIDATE, top_id=top.id, margin=top.score - runner_up)
        return resolved(mention, Entity(top.id, top.label, top.types), score=top.score)


def chunk_document(text: str, mentions: list[Mention], limit: int) -> list[tuple[int, int]]:
    """Character ranges that cover `text`, each at most `limit` long, none cutting a mention.

    A chunk ends at the last newline or space before the limit, and earlier
    when that would fall inside a mention. Ranges are relative to `text`.
    """
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
            for ms, me in spans:              # never cut through a mention
                if ms < end < me:
                    back = text.rfind(" ", start, ms)
                    end = back if back > start else ms
                    break
        chunks.append((start, end))
        start = end
    return chunks


@dataclass(frozen=True)
class Problem:
    """What an error response says, read once: the status, the stable `code`
    of an RFC 9457 body when the engine sent one, the human-readable detail,
    and the `Retry-After` header when there is one."""

    status: int
    detail: Any
    code: str | None = None
    retry_after: str | None = None

    @classmethod
    def of(cls, response: httpx.Response) -> Problem:
        try:
            body = response.json()
        except ValueError:
            body = None
        if isinstance(body, dict):
            detail = body.get("detail") or body.get("title") or body
            code = body.get("code") if isinstance(body.get("code"), str) else None
        else:
            detail = body if body is not None else response.text[:200]
            code = None
        return cls(response.status_code, detail, code, response.headers.get("Retry-After"))

    def diagnostics(self) -> dict[str, Any]:
        out: dict[str, Any] = {"status": self.status}
        if self.code is not None:
            out["code"] = self.code
        if self.retry_after is not None:
            out["retry_after"] = self.retry_after
        return out


class WordLiftResolver:
    """Client for WordLift's `POST /v1/resolve` (see docs/resolve-contract.md).

    One request carries a text, its mention spans and, with `candidates`, the
    caller's candidates; the engine returns the supported identity or
    `unresolved` per mention. Transport failures become `unresolved` with
    reason `resolver_unavailable` rather than a guessed identity: a wrong link
    costs more than no link.
    """

    DEFAULT_BASE_URL = "https://resolve.wordlift.io"
    WORDLIFT_GRAPH = WORDLIFT_GRAPH
    #: Your entities first, Wikidata for everything else.
    LOCAL_FIRST = f"{WORDLIFT_GRAPH},{PUBLIC_WORLD}"

    #: Mentions per request. The engine resolves a document's mentions in one
    #: pass; this bounds a single request's work and response size.
    MAX_MENTIONS_PER_REQUEST = 200
    #: The engine's limit on `text` (docs/openapi.resolve.json). A longer text
    #: is sent in chunks that never cut a mention; results keep document offsets.
    MAX_TEXT_CHARS = 100_000

    #: Status to outcome. A caller error raises and stops the batch; the engine
    #: or the gateway being unable to serve becomes an explicit `unresolved`.
    RAISES: dict[int, Callable[[Problem], Exception]] = {
        422: lambda p: InvalidRequestError(p.detail),
        401: lambda p: AuthorizationError(p.status, p.detail),
        403: lambda p: AuthorizationError(p.status, p.detail),
    }
    REASONS: dict[int, str] = {429: RATE_LIMITED}
    DEFAULT_REASON = RESOLVER_UNAVAILABLE

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        dataset_uri: str = "wikidata://public",
        timeout: float = 30.0,
        engine_retrieval: bool = True,
        dataset: dict[str, Any] | list[dict[str, Any]] | None = None,
        client: httpx.Client | None = None,
    ):
        """The world identities come from is chosen once, here.

        `dataset_uri="wikidata://public"` (default): the engine's knowledge base.
        `dataset_uri=WordLiftResolver.WORDLIFT_GRAPH`: the caller's own WordLift
        knowledge graph, read by the engine with the same key.
        `dataset_uri=WordLiftResolver.LOCAL_FIRST`: your graph first, Wikidata for
        the rest; `Resolution.diagnostics["dataset_uri"]` says which world answered.
        `dataset=[{id, name, aliases, description, types, same_as}, ...]` (or
        `{"entities": [...]}`): an inline vocabulary sent with every request; the
        engine answers with one of these ids or `unresolved`.

        `engine_retrieval` decides what an empty candidate list means: True
        (default) omits `candidates` so the engine retrieves against the
        dataset; False answers `no_candidates` locally. With a user dataset the
        dataset is the candidate world and per-mention candidates are not sent.
        """
        self.base_url = base_url.rstrip("/")
        self.dataset = {"entities": dataset} if isinstance(dataset, list) else dataset
        self.dataset_uri = "inline" if self.dataset is not None else dataset_uri
        self.timeout = timeout
        self.engine_retrieval = engine_retrieval
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout)
        self._headers = {"Authorization": f"Key {api_key}", "Content-Type": "application/json"}

    def close(self) -> None:
        """Close the HTTP client this resolver created; an injected client stays the caller's."""
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> WordLiftResolver:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @property
    def user_dataset(self) -> bool:
        return is_user_world(self.dataset_uri, self.dataset)

    def resolve(self, mention: Mention, context: str, candidates: list[Candidate], *, language: str = "") -> Resolution:
        """One mention; see `resolve_many` for what is raised and what comes back."""
        return self.resolve_many(context, [mention], [candidates], language=language)[0]

    def resolve_many(
        self,
        text: str,
        mentions: list[Mention],
        candidates: list[list[Candidate]],
        *,
        language: str = "",
    ) -> list[Resolution]:
        """Resolve all `mentions` of `text` with as few requests as possible.

        Every span is checked first: a mention that does not lie in `text` as
        written raises ValueError before any request is sent. Then one request
        carries up to MAX_MENTIONS_PER_REQUEST spans and the inline vocabulary
        once, and a text over MAX_TEXT_CHARS is cut into chunks that never
        split a mention. Results come back in the order of `mentions`, one per
        mention, with document offsets. The engine decides each mention on the
        400 characters either side of it, the window `run()` used to send per
        mention, so batching does not change decisions.

        Status to outcome (RAISES, REASONS): 422 raises InvalidRequestError and
        401/403 raise AuthorizationError, since a caller error stops the batch;
        429 becomes `rate_limited` with `retry_after` and the problem `code` in
        the diagnostics when the body is Problem JSON (the gateway's allowance
        block is text/plain, so it has no code); any other 4xx/5xx and a
        transport failure become `resolver_unavailable` with the status when
        there is one. The client never retries on its own: a timed-out request
        may have been served and metered, so the retry policy is the caller's,
        and `diagnostics["credits"]` reports what each request cost
        (`X-Wordlift-Consumption`).
        """
        if len(mentions) != len(candidates):
            raise ValueError("one candidate list per mention")
        document = Context.of(text)
        for m in mentions:
            local_span(m, document)                      # raises before any request
        out: list[Resolution | None] = [None] * len(mentions)
        to_send = [i for i, (m, cands) in enumerate(zip(mentions, candidates, strict=True))
                   if cands or self.engine_retrieval or self.user_dataset]
        for i in set(range(len(mentions))) - set(to_send):
            out[i] = unresolved(mentions[i], NO_CANDIDATES)
        for c_start, c_end in chunk_document(document.text, [mentions[i] for i in to_send], self.MAX_TEXT_CHARS):
            chunk = Context(document.text[c_start:c_end], document.offset + c_start)
            inside = [i for i in to_send if chunk.local_span(mentions[i]) is not None]
            for b in range(0, len(inside), self.MAX_MENTIONS_PER_REQUEST):
                batch = inside[b:b + self.MAX_MENTIONS_PER_REQUEST]
                results = self._request(chunk, [mentions[i] for i in batch], [candidates[i] for i in batch], language)
                for i, r in zip(batch, results, strict=True):
                    out[i] = r
        return complete(out)

    def payload(self, mention: Mention, context: str, candidates: list[Candidate], language: str) -> dict[str, Any]:
        """The request body for one mention; see `payload_many`."""
        return self.payload_many(context, [mention], [candidates], language)

    def payload_many(self, text: str, mentions: list[Mention], candidates: list[list[Candidate]], language: str) -> dict[str, Any]:
        """One request body for several mentions of one text; spans rebased to the text sent."""
        spans = []
        for m, cands in zip(mentions, candidates, strict=True):
            start, end = local_span(m, text)
            span: dict[str, Any] = {"text": m.text, "start": start, "end": end, "type": m.label or None}
            if cands and not self.user_dataset:
                span["candidates"] = [
                    {"id": c.id, "label": c.label or None, "description": c.description or None,
                     "types": list(c.types) or None, "score": c.score}
                    for c in cands
                ]
            spans.append(span)
        body: dict[str, Any] = {"text": str(text), "dataset_uri": self.dataset_uri, "language": language or None, "mentions": spans}
        if self.dataset is not None:
            body["dataset"] = self.dataset
        return body

    def _request(self, context: Context, mentions: list[Mention], candidates: list[list[Candidate]], language: str) -> list[Resolution]:
        """One request: transport, then the status table, then the rows with the request's cost attached."""
        request = self.payload_many(context, mentions, candidates, language)
        try:
            response = self._client.post(f"{self.base_url}/v1/resolve", json=request, headers=self._headers, timeout=self.timeout)
        except httpx.HTTPError as exc:
            return [unresolved(m, RESOLVER_UNAVAILABLE, error=type(exc).__name__) for m in mentions]
        if response.status_code >= 400:
            return self._failure(Problem.of(response), mentions)
        return self._with_credits(self._results(response, context, mentions), response)

    def _failure(self, problem: Problem, mentions: list[Mention]) -> list[Resolution]:
        raises = self.RAISES.get(problem.status)
        if raises is not None:
            raise raises(problem)
        reason = self.REASONS.get(problem.status, self.DEFAULT_REASON)
        return [unresolved(m, reason, **problem.diagnostics()) for m in mentions]

    def _results(self, response: httpx.Response, context: str, mentions: list[Mention]) -> list[Resolution]:
        try:
            body = response.json()
        except ValueError:
            return [unresolved(m, PROTOCOL_ERROR, detail="response is not JSON") for m in mentions]
        return [self.parse(m, body, context) for m in mentions]

    @staticmethod
    def _with_credits(results: list[Resolution], response: httpx.Response) -> list[Resolution]:
        credits = response.headers.get("X-Wordlift-Consumption")
        if credits is None:
            return results
        return [replace(r, diagnostics={**r.diagnostics, "credits": credits}) for r in results]

    @staticmethod
    def parse(mention: Mention, body: Any, context: str | None = None) -> Resolution:
        """The resolution for `mention` in a contract-shaped response body.

        Exactly one row must carry the mention's span (rebased to the context
        it was sent with). No row, two rows, a resolved row without an
        identity, or a body of another shape is a `protocol_error`: an answer
        the client cannot act on is never turned into an identity. Unknown
        fields are ignored.
        """
        if not isinstance(body, dict) or not isinstance(body.get("mentions"), list):
            return unresolved(mention, PROTOCOL_ERROR, detail="response has no `mentions` list")
        local = local_span(mention, context) if context is not None else (mention.start, mention.end)
        rows = [r for r in body["mentions"] if isinstance(r, dict) and r.get("start") == local[0] and r.get("end") == local[1]]
        if len(rows) != 1:
            return unresolved(mention, PROTOCOL_ERROR, detail=f"{len(rows)} rows for the requested span")
        row = rows[0]
        entity = row.get("entity")
        extra = dict(row.get("diagnostics") or {})
        if row.get("dataset_uri"):
            extra["dataset_uri"] = row["dataset_uri"]
        if row.get("signals"):
            extra["signals"] = row["signals"]
        status = row.get("status")
        if status == "resolved":
            if not isinstance(entity, dict) or not isinstance(entity.get("id"), str) or not entity["id"]:
                return unresolved(mention, PROTOCOL_ERROR, detail="resolved row without an identity")
            score = row.get("score")
            if score is not None and not (isinstance(score, (int, float)) and not isinstance(score, bool) and math.isfinite(score)):
                score = None
            return resolved(
                mention,
                Entity(entity["id"], entity.get("label") or "", tuple(entity.get("types") or ()), tuple(entity.get("same_as") or ())),
                score=score,
                **extra,
            )
        if status == "unresolved":
            return unresolved(mention, row.get("reason") or NO_SUITABLE_CANDIDATE, **extra)
        return unresolved(mention, PROTOCOL_ERROR, detail=f"unknown status {status!r}")
