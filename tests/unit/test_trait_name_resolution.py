"""Unit tests for the trait-name resolver helper.

Imports from langchain/trait_name_resolver.py — a top-level module with no
Supabase or config dependency, so tests don't need env-var setup.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "langchain"))

from helpers.trait_name_resolver import _resolve_trait_name

def test_exact_match_returns_matched_true():
    result = _resolve_trait_name(
        "primary_root_length", ["primary_root_length", "leaf_count"]
    )
    assert result == {"matched": True, "name": "primary_root_length"}


def test_typo_with_close_match_returns_suggestions():
    result = _resolve_trait_name(
        "root_lenght",
        ["primary_root_length", "total_root_length", "leaf_count"],
    )
    assert result["matched"] is False
    assert result["sample_traits"] is None
    # both *_root_length entries are close; leaf_count is not
    assert "primary_root_length" in result["suggestions"]
    assert "total_root_length" in result["suggestions"]
    assert "leaf_count" not in result["suggestions"]


def test_no_close_match_returns_alphabetical_sample():
    candidates = ["primary_root_length", "leaf_count"]
    result = _resolve_trait_name("velocity_x", candidates)
    assert result["matched"] is False
    assert result["suggestions"] == []
    # alphabetical, not insertion order
    assert result["sample_traits"] == ["leaf_count", "primary_root_length"]


def test_empty_candidate_set_returns_empty_payload():
    result = _resolve_trait_name("anything", [])
    assert result == {"matched": False, "suggestions": [], "sample_traits": []}


def test_sample_truncates_at_10_when_more_traits_exist():
    candidates = [f"trait_{i:02d}" for i in range(25)]
    result = _resolve_trait_name("nope_no_match_zzz", candidates)
    assert result["matched"] is False
    assert result["suggestions"] == []
    assert len(result["sample_traits"]) == 10
    # first 10 alphabetically: trait_00..trait_09
    assert result["sample_traits"] == [f"trait_{i:02d}" for i in range(10)]


def test_suggestions_capped_at_five():
    """Many close matches should be truncated to 5 (get_close_matches n=5)."""
    candidates = [f"primary_root_length_v{i}" for i in range(20)]
    result = _resolve_trait_name("primary_root_length_v", candidates)
    assert result["matched"] is False
    assert len(result["suggestions"]) <= 5
