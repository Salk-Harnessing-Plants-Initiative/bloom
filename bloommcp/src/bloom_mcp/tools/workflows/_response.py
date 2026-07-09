"""Shared response shape for every workflow MCP tool.

Workflows return a small structured payload — manifest path + headline stats
+ optional plot URL. Bulky data (per-row tables, full matrices, cluster
labels) lives on disk inside the versioned directory and is read back via
`load_experiment_data(version=...)` when the LLM needs it.

The shape is intentionally MCP-client-agnostic: the bloom chat UI extracts
`plot_url` / `followup_actions` as SSE trigger keys for inline rendering;
Claude Desktop receives the same JSON and lets the LLM verbalize it. No
client-specific fields.
"""

from __future__ import annotations

from typing import Any, TypedDict


class FollowupAction(TypedDict):
    label: str
    prompt: str


class WorkflowResponse(TypedDict, total=False):
    version_id: str
    version_dir: str
    manifest_path: str
    summary: dict[str, Any]
    outputs: dict[str, str]
    plot_url: str
    plot_layout: str
    followup_actions: list[FollowupAction]


REQUIRED_KEYS: tuple[str, ...] = (
    "version_id",
    "version_dir",
    "manifest_path",
    "summary",
    "outputs",
)

OPTIONAL_KEYS: tuple[str, ...] = (
    "plot_url",
    "plot_layout",
    "followup_actions",
)
