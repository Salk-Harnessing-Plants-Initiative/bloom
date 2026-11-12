"""Images a tool returns next to its JSON result, as MCP image content Claude can view."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field

from fastmcp.tools.tool import ToolResult
from mcp.types import ImageContent, TextContent
from pydantic import BaseModel


@dataclass(frozen=True)
class ToolImage:
    data: bytes
    mime_type: str


@dataclass(frozen=True)
class WithImages:
    """A tool's result plus images; @as_mcp_tool validates `result` and sends both."""

    result: BaseModel
    images: list[ToolImage] = field(default_factory=list)


def tool_result(result: BaseModel, images: list[ToolImage]) -> ToolResult:
    """The JSON as text and structured content, then each image."""
    content: list[TextContent | ImageContent] = [
        TextContent(type="text", text=result.model_dump_json())
    ]
    content += [
        ImageContent(
            type="image",
            data=base64.b64encode(i.data).decode("ascii"),
            mimeType=i.mime_type,
        )
        for i in images
    ]
    return ToolResult(
        content=content, structured_content=result.model_dump(mode="json")
    )
