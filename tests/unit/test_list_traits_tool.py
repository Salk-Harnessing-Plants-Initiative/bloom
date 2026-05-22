"""Unit tests for list_traits_tool.

Verifies the followup_actions chip payload (label + prompt per trait,
capped at 20) plus preservation of the original count/traits/hint keys.
Mocks httpx.get so the tests are pure unit (no live Supabase).
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

from tools.cyl_tools import list_traits_tool  # noqa: E402


def _mock_response(trait_names: list[str]):
    return MagicMock(
        status_code=200,
        json=lambda: [{"id": i + 1, "name": name} for i, name in enumerate(trait_names)],
    )


@patch("tools.cyl_tools.httpx.get")
def test_emits_followup_actions_one_per_trait(mock_get):
    mock_get.return_value = _mock_response(["leaf_count", "primary_length", "total_length"])

    result = list_traits_tool.invoke({})

    assert "followup_actions" in result
    assert len(result["followup_actions"]) == 3
    labels = [chip["label"] for chip in result["followup_actions"]]
    assert labels == ["leaf_count", "primary_length", "total_length"]


@patch("tools.cyl_tools.httpx.get")
def test_each_chip_has_label_and_prompt(mock_get):
    mock_get.return_value = _mock_response(["primary_length"])

    result = list_traits_tool.invoke({})

    chip = result["followup_actions"][0]
    assert chip["label"] == "primary_length"
    assert chip["prompt"] == "Show me stats for primary_length"


@patch("tools.cyl_tools.httpx.get")
def test_chips_capped_at_twenty(mock_get):
    many = [f"trait_{i:02d}" for i in range(50)]
    mock_get.return_value = _mock_response(many)

    result = list_traits_tool.invoke({})

    assert result["count"] == 50
    assert len(result["traits"]) == 50
    assert len(result["followup_actions"]) == 20
    assert result["followup_actions"][0]["label"] == "trait_00"
    assert result["followup_actions"][-1]["label"] == "trait_19"


@patch("tools.cyl_tools.httpx.get")
def test_empty_trait_list_returns_empty_followup_actions(mock_get):
    mock_get.return_value = _mock_response([])

    result = list_traits_tool.invoke({})

    assert result["count"] == 0
    assert result["traits"] == []
    assert result["followup_actions"] == []


@patch("tools.cyl_tools.httpx.get")
def test_original_keys_preserved(mock_get):
    mock_get.return_value = _mock_response(["leaf_count", "primary_length"])

    result = list_traits_tool.invoke({})

    assert result["count"] == 2
    assert result["traits"] == ["leaf_count", "primary_length"]
    assert "hint" in result
    assert "get_trait_growth_stats_tool" in result["hint"]
