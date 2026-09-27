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
