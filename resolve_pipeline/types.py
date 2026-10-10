"""Data that crosses the resolve() seam.

Kept deliberately small: a mention with its span, a candidate identity with
whatever evidence the retriever chose to attach, and a resolution that is
either an entity or an explicit `unresolved` with a reason.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Developer-facing reason vocabulary from docs/resolve-contract.md.
NO_SUITABLE_CANDIDATE = "no_suitable_candidate"
NO_CANDIDATES = "no_candidates"
TYPE_CONFLICT = "type_conflict"
LOW_RELEVANCE = "low_relevance"
RESOLVER_UNAVAILABLE = "resolver_unavailable"
# The engine (or the gateway) refused the request with 429: the replica's queue
# is full or the plan's monthly allowance is spent. `diagnostics["retry_after"]`
# carries the seconds the server asked for. Distinct from an outage so a caller
# can wait instead of failing over; never turned into an identity.
RATE_LIMITED = "rate_limited"
# The engine answered, but not with something a client can act on: no row for
# the requested span, two rows for it, a resolved row without an identity, a
# body that is not the contract's shape. Distinct from a transport failure and
# from a genuine abstention; never turned into an identity.
PROTOCOL_ERROR = "protocol_error"
# A caller-supplied mention that does not lie in the text as written (wrong
# offsets, normalised text, offsets from another text). No request is made for
# it. `run(..., on_invalid_mention="unresolved")` reports it with this reason;
# the default raises ValueError before anything is sent (issue #5).
INVALID_MENTION = "invalid_mention"

RESOLVED = "resolved"
UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class Mention:
    text: str
    start: int
    end: int
    label: str = ""
    score: float | None = None


class Context(str):
    """The text a resolver reads, and where it starts in the document.

    A context window is a slice of the document; the mention's offsets are
    document-relative. `offset` is what makes the two agree: the mention sits
    at `start - offset` in the text. Context is a `str`, so a retriever or
    resolver written against plain text keeps working; a plain string means
    offset 0 (the string is the whole document).
    """
    offset: int

    def __new__(cls, text: str, offset: int = 0) -> Context:
        self = super().__new__(cls, text)
        self.offset = offset
        return self

    @property
    def text(self) -> str:
        return str.__str__(self)

    def __repr__(self) -> str:
        return f"Context({self.text!r}, offset={self.offset})"

    @classmethod
    def of(cls, context: str | Context) -> Context:
        return context if isinstance(context, Context) else cls(context, 0)

    def local_span(self, mention: Mention) -> tuple[int, int] | None:
        """The mention's span inside this text, or None when it does not lie here as written."""
        start, end = mention.start - self.offset, mention.end - self.offset
        if start < 0 or end > len(self.text) or self.text[start:end] != mention.text:
            return None
        return start, end


@dataclass(frozen=True)
class Candidate:
    id: str
    label: str = ""
    description: str = ""
    types: tuple[str, ...] = ()
    score: float | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Entity:
    id: str
    label: str = ""
    types: tuple[str, ...] = ()
    same_as: tuple[str, ...] = ()


@dataclass(frozen=True)
class Resolution:
    mention: Mention
    status: str
    entity: Entity | None = None
    score: float | None = None
    reason: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @property
    def resolved(self) -> bool:
        return self.status == RESOLVED


def resolved(mention: Mention, entity: Entity, score: float | None = None, **diagnostics: Any) -> Resolution:
    return Resolution(mention, RESOLVED, entity=entity, score=score, diagnostics=diagnostics)


def unresolved(mention: Mention, reason: str, **diagnostics: Any) -> Resolution:
    return Resolution(mention, UNRESOLVED, reason=reason, diagnostics=diagnostics)
