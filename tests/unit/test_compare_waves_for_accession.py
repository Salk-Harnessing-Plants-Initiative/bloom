"""Unit tests for compare_waves_for_accession_tool.

Mocks the five PostgREST calls the tool makes:
  1. /cyl_waves               (experiment's waves)
  2. /cyl_trait_by_experiment_wave (trait candidates)
  3. /cyl_traits               (resolve canonical name)
  4. /accessions               (resolve accession_id)
  5. /cyl_plants               (plants of accession + embedded scans + traits)

And patches render_and_save so no PNGs are written.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("BLOOM_AGENT_KEY", "test-token-not-real")
os.environ.setdefault("SUPABASE_URL", "http://test-supabase")
os.environ.setdefault("BLOOM_PLOTS_DIR", "/tmp/bloom-test-plots")
os.environ.setdefault("BLOOM_PLOTS_URL", "http://localhost:5002/plots")

REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "langchain"))

from tools.cyl_tools import compare_waves_for_accession_tool  # noqa: E402


def _side_effect(*, waves, candidates, trait_id, accession_id, plants, accession_sample=None):
    """Router for the five httpx.get URLs the tool calls."""

    def _impl(url, **kwargs):
        params = kwargs.get("params", {})
        if url.endswith("/cyl_waves"):
            return MagicMock(status_code=200, json=lambda: waves)
        if url.endswith("/cyl_trait_by_experiment_wave"):
            return MagicMock(
                status_code=200,
                json=lambda: [{"trait_name": t} for t in candidates],
            )
        if url.endswith("/cyl_traits"):
            rows = [{"id": trait_id}] if trait_id is not None else []
            return MagicMock(status_code=200, json=lambda: rows)
        if url.endswith("/accessions"):
            rows = [{"id": accession_id}] if accession_id is not None else []
            return MagicMock(status_code=200, json=lambda: rows)
        if url.endswith("/cyl_plants"):
            # Two cases: full plant fetch (with scans) OR sample fetch (just accessions)
            if "select" in params and "accessions(name)" in params["select"] and "cyl_scans" not in params["select"]:
                # Sample fetch when accession not found
                sample = accession_sample or []
                return MagicMock(
                    status_code=200,
                    json=lambda: [{"accessions": {"name": n}} for n in sample],
                )
            return MagicMock(status_code=200, json=lambda: plants)
        raise AssertionError(f"unexpected httpx.get URL: {url}")

    return _impl


def _plant(wave_id: int, scans: list[dict]) -> dict:
    return {"id": 1, "wave_id": wave_id, "cyl_scans": scans}


def _scan(plant_age_days: int, trait_id: int, values: list[float]) -> dict:
    return {
        "plant_age_days": plant_age_days,
        "cyl_scan_traits": [
            {"trait_id": trait_id, "value": v} for v in values
        ],
    }


@patch("tools.cyl_tools.render_and_save", return_value="http://x/plot.png")
@patch("tools.cyl_tools.httpx.get")
def test_happy_path_returns_per_wave_sorted_chronologically(mock_get, mock_render):
    mock_get.side_effect = _side_effect(
        waves=[
            {"id": 10, "number": 1, "name": "Wave 1"},
            {"id": 11, "number": 2, "name": "Wave 2"},
        ],
        candidates=["primary_length"],
        trait_id=5,
        accession_id=42,
        plants=[
            _plant(10, [_scan(21, 5, [60, 62, 58])]),
            _plant(11, [_scan(21, 5, [70, 72, 68])]),
        ],
    )

    result = compare_waves_for_accession_tool.invoke(
        {"trait_name": "primary_length", "accession_name": "indi-12", "experiment_id": 1}
    )

    assert result["accession_name"] == "indi-12"
    assert result["n_waves"] == 2
    nums = [w["wave_number"] for w in result["per_wave"]]
    assert nums == [1, 2]
    assert result["per_wave"][0]["wave_name"] == "Wave 1"
    assert result["per_wave"][0]["n"] == 3
    assert result["plot_layout"] == "boxplot"
    assert result["plot_url"].endswith(".png")


@patch("tools.cyl_tools.render_and_save", return_value="http://x/plot.png")
@patch("tools.cyl_tools.httpx.get")
def test_single_wave_sets_cv_to_none(mock_get, mock_render):
    mock_get.side_effect = _side_effect(
        waves=[{"id": 10, "number": 1, "name": "Wave 1"}],
        candidates=["primary_length"],
        trait_id=5,
        accession_id=42,
        plants=[_plant(10, [_scan(21, 5, [60, 62])])],
    )

    result = compare_waves_for_accession_tool.invoke(
        {"trait_name": "primary_length", "accession_name": "indi-12", "experiment_id": 1}
    )

    assert result["n_waves"] == 1
    assert result["consistency"]["cv_of_wave_medians"] is None


@patch("tools.cyl_tools.render_and_save", return_value="http://x/plot.png")
@patch("tools.cyl_tools.httpx.get")
def test_unknown_accession_returns_error_no_plot(mock_get, mock_render):
    mock_get.side_effect = _side_effect(
        waves=[{"id": 10, "number": 1, "name": "Wave 1"}],
        candidates=["primary_length"],
        trait_id=5,
        accession_id=None,
        plants=[],
        accession_sample=["indi-1", "indi-3", "indi-7"],
    )

    result = compare_waves_for_accession_tool.invoke(
        {"trait_name": "primary_length", "accession_name": "ghost-accession", "experiment_id": 1}
    )

    assert result["error"] == "accession not found in experiment"
    assert "available_accessions_sample" in result
    assert mock_render.call_count == 0


@patch("tools.cyl_tools.render_and_save", return_value="http://x/plot.png")
@patch("tools.cyl_tools.httpx.get")
def test_accession_missing_from_some_waves_returns_error_no_plot(mock_get, mock_render):
    """Experiment has 3 waves but accession only appears in 2 → coverage error."""
    mock_get.side_effect = _side_effect(
        waves=[
            {"id": 10, "number": 1, "name": "Wave 1"},
            {"id": 11, "number": 2, "name": "Wave 2"},
            {"id": 12, "number": 3, "name": "Wave 3"},
        ],
        candidates=["primary_length"],
        trait_id=5,
        accession_id=42,
        plants=[
            _plant(10, [_scan(21, 5, [60])]),
            _plant(11, [_scan(21, 5, [62])]),
            # No plant in wave 12 → coverage gap
        ],
    )

    result = compare_waves_for_accession_tool.invoke(
        {"trait_name": "primary_length", "accession_name": "indi-12", "experiment_id": 1}
    )

    assert result["error"] == "accession does not span all waves of the experiment"
    assert result["missing_waves"] == [12]
    assert result["accession_present_in_waves"] == [10, 11]
    assert result["experiment_waves"] == [10, 11, 12]
    assert "compare_accessions_in_wave_tool" in result["note"]
    assert mock_render.call_count == 0


@patch("tools.cyl_tools.render_and_save", return_value="http://x/plot.png")
@patch("tools.cyl_tools.httpx.get")
def test_typo_trait_returns_suggestions_no_plot(mock_get, mock_render):
    mock_get.side_effect = _side_effect(
        waves=[{"id": 10, "number": 1, "name": "Wave 1"}],
        candidates=["primary_length", "leaf_count"],
        trait_id=None,
        accession_id=None,
        plants=[],
    )

    result = compare_waves_for_accession_tool.invoke(
        {"trait_name": "primary_lenght", "accession_name": "indi-12", "experiment_id": 1}
    )

    assert result["error"] == "trait not found"
    assert "primary_length" in result["suggestions"]
    assert mock_render.call_count == 0


@patch("tools.cyl_tools.render_and_save", return_value="http://x/plot.png")
@patch("tools.cyl_tools.httpx.get")
def test_consistency_block_computed_from_wave_medians(mock_get, mock_render):
    mock_get.side_effect = _side_effect(
        waves=[
            {"id": 10, "number": 1, "name": "Wave 1"},
            {"id": 11, "number": 2, "name": "Wave 2"},
            {"id": 12, "number": 3, "name": "Wave 3"},
        ],
        candidates=["primary_length"],
        trait_id=5,
        accession_id=42,
        plants=[
            _plant(10, [_scan(21, 5, [60, 60, 60])]),
            _plant(11, [_scan(21, 5, [80, 80, 80])]),
            _plant(12, [_scan(21, 5, [100, 100, 100])]),
        ],
    )

    result = compare_waves_for_accession_tool.invoke(
        {"trait_name": "primary_length", "accession_name": "indi-12", "experiment_id": 1}
    )

    cons = result["consistency"]
    assert cons["range_of_medians"] == [60.0, 100.0]
    assert cons["median_across_waves"] == 80.0
    # CV = std([60, 80, 100]) / mean([60, 80, 100]) = 20.0 / 80.0 = 0.25
    assert cons["cv_of_wave_medians"] == 0.25


@patch("tools.cyl_tools.render_and_save", return_value="http://x/plot.png")
@patch("tools.cyl_tools.httpx.get")
def test_plot_url_populated_on_happy_path(mock_get, mock_render):
    mock_get.side_effect = _side_effect(
        waves=[
            {"id": 10, "number": 1, "name": "Wave 1"},
            {"id": 11, "number": 2, "name": "Wave 2"},
        ],
        candidates=["primary_length"],
        trait_id=5,
        accession_id=42,
        plants=[
            _plant(10, [_scan(21, 5, [60])]),
            _plant(11, [_scan(21, 5, [70])]),
        ],
    )

    result = compare_waves_for_accession_tool.invoke(
        {"trait_name": "primary_length", "accession_name": "indi-12", "experiment_id": 1}
    )

    assert result["plot_url"] == "http://x/plot.png"
    assert mock_render.call_count == 1


@patch("tools.cyl_tools.render_and_save", return_value="http://x/plot.png")
@patch("tools.cyl_tools.httpx.get")
def test_age_context_single_age_per_wave(mock_get, mock_render):
    mock_get.side_effect = _side_effect(
        waves=[
            {"id": 10, "number": 1, "name": "Wave 1"},
            {"id": 11, "number": 2, "name": "Wave 2"},
        ],
        candidates=["primary_length"],
        trait_id=5,
        accession_id=42,
        plants=[
            _plant(10, [_scan(21, 5, [60])]),
            _plant(11, [_scan(21, 5, [70])]),
        ],
    )

    result = compare_waves_for_accession_tool.invoke(
        {"trait_name": "primary_length", "accession_name": "indi-12", "experiment_id": 1}
    )

    for wave in result["per_wave"]:
        assert wave["plant_age_days_distinct"] == [21]
        assert wave["plant_age_days_min"] == 21
        assert wave["plant_age_days_max"] == 21


@patch("tools.cyl_tools.render_and_save", return_value="http://x/plot.png")
@patch("tools.cyl_tools.httpx.get")
def test_specific_age_mode_filters_to_that_age(mock_get, mock_render):
    """Wave 1 has scans at ages [14, 21, 28]; with plant_age_days=21 only
    that age contributes (despite default being latest-per-plant)."""
    mock_get.side_effect = _side_effect(
        waves=[{"id": 10, "number": 1, "name": "Wave 1"}],
        candidates=["primary_length"],
        trait_id=5,
        accession_id=42,
        plants=[
            _plant(10, [
                _scan(14, 5, [50]),
                _scan(21, 5, [60]),
                _scan(28, 5, [70]),
            ]),
        ],
    )

    result = compare_waves_for_accession_tool.invoke(
        {
            "trait_name": "primary_length",
            "accession_name": "indi-12",
            "experiment_id": 1,
            "plant_age_days": 21,
        }
    )

    assert result["scope"]["scan_mode"] == "specific_age"
    assert result["per_wave"][0]["mean"] == 60.0
    assert result["per_wave"][0]["plant_age_days_distinct"] == [21]
