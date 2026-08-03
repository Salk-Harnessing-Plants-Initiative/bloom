"""Resolve an experiment by name — shared, client-free helper for the cyl commands.

Kept pure (no I/O) so it is unit-testable and reusable: the caller fetches the experiments and
decides what to do with the outcome. Matching is layered — a case-insensitive substring first
(predictable), then a stdlib ``difflib`` approximate fallback (typo-tolerant) only when substring
finds nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import get_close_matches
from typing import Any


@dataclass(frozen=True)
class Resolved:
    """Exactly one experiment matched."""

    experiment_id: int
    label: str  # "name (species)" — for the confirmation message


@dataclass(frozen=True)
class Ambiguous:
    """Several experiments matched; the caller should surface these and not guess."""

    candidates: list[tuple[int, str]]  # (experiment_id, "name (species)"), sorted by label


@dataclass(frozen=True)
class NoMatch:
    """No experiment matched."""


Resolution = Resolved | Ambiguous | NoMatch


def _label(exp: dict[str, Any]) -> str:
    name = exp.get("name") or ""
    species = (exp.get("species") or {}).get("common_name") or "?"
    return f"{name} ({species})"


def resolve_experiment(
    experiments: list[dict[str, Any]], name: str, species: str | None = None
) -> Resolution:
    """Resolve ``name`` to a single experiment among ``experiments``.

    Each item is a ``cyl_experiments`` row joined to ``species`` (has ``id``, ``name``, and a
    ``species.common_name``). ``species`` (a common name) narrows the pool first. Returns
    ``Resolved`` on a single match, ``Ambiguous`` (sorted candidates) on several, ``NoMatch`` on
    none. Never picks one when the name is ambiguous.
    """
    pool = experiments
    if species:
        want = species.casefold()
        pool = [
            e
            for e in pool
            if ((e.get("species") or {}).get("common_name") or "").casefold() == want
        ]

    query = name.casefold().strip()
    hits = [e for e in pool if query and query in (e.get("name") or "").casefold()]

    if not hits:  # substring found nothing — fall back to approximate matching
        names = [e.get("name") or "" for e in pool]
        close = set(get_close_matches(name, names, n=5, cutoff=0.6))
        hits = [e for e in pool if (e.get("name") or "") in close]

    if not hits:
        return NoMatch()
    if len(hits) == 1:
        return Resolved(hits[0].get("id"), _label(hits[0]))
    candidates = sorted(((e.get("id"), _label(e)) for e in hits), key=lambda c: c[1].casefold())
    return Ambiguous(candidates)
