"""Classify the result of an experiment-name search into a single outcome.

The matching itself runs server-side (the ``cyl_experiment_search`` RPC, trigram-indexed) so it
scales; this module just turns the rows the RPC returns into one of three outcomes for the caller:
``Resolved`` (exactly one experiment — its id/label), ``Ambiguous`` (several — the caller lists
them and does not guess), or ``NoMatch`` (none). Pure (no I/O), so it is unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Match:
    """One matched experiment, with the fields shown on an ambiguous result."""

    id: int
    name: str
    species: str | None
    created: str | None  # YYYY-MM-DD

    @property
    def label(self) -> str:
        return f"{self.name} ({self.species or '?'})"


@dataclass(frozen=True)
class Resolved:
    """Exactly one experiment matched."""

    match: Match


@dataclass(frozen=True)
class Ambiguous:
    """Several experiments matched; the caller should surface these and not guess."""

    candidates: list[Match]


@dataclass(frozen=True)
class NoMatch:
    """No experiment matched."""


Resolution = Resolved | Ambiguous | NoMatch


def _match(row: dict[str, Any]) -> Match:
    return Match(
        id=row.get("id"),
        name=row.get("name") or "",
        species=row.get("species_name"),
        created=str(row.get("created_at") or "")[:10] or None,
    )


def classify(rows: list[dict[str, Any]]) -> Resolution:
    """Turn ``cyl_experiment_search`` rows into a single ``Resolution``.

    The rows are already the server-side matches (in ``ORDER BY name`` order); this only counts
    them: none -> ``NoMatch``, one -> ``Resolved``, several -> ``Ambiguous`` (order preserved).
    """
    matches = [_match(r) for r in rows]
    if not matches:
        return NoMatch()
    if len(matches) == 1:
        return Resolved(matches[0])
    return Ambiguous(matches)
