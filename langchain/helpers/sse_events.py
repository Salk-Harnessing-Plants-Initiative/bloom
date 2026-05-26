"""SSE event builders for the agent's chat stream. Pure stdlib so unit tests
can import without pulling in fastapi/langchain.
"""
from __future__ import annotations

import json


_TRIGGER_KEYS = ("suggestions", "sample_traits", "followup_actions")
_UI_KEYS = _TRIGGER_KEYS + ("trait_name",)


def tool_result_event(tool_name: str, output) -> str | None:
    """Build a `tool_result` SSE line if the tool's output is a structured
    payload the UI should render. Triggers on any of: `suggestions` (typo
    fuzzy-match), `sample_traits` (no-match alphabetical sample), or
    `followup_actions` (universal action chips for listing tools, HITL).
    Forwards only UI-consumed keys so bulky tool payloads (traits / experiments
    lists) don't duplicate over the wire.
    Returns None for every other output shape so the caller can skip emission.
    """
    payload = getattr(output, "content", output)
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (json.JSONDecodeError, ValueError):
            return None
    if not isinstance(payload, dict):
        return None
    if not any(key in payload for key in _TRIGGER_KEYS):
        return None
    ui_payload = {k: payload[k] for k in _UI_KEYS if k in payload}
    return f"data: {json.dumps({'type': 'tool_result', 'tool': tool_name, 'result': ui_payload})}\n\n"
