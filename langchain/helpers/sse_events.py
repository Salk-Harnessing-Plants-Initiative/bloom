"""SSE event builders for the agent's chat stream. Pure stdlib so unit tests
can import without pulling in fastapi/langchain.
"""
from __future__ import annotations

import json


def tool_result_event(tool_name: str, output) -> str | None:
    """Build a `tool_result` SSE line if the tool's output is a structured
    payload the UI should render (suggestions or sample_traits). Returns None
    for every other output shape so the caller can skip emission.
    """
    payload = getattr(output, "content", output)
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (json.JSONDecodeError, ValueError):
            return None
    if not isinstance(payload, dict):
        return None
    if "suggestions" not in payload and "sample_traits" not in payload:
        return None
    return f"data: {json.dumps({'type': 'tool_result', 'tool': tool_name, 'result': payload})}\n\n"
