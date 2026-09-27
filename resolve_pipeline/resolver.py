"""The resolve() seam: a protocol, an open reference implementation, and the
client for WordLift's engine.

A resolver answers one question for one mention: which of the supplied
candidates, if any, is the identity this mention refers to in this context.
Returning `unresolved` is a valid answer.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import httpx

from .types import (
    LOW_RELEVANCE, NO_CANDIDATES, NO_SUITABLE_CANDIDATE, RESOLVER_UNAVAILABLE,
    Candidate, Entity, Mention, Resolution, resolved, unresolved,
)


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


class WordLiftResolver:
    """Client for WordLift's `POST /v1/resolve` (see docs/resolve-contract.md).

    The request carries the mention span, its context and the caller's
    candidates; the engine returns the supported identity or `unresolved`.
    Transport failures become `unresolved` with reason `resolver_unavailable`
    rather than a guessed identity: a wrong link costs more than no link.
    """

    DEFAULT_BASE_URL = "https://resolve.wordlift.io"
    WORDLIFT_GRAPH = "wordlift://dataset/me"
    #: Your entities first, Wikidata for everything else.
    LOCAL_FIRST = "wordlift://dataset/me,wikidata://public"

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
        self._client = client or httpx.Client(timeout=timeout)
        self._headers = {"Authorization": f"Key {api_key}", "Content-Type": "application/json"}

    @property
    def user_dataset(self) -> bool:
        worlds = [w.strip() for w in self.dataset_uri.split(",")]
        return self.dataset is not None or self.WORDLIFT_GRAPH in worlds or "inline" in worlds

    def payload(self, mention: Mention, context: str, candidates: list[Candidate], language: str) -> dict[str, Any]:
        span: dict[str, Any] = {"text": mention.text, "start": mention.start, "end": mention.end,
                                "type": mention.label or None}
        if candidates and not self.user_dataset:
            span["candidates"] = [
                {"id": c.id, "label": c.label or None, "description": c.description or None,
                 "types": list(c.types) or None, "score": c.score}
                for c in candidates
            ]
        body: dict[str, Any] = {"text": context, "dataset_uri": self.dataset_uri, "language": language or None, "mentions": [span]}
        if self.dataset is not None:
            body["dataset"] = self.dataset
        return body

    def resolve(self, mention: Mention, context: str, candidates: list[Candidate], *, language: str = "") -> Resolution:
        if not candidates and not self.engine_retrieval and not self.user_dataset:
            return unresolved(mention, NO_CANDIDATES)
        try:
            response = self._client.post(
                f"{self.base_url}/v1/resolve",
                json=self.payload(mention, context, candidates, language),
                headers=self._headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return unresolved(mention, RESOLVER_UNAVAILABLE, error=type(exc).__name__)
        return self.parse(mention, body)

    @staticmethod
    def parse(mention: Mention, body: dict[str, Any]) -> Resolution:
        rows = body.get("mentions") or []
        row = next((r for r in rows if r.get("start") == mention.start and r.get("end") == mention.end), rows[0] if rows else {})
        entity = row.get("entity")
        extra = dict(row.get("diagnostics") or {})
        if row.get("dataset_uri"):
            extra["dataset_uri"] = row["dataset_uri"]
        if row.get("signals"):
            extra["signals"] = row["signals"]
        if row.get("status") == "resolved" and entity:
            return resolved(
                mention,
                Entity(entity["id"], entity.get("label", ""), tuple(entity.get("types") or ()), tuple(entity.get("same_as") or ())),
                score=row.get("score"),
                **extra,
            )
        return unresolved(mention, row.get("reason") or NO_SUITABLE_CANDIDATE, **extra)
