"""Fuzzy-match a user-provided trait name against the candidate set that
actually exists in the database.
"""
from __future__ import annotations

from difflib import get_close_matches

_MAX_SUGGESTIONS = 5
_MAX_SAMPLE = 10
_SIMILARITY_CUTOFF = 0.6


def _resolve_trait_name(input_name: str, candidate_names: list[str]) -> dict:
    """Return a structured payload describing whether input_name matches.

    Three return shapes:
      - {"matched": True, "name": <name>}                      exact match
      - {"matched": False, "suggestions": [...],               close matches
         "sample_traits": None}
      - {"matched": False, "suggestions": [],                  no close match —
         "sample_traits": [up to 10 alphabetical]}             present full sample

    Alphabetical sort on the sample branch is deterministic for tests and
    gives the user a predictable list to scan.
    """
    if input_name in candidate_names:
        return {"matched": True, "name": input_name}

    suggestions = get_close_matches(
        input_name, candidate_names, n=_MAX_SUGGESTIONS, cutoff=_SIMILARITY_CUTOFF
    )
    if suggestions:
        return {"matched": False, "suggestions": suggestions, "sample_traits": None}

    return {
        "matched": False,
        "suggestions": [],
        "sample_traits": sorted(candidate_names)[:_MAX_SAMPLE],
    }
