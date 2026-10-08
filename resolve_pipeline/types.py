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
# The engine answered, but not with something a client can act on: no row for
# the requested span, two rows for it, a resolved row without an identity, a
# body that is not the contract's shape. Distinct from a transport failure and
# from a genuine abstention; never turned into an identity.
PROTOCOL_ERROR = "protocol_error"

RESOLVED = "resolved"
UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class Mention:
    text: str
    start: int
    end: int
    label: str = ""
    score: float | None = None


@dataclass(frozen=True)
class Context:
    """The text a resolver reads, and where it starts in the document.

    A context window is a slice of the document; the mention's offsets are
    document-relative. `offset` is what makes the two agree: the mention sits
    at `start - offset` in `text`. A plain string is accepted wherever a
    Context is, and means offset 0 (the string is the whole document).
    """
    text: str
    offset: int = 0

    def __str__(self) -> str:
        return self.text

    def __len__(self) -> int:
        return len(self.text)

    def __contains__(self, item: object) -> bool:
        return item in self.text

    @classmethod
    def of(cls, context: "str | Context") -> "Context":
        return context if isinstance(context, Context) else cls(str(context), 0)

    def local_span(self, mention: "Mention") -> tuple[int, int] | None:
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
