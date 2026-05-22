"""Unit tests for list_experiments_tool.

Verifies the per-experiment chip payload (label + prompt, capped at 20)
plus the 3 baseline action chips. Mocks httpx.get so the tests are pure
unit (no live Supabase).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("BLOOM_AGENT_KEY", "test-token-not-real")
os.environ.setdefault("SUPABASE_URL", "http://test-supabase")

REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "langchain"))

from tools.cyl_tools import list_experiments_tool  # noqa: E402


def _mock_response(experiments: list[dict]):
    return MagicMock(status_code=200, json=lambda: experiments)


def _make_experiment(i: int, name: str | None = None) -> dict:
    return {
        "id": i,
        "name": name if name is not None else f"exp-{i:02d}",
        "created_at": "2026-01-01",
        "species": {"id": 1, "common_name": "alfalfa"},
        "people": {"id": 1, "name": "alice"},
    }


@patch("tools.cyl_tools.httpx.get")
def test_emits_per_experiment_chips(mock_get):
    mock_get.return_value = _mock_response(
        [_make_experiment(1, "alfalfa-2024"), _make_experiment(2, "alfalfa-2025")]
    )

    result = list_experiments_tool.invoke({})

    labels = [chip["label"] for chip in result["followup_actions"]]
    assert "alfalfa-2024" in labels
    assert "alfalfa-2025" in labels
    alfalfa_chip = next(c for c in result["followup_actions"] if c["label"] == "alfalfa-2024")
    assert alfalfa_chip["prompt"] == "Show waves for alfalfa-2024"


@patch("tools.cyl_tools.httpx.get")
def test_appends_three_baseline_action_chips(mock_get):
    mock_get.return_value = _mock_response([_make_experiment(1, "alfalfa-2024")])

    result = list_experiments_tool.invoke({})

    labels = [chip["label"] for chip in result["followup_actions"]]
    assert "List the traits" in labels
    assert "Show trait statistics" in labels
    assert "Compare across waves" in labels
    assert len(result["followup_actions"]) == 1 + 3


@patch("tools.cyl_tools.httpx.get")
def test_experiment_chips_capped_at_twenty(mock_get):
    many = [_make_experiment(i) for i in range(50)]
    mock_get.return_value = _mock_response(many)

    result = list_experiments_tool.invoke({})

    experiment_labels = [chip["label"] for chip in result["followup_actions"][:20]]
    assert len(experiment_labels) == 20
    assert experiment_labels[0] == "exp-00"
    assert experiment_labels[-1] == "exp-19"
    # Baselines still appended after the 20 experiment chips
    assert len(result["followup_actions"]) == 20 + 3


@patch("tools.cyl_tools.httpx.get")
def test_empty_experiments_still_returns_baseline_chips(mock_get):
    mock_get.return_value = _mock_response([])

    result = list_experiments_tool.invoke({})

    assert result["count"] == 0
    assert result["experiments"] == []
    assert len(result["followup_actions"]) == 3
    labels = [chip["label"] for chip in result["followup_actions"]]
    assert labels == ["List the traits", "Show trait statistics", "Compare across waves"]


@patch("tools.cyl_tools.httpx.get")
def test_skips_experiments_with_missing_name(mock_get):
    named = _make_experiment(1, "alfalfa-2024")
    unnamed = _make_experiment(2)
    unnamed["name"] = None
    mock_get.return_value = _mock_response([named, unnamed])

    result = list_experiments_tool.invoke({})

    labels = [chip["label"] for chip in result["followup_actions"]]
    assert "alfalfa-2024" in labels
    assert len(result["followup_actions"]) == 1 + 3
