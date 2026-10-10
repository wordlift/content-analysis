"""The resolve() seam: a protocol, an open reference implementation, and the
client for WordLift's engine.

A resolver answers one question for one mention: which of the supplied
candidates, if any, is the identity this mention refers to in this context.
Returning `unresolved` is a valid answer.
"""
from __future__ import annotations

import math
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

    def resolve_many(
        self, text: str, mentions: list[Mention], candidates: list[list[Candidate]], *, language: str = ""
    ) -> list[Resolution]:
        """Local resolver: one decision per mention, no request to batch."""
        return [self.resolve(m, text, c, language=language) for m, c in zip(mentions, candidates, strict=True)]


class WordLiftResolver:
    """Client for WordLift's `POST /v1/resolve` (see docs/resolve-contract.md).

    The request carries the mention span, its context and the caller's
    candidates; the engine returns the supported identity or `unresolved`.
    Transport failures become `unresolved` with reason `resolver_unavailable`
    rather than a guessed identity: a wrong link costs more than no link.
    """

    DEFAULT_BASE_URL = "https://resolve.wordlift.io"
    WORDLIFT_GRAPH = WORDLIFT_GRAPH
    #: Your entities first, Wikidata for everything else.
    LOCAL_FIRST = f"{WORDLIFT_GRAPH},{PUBLIC_WORLD}"

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

    def payload(self, mention: Mention, context: str, candidates: list[Candidate], language: str) -> dict[str, Any]:
        """The request body. The span is rebased to the context text; see Context."""
        start, end = local_span(mention, context)
        span: dict[str, Any] = {"text": mention.text, "start": start, "end": end,
                                "type": mention.label or None}
        if candidates and not self.user_dataset:
            span["candidates"] = [
                {"id": c.id, "label": c.label or None, "description": c.description or None,
                 "types": list(c.types) or None, "score": c.score}
                for c in candidates
            ]
        body: dict[str, Any] = {"text": str(context), "dataset_uri": self.dataset_uri, "language": language or None, "mentions": [span]}
        if self.dataset is not None:
            body["dataset"] = self.dataset
        return body

    #: Mentions per request. The engine resolves a document's mentions in one
    #: pass; this bounds a single request's work and response size.
    MAX_MENTIONS_PER_REQUEST = 200

    def resolve(self, mention: Mention, context: str, candidates: list[Candidate], *, language: str = "") -> Resolution:
        """Raises ValueError, before any request, when the mention does not lie in `context` as written,
        InvalidRequestError (a ValueError) when the engine rejects the request with 422, and
        AuthorizationError when it refuses the key."""
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

        One request carries up to MAX_MENTIONS_PER_REQUEST spans and the inline
        vocabulary once, instead of one request (and one vocabulary upload) per
        mention. Results come back in the order of `mentions`, one per mention,
        with explicit `unresolved` outcomes; the engine chooses each mention's
        own context inside the document, which is the same 400-character
        window the pipeline used to send per mention.

        A 429 becomes `rate_limited` with `retry_after` in the diagnostics; a
        transport failure or a 5xx becomes `resolver_unavailable` with the
        status when there is one. 422 raises InvalidRequestError and 401/403
        raise AuthorizationError: a caller error stops the batch. The client
        never retries on its own: a timed-out request may have been served
        and metered (`X-Wordlift-Consumption`), so the retry policy is the
        caller's, and `diagnostics["credits"]` reports what each request cost.
        """
        if len(mentions) != len(candidates):
            raise ValueError("one candidate list per mention")
        out: list[Resolution | None] = [None] * len(mentions)
        indices: list[int] = []
        for i, (m, cands) in enumerate(zip(mentions, candidates, strict=True)):
            if not cands and not self.engine_retrieval and not self.user_dataset:
                out[i] = unresolved(m, NO_CANDIDATES)
            else:
                indices.append(i)
        for start in range(0, len(indices), self.MAX_MENTIONS_PER_REQUEST):
            batch = indices[start:start + self.MAX_MENTIONS_PER_REQUEST]
            request = self.payload_many(text, [mentions[i] for i in batch], [candidates[i] for i in batch], language)
            results = self._post(request, text, [mentions[i] for i in batch])
            for i, r in zip(batch, results, strict=True):
                out[i] = r
        return [r for r in out if r is not None]

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

    def _post(self, request: dict[str, Any], text: str, mentions: list[Mention]) -> list[Resolution]:
        try:
            response = self._client.post(f"{self.base_url}/v1/resolve", json=request, headers=self._headers, timeout=self.timeout)
        except httpx.HTTPError as exc:
            return [unresolved(m, RESOLVER_UNAVAILABLE, error=type(exc).__name__) for m in mentions]
        status = response.status_code
        detail = self._error_detail(response)
        if status == 422:
            raise InvalidRequestError(detail)
        if status in (401, 403):
            raise AuthorizationError(status, detail)
        if status == 429:
            retry_after = response.headers.get("Retry-After")
            meta = {"status": status, "code": self._error_code(response)}
            if retry_after is not None:
                meta["retry_after"] = retry_after
            return [unresolved(m, RATE_LIMITED, **meta) for m in mentions]
        if status >= 400:
            return [unresolved(m, RESOLVER_UNAVAILABLE, status=status, code=self._error_code(response)) for m in mentions]
        credits = response.headers.get("X-Wordlift-Consumption")
        try:
            body = response.json()
        except ValueError:
            return [unresolved(m, PROTOCOL_ERROR, detail="response is not JSON") for m in mentions]
        results = [self.parse(m, body, text) for m in mentions]
        if credits is not None:
            results = [Resolution(r.mention, r.status, r.entity, r.score, r.reason, {**r.diagnostics, "credits": credits})
                       for r in results]
        return results

    @staticmethod
    def _error_detail(response: httpx.Response) -> Any:
        try:
            body = response.json()
        except ValueError:
            return response.text[:200]
        if isinstance(body, dict):
            return body.get("detail") or body.get("title") or body
        return body

    @staticmethod
    def _error_code(response: httpx.Response) -> str | None:
        """The stable `code` of an RFC 9457 problem body, when the engine sent one."""
        try:
            body = response.json()
        except ValueError:
            return None
        return body.get("code") if isinstance(body, dict) else None

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
