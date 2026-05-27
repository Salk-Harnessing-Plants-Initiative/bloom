"""Unit tests for the tool_result_event helper.

Verifies the rules for when chat_stream emits the new tool_result SSE event:
fires for dict outputs containing `suggestions` or `sample_traits`; skipped
for every other shape (success payloads, strings that don't parse, None).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "langchain"))

from helpers.sse_events import tool_result_event as _tool_result_event  # noqa: E402


def test_emits_event_for_suggestions_payload():
    output = {
        "error": "trait not found",
        "trait_name": "root_lenght",
        "suggestions": ["primary_length", "total_length"],
        "sample_traits": None,
    }
    line = _tool_result_event("compare_trait_between_experiments_tool", output)
    assert line is not None
    parsed = json.loads(line.removeprefix("data: ").strip())
    assert parsed["type"] == "tool_result"
    assert parsed["tool"] == "compare_trait_between_experiments_tool"
    assert parsed["result"]["suggestions"] == ["primary_length", "total_length"]


def test_emits_event_for_sample_traits_only_payload():
    output = {
        "error": "trait not found",
        "trait_name": "velocity_x",
        "suggestions": [],
        "sample_traits": ["leaf_count", "primary_length"],
    }
    line = _tool_result_event("compare_trait_between_experiments_tool", output)
    assert line is not None
    parsed = json.loads(line.removeprefix("data: ").strip())
    assert parsed["result"]["sample_traits"] == ["leaf_count", "primary_length"]


def test_skips_event_for_success_rows_payload():
    output = {
        "rows": [{"experiment_id": 1, "wave_number": 1, "n": 100, "mean": 45.2}],
        "trait_name": "primary_length",
        "experiment_a_name": "alfalfa-2024",
    }
    assert _tool_result_event("compare_trait_between_experiments_tool", output) is None


def test_skips_event_for_string_output():
    assert _tool_result_event("list_experiments_tool", "[{\"id\": 1}]") is None


def test_skips_event_for_none_output():
    assert _tool_result_event("any_tool", None) is None


def test_skips_event_for_list_output():
    assert _tool_result_event("list_experiments_tool", [{"id": 1, "name": "x"}]) is None


def test_handles_toolmessage_wrapper_with_dict_content():
    """LangChain may wrap tool outputs in a ToolMessage; the helper should
    unwrap via .content and parse strings if needed."""

    class FakeToolMessage:
        def __init__(self, content):
            self.content = content

    output = FakeToolMessage(
        content={"suggestions": ["primary_length"], "sample_traits": None}
    )
    line = _tool_result_event("compare_trait_between_experiments_tool", output)
    assert line is not None


def test_handles_toolmessage_wrapper_with_json_string_content():
    class FakeToolMessage:
        def __init__(self, content):
            self.content = content

    output = FakeToolMessage(
        content=json.dumps({"suggestions": ["primary_length"], "sample_traits": None})
    )
    line = _tool_result_event("compare_trait_between_experiments_tool", output)
    assert line is not None
    parsed = json.loads(line.removeprefix("data: ").strip())
    assert parsed["result"]["suggestions"] == ["primary_length"]


def test_emits_event_for_followup_actions_payload():
    output = {
        "traits": [{"id": 1, "name": "primary_length"}],
        "followup_actions": [
            {"label": "primary_length", "prompt": "Show me stats for primary_length"},
        ],
    }
    line = _tool_result_event("list_traits_tool", output)
    assert line is not None
    parsed = json.loads(line.removeprefix("data: ").strip())
    assert parsed["type"] == "tool_result"
    assert parsed["tool"] == "list_traits_tool"
    assert len(parsed["result"]["followup_actions"]) == 1
    assert parsed["result"]["followup_actions"][0]["label"] == "primary_length"


def test_emits_event_for_combined_followup_and_suggestions_payload():
    """Both shapes coexist in one payload — chip rendering surfaces both."""
    output = {
        "error": "trait not found",
        "trait_name": "primary_lenght",
        "suggestions": ["primary_length"],
        "sample_traits": None,
        "followup_actions": [
            {"label": "primary_length", "prompt": "Show me stats for primary_length"},
        ],
    }
    line = _tool_result_event("compare_trait_between_experiments_tool", output)
    assert line is not None
    parsed = json.loads(line.removeprefix("data: ").strip())
    assert parsed["result"]["suggestions"] == ["primary_length"]
    assert len(parsed["result"]["followup_actions"]) == 1


def test_emits_event_for_plot_url_only_payload():
    """plot_url alone (no chips, no suggestions) is a valid trigger."""
    output = {
        "plot_url": "http://localhost:5002/plots/cyl_supabase_accession_rank_abcd1234.png",
        "plot_layout": "boxplot",
    }
    line = _tool_result_event("compare_accessions_in_wave_tool", output)
    assert line is not None
    parsed = json.loads(line.removeprefix("data: ").strip())
    assert parsed["type"] == "tool_result"
    assert parsed["tool"] == "compare_accessions_in_wave_tool"
    assert parsed["result"]["plot_url"].endswith(".png")
    assert parsed["result"]["plot_layout"] == "boxplot"


def test_plot_url_payload_strips_bulky_keys():
    """Bulky non-UI keys (rankings table, scope, summary) must NOT make it
    over the wire — only plot_url + plot_layout (+ trait_name) are forwarded."""
    output = {
        "rankings": [
            {"rank": i, "accession_name": f"indi-{i}", "n": 20, "mean": i,
             "std": 1, "median": i, "min": 0, "max": 100}
            for i in range(50)
        ],
        "scope": {"wave_id": 12, "scan_mode": "latest_per_plant"},
        "summary": {"median_of_accession_medians": 25.0, "top_3": ["a", "b", "c"]},
        "trait_name": "primary_length",
        "n_accessions": 50,
        "plot_url": "http://localhost:5002/plots/cyl_supabase_x.png",
        "plot_layout": "ranked_profile",
    }
    line = _tool_result_event("compare_accessions_in_wave_tool", output)
    assert line is not None
    parsed = json.loads(line.removeprefix("data: ").strip())
    assert set(parsed["result"].keys()) == {"trait_name", "plot_url", "plot_layout"}
    assert "rankings" not in parsed["result"]
    assert "scope" not in parsed["result"]
    assert "summary" not in parsed["result"]
