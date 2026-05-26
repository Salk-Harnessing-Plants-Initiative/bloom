"""Unit tests for compare_accessions_in_wave_tool.

Mocks httpx.get for the three PostgREST calls the tool makes:
  1. /cyl_trait_by_experiment_wave (candidate trait names in scope)
  2. /cyl_traits (resolve canonical name to trait_id)
  3. /cyl_plants (raw values per plant via embedded scans/scan_traits/accessions)

Also mocks the plot-renderer so no PNGs are written from unit tests.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("BLOOM_AGENT_KEY", "test-token-not-real")
os.environ.setdefault("SUPABASE_URL", "http://test-supabase")
os.environ.setdefault("BLOOM_PLOTS_DIR", "/tmp/bloom-test-plots")
os.environ.setdefault("BLOOM_PLOTS_URL", "http://localhost:5002/plots")

REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "langchain"))

from tools.cyl_tools import compare_accessions_in_wave_tool  # noqa: E402


def _httpx_side_effect(*, candidates: list[str], trait_id: int | None, plants: list[dict]):
    """Build a side_effect routing each httpx.get URL to the right mock JSON."""

    def _side_effect(url, **kwargs):
        if url.endswith("/cyl_trait_by_experiment_wave"):
            return MagicMock(
                status_code=200,
                json=lambda: [{"trait_name": t} for t in candidates],
            )
        if url.endswith("/cyl_traits"):
            rows = [{"id": trait_id}] if trait_id is not None else []
            return MagicMock(status_code=200, json=lambda: rows)
        if url.endswith("/cyl_plants"):
            return MagicMock(status_code=200, json=lambda: plants)
        raise AssertionError(f"unexpected httpx.get URL: {url}")

    return _side_effect


def _plant(
    accession_name: str,
    trait_id: int,
    values: list[float],
    plant_age_days: int = 21,
) -> dict:
    """Build a /cyl_plants embedded-resource shaped mock entry — single scan
    at `plant_age_days` (default 21, matching the dev-seed convention)."""
    return {
        "id": 1,
        "accessions": {"name": accession_name},
        "cyl_scans": [
            {
                "plant_age_days": plant_age_days,
                "cyl_scan_traits": [
                    {"trait_id": trait_id, "value": v} for v in values
                ],
            }
        ],
    }


def _plant_multi_scan(
    accession_name: str,
    trait_id: int,
    scans_by_age: dict[int, list[float]],
) -> dict:
    """Build a plant with multiple scans, one per age in `scans_by_age`."""
    return {
        "id": 1,
        "accessions": {"name": accession_name},
        "cyl_scans": [
            {
                "plant_age_days": age,
                "cyl_scan_traits": [
                    {"trait_id": trait_id, "value": v} for v in values
                ],
            }
            for age, values in scans_by_age.items()
        ],
    }


@patch("tools.cyl_tools.render_and_save", return_value="http://x/y.png")
@patch("tools.cyl_tools.httpx.get")
def test_typo_trait_returns_suggestions_no_plot(mock_get, mock_render):
    mock_get.side_effect = _httpx_side_effect(
        candidates=["primary_length", "total_length", "leaf_count"],
        trait_id=None,
        plants=[],
    )

    result = compare_accessions_in_wave_tool.invoke(
        {"trait_name": "primary_lenght", "wave_id": 12}
    )

    assert result["error"] == "trait not found"
    assert "primary_length" in result["suggestions"]
    assert "plot_url" not in result
    assert mock_render.call_count == 0


@patch("tools.cyl_tools.render_and_save", return_value="http://x/cyl_supabase_accession_rank_aaaa1111.png")
@patch("tools.cyl_tools.httpx.get")
def test_happy_path_small_panel_returns_boxplot_layout(mock_get, mock_render):
    mock_get.side_effect = _httpx_side_effect(
        candidates=["primary_length"],
        trait_id=5,
        plants=[
            _plant("indi-12", 5, [78, 80, 82, 79, 78]),
            _plant("indi-7", 5, [72, 73, 74, 71, 72]),
            _plant("indi-3", 5, [65, 66, 64, 67, 65]),
        ],
    )

    result = compare_accessions_in_wave_tool.invoke(
        {"trait_name": "primary_length", "wave_id": 12}
    )

    assert result["trait_name"] == "primary_length"
    assert result["scope"]["wave_id"] == 12
    assert result["scope"]["scan_mode"] == "latest_per_plant"
    assert result["scope"]["plant_age_days"] is None
    assert result["n_accessions"] == 3
    assert result["plot_layout"] == "boxplot"
    assert result["plot_url"].endswith(".png")
    assert "summary" not in result  # small panel

    rankings = result["rankings"]
    assert [r["accession_name"] for r in rankings] == ["indi-12", "indi-7", "indi-3"]
    assert [r["rank"] for r in rankings] == [1, 2, 3]
    assert rankings[0]["n"] == 5


@patch("tools.cyl_tools.render_and_save", return_value="http://x/plot.png")
@patch("tools.cyl_tools.httpx.get")
def test_large_panel_returns_ranked_profile_layout_and_summary(mock_get, mock_render):
    n_accessions = 15
    plants = [_plant(f"indi-{i:02d}", 5, [50 + i + 0.5, 50 + i, 50 + i + 1]) for i in range(n_accessions)]
    mock_get.side_effect = _httpx_side_effect(
        candidates=["primary_length"], trait_id=5, plants=plants,
    )

    result = compare_accessions_in_wave_tool.invoke(
        {"trait_name": "primary_length", "wave_id": 30}
    )

    assert result["n_accessions"] == n_accessions
    assert result["plot_layout"] == "ranked_profile"
    assert "summary" in result
    s = result["summary"]
    assert "median_of_accession_medians" in s
    assert "range_of_medians" in s
    assert len(s["top_3"]) == 3
    assert len(s["bottom_3"]) == 3

    # Highest-median accession should be indi-14 (i=14 → 50+14+0.5=64.5 median)
    assert result["rankings"][0]["accession_name"] == "indi-14"
    assert s["top_3"][0] == "indi-14"


@patch("tools.cyl_tools.render_and_save", return_value="http://x/plot.png")
@patch("tools.cyl_tools.httpx.get")
def test_no_data_returns_empty_rankings_no_plot(mock_get, mock_render):
    mock_get.side_effect = _httpx_side_effect(
        candidates=["primary_length"],
        trait_id=5,
        plants=[],  # no plants in scope
    )

    result = compare_accessions_in_wave_tool.invoke(
        {"trait_name": "primary_length", "wave_id": 99}
    )

    assert result["n_accessions"] == 0
    assert result["rankings"] == []
    assert "note" in result
    assert "no scans" in result["note"]
    assert "plot_url" not in result
    assert mock_render.call_count == 0


@patch("tools.cyl_tools.render_and_save", return_value="http://x/plot.png")
@patch("tools.cyl_tools.httpx.get")
def test_rankings_sorted_by_median_descending(mock_get, mock_render):
    mock_get.side_effect = _httpx_side_effect(
        candidates=["primary_length"],
        trait_id=5,
        plants=[
            _plant("low", 5, [10, 11, 12]),
            _plant("high", 5, [100, 101, 102]),
            _plant("mid", 5, [50, 51, 52]),
        ],
    )

    result = compare_accessions_in_wave_tool.invoke(
        {"trait_name": "primary_length", "wave_id": 1}
    )

    medians = [r["median"] for r in result["rankings"]]
    assert medians == sorted(medians, reverse=True)
    assert result["rankings"][0]["accession_name"] == "high"
    assert result["rankings"][-1]["accession_name"] == "low"


@patch("tools.cyl_tools.render_and_save", return_value="http://x/plot.png")
@patch("tools.cyl_tools.httpx.get")
def test_default_mode_picks_latest_scan_per_plant(mock_get, mock_render):
    """When no plant_age_days is passed, only the highest-age scan per plant contributes."""
    mock_get.side_effect = _httpx_side_effect(
        candidates=["primary_length"],
        trait_id=5,
        plants=[
            _plant_multi_scan("indi-12", 5, {14: [60], 21: [70], 28: [78]}),
            _plant_multi_scan("indi-7", 5, {14: [55], 21: [65], 28: [72]}),
        ],
    )

    result = compare_accessions_in_wave_tool.invoke(
        {"trait_name": "primary_length", "wave_id": 12}
    )

    assert result["scope"]["scan_mode"] == "latest_per_plant"
    assert result["scope"]["plant_age_days"] is None
    # Only the age-28 scans (the latest per plant) contribute
    indi12 = next(r for r in result["rankings"] if r["accession_name"] == "indi-12")
    indi7 = next(r for r in result["rankings"] if r["accession_name"] == "indi-7")
    assert indi12["n"] == 1 and indi12["mean"] == 78.0
    assert indi7["n"] == 1 and indi7["mean"] == 72.0


@patch("tools.cyl_tools.render_and_save", return_value="http://x/plot.png")
@patch("tools.cyl_tools.httpx.get")
def test_specific_age_mode_filters_to_that_age(mock_get, mock_render):
    """When plant_age_days is passed, only scans at that exact age contribute."""
    mock_get.side_effect = _httpx_side_effect(
        candidates=["primary_length"],
        trait_id=5,
        plants=[
            _plant_multi_scan("indi-12", 5, {14: [60], 21: [70], 28: [78]}),
            _plant_multi_scan("indi-7", 5, {14: [55], 21: [65], 28: [72]}),
        ],
    )

    result = compare_accessions_in_wave_tool.invoke(
        {"trait_name": "primary_length", "wave_id": 12, "plant_age_days": 21}
    )

    assert result["scope"]["scan_mode"] == "specific_age"
    assert result["scope"]["plant_age_days"] == 21
    indi12 = next(r for r in result["rankings"] if r["accession_name"] == "indi-12")
    indi7 = next(r for r in result["rankings"] if r["accession_name"] == "indi-7")
    # Only age-21 scans contribute — values are 70 and 65
    assert indi12["mean"] == 70.0
    assert indi7["mean"] == 65.0


@patch("tools.cyl_tools.render_and_save", return_value="http://x/plot.png")
@patch("tools.cyl_tools.httpx.get")
def test_plants_with_null_values_or_wrong_trait_excluded(mock_get, mock_render):
    """Defensive: null trait values and wrong trait_ids are ignored."""
    mock_get.side_effect = _httpx_side_effect(
        candidates=["primary_length"],
        trait_id=5,
        plants=[
            {
                "id": 1,
                "accessions": {"name": "indi-12"},
                "cyl_scans": [
                    {
                        "plant_age_days": 21,
                        "cyl_scan_traits": [
                            {"trait_id": 5, "value": 78.0},
                            {"trait_id": 5, "value": None},  # null — skip
                            {"trait_id": 99, "value": 999.0},  # wrong trait — skip
                        ],
                    }
                ],
            }
        ],
    )

    result = compare_accessions_in_wave_tool.invoke(
        {"trait_name": "primary_length", "wave_id": 1}
    )

    assert result["rankings"][0]["n"] == 1
    assert result["rankings"][0]["mean"] == 78.0
