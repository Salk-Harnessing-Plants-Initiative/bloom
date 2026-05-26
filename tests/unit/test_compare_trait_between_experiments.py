"""Unit tests for compare_trait_between_experiments_tool.

Mocks httpx.get so the tests are pure unit (no live Supabase). Two URL
patterns to mock: the trait-name candidate fetch and the wave-level
compare query.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Env vars config.py requires at import time — must be set BEFORE the
# tools.cyl_tools import below.
os.environ.setdefault("BLOOM_AGENT_KEY", "test-token-not-real")
os.environ.setdefault("SUPABASE_URL", "http://test-supabase")

REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "langchain"))

from tools.cyl_tools import compare_trait_between_experiments_tool  # noqa: E402


def _mock_get_side_effect(distinct_traits: list[str], compare_rows: list[dict]):
    """Build a side_effect callable that returns the right mock per URL."""

    def _side_effect(url, **kwargs):
        params = kwargs.get("params", {})
        trait_filter = params.get("trait_name")
        if trait_filter and trait_filter.startswith("eq."):
            return MagicMock(status_code=200, json=lambda: compare_rows)
        return MagicMock(
            status_code=200,
            json=lambda: [{"trait_name": t} for t in distinct_traits],
        )

    return _side_effect


def test_same_experiment_twice_raises_value_error():
    with pytest.raises(ValueError, match="two distinct experiment IDs"):
        compare_trait_between_experiments_tool.invoke(
            {"experiment_a_id": 5, "experiment_b_id": 5, "trait_name": "primary_length"}
        )


@patch("tools.cyl_tools.httpx.get")
def test_happy_path_returns_per_wave_breakdown(mock_get):
    seed_rows = [
        {"experiment_id": 1, "experiment_name": "alfalfa-2024", "wave_id": 10, "wave_number": 1,
         "trait_name": "primary_length", "n": 180, "mean": 45.2, "std": 8.1, "min_value": 28.0, "max_value": 67.0},
        {"experiment_id": 1, "experiment_name": "alfalfa-2024", "wave_id": 11, "wave_number": 2,
         "trait_name": "primary_length", "n": 165, "mean": 48.6, "std": 7.3, "min_value": 32.0, "max_value": 70.0},
        {"experiment_id": 2, "experiment_name": "alfalfa-2025", "wave_id": 20, "wave_number": 1,
         "trait_name": "primary_length", "n": 192, "mean": 44.1, "std": 9.0, "min_value": 27.0, "max_value": 68.0},
        {"experiment_id": 2, "experiment_name": "alfalfa-2025", "wave_id": 21, "wave_number": 2,
         "trait_name": "primary_length", "n": 175, "mean": 47.3, "std": 8.2, "min_value": 30.0, "max_value": 71.0},
    ]
    mock_get.side_effect = _mock_get_side_effect(
        distinct_traits=["primary_length", "leaf_count"],
        compare_rows=seed_rows,
    )

    result = compare_trait_between_experiments_tool.invoke(
        {"experiment_a_id": 1, "experiment_b_id": 2, "trait_name": "primary_length"}
    )

    assert "rows" in result
    assert len(result["rows"]) == 4
    assert result["trait_name"] == "primary_length"
    assert result["experiment_a_id"] == 1
    assert result["experiment_a_name"] == "alfalfa-2024"
    assert result["experiment_b_id"] == 2
    assert result["experiment_b_name"] == "alfalfa-2025"
    # Two calls: one for candidates, one for the compare
    assert mock_get.call_count == 2


@patch("tools.cyl_tools.httpx.get")
def test_unknown_trait_returns_suggestions(mock_get):
    mock_get.side_effect = _mock_get_side_effect(
        distinct_traits=["primary_length", "total_length", "leaf_count"],
        compare_rows=[],  # never queried
    )

    result = compare_trait_between_experiments_tool.invoke(
        {"experiment_a_id": 1, "experiment_b_id": 2, "trait_name": "primary_lenght"}
    )

    assert result["error"] == "trait not found"
    assert result["trait_name"] == "primary_lenght"
    assert "primary_length" in result["suggestions"]
    # No compare query — only the candidates fetch
    assert mock_get.call_count == 1


@patch("tools.cyl_tools.httpx.get")
def test_no_data_for_pair_returns_empty_rows(mock_get):
    mock_get.side_effect = _mock_get_side_effect(
        distinct_traits=["primary_length"],
        compare_rows=[],
    )

    result = compare_trait_between_experiments_tool.invoke(
        {"experiment_a_id": 1, "experiment_b_id": 2, "trait_name": "primary_length"}
    )

    assert result["rows"] == []
    assert result["trait_name"] == "primary_length"
    assert "no matching scans" in result["note"]
