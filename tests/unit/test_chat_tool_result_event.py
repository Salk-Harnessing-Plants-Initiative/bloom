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

from sse_events import tool_result_event as _tool_result_event  # noqa: E402


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


def test_skips_event_for_plot_url_payload():
    output = {
        "plot_url": "http://localhost/plots/cyl_supabase_dist_abc.png",
        "n": 200,
        "trait_name": "primary_length",
    }
    assert _tool_result_event("plot_trait_distribution_supabase_tool", output) is None


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
