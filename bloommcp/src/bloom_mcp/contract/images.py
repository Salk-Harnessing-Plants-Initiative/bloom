"""Images a tool returns next to its JSON result, as MCP image content Claude can view."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field

from fastmcp.tools import ToolResult
from mcp.types import ImageContent, TextContent
from pydantic import BaseModel


@dataclass(frozen=True)
class ToolImage:
    data: bytes
    mime_type: str
    # A line sent just before the image, saying which file it is.
    label: str | None = None


@dataclass(frozen=True)
class WithImages:
    """A tool's result plus images; @as_mcp_tool validates `result` and sends both."""

    result: BaseModel
    images: list[ToolImage] = field(default_factory=list)


def tool_result(result: BaseModel, images: list[ToolImage]) -> ToolResult:
    """The JSON as text and structured content, then each image after its label."""
    content: list[TextContent | ImageContent] = [
        TextContent(type="text", text=result.model_dump_json())
    ]
    for i in images:
        if i.label:
            content.append(TextContent(type="text", text=i.label))
        data = base64.b64encode(i.data).decode("ascii")
        content.append(ImageContent(type="image", data=data, mimeType=i.mime_type))
    return ToolResult(
        content=content, structured_content=result.model_dump(mode="json")
    )
