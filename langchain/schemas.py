"""Pydantic request/response models shared by the chat and thread endpoints."""
from typing import Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    prompt: str
    provider: str = "openai"  # "openai" or "local"
    model: Optional[str] = None  # Defaults to first model for provider
    tool_set: str = "all"  # "all", "scrna", "cyl", "generic"
    mcp_tool_names: list[str] = Field(default_factory=list)  # Filter MCP tools by name (empty = foundational only)
    thread_id: str = "default"  # Conversation thread ID for memory persistence


class ChatResponse(BaseModel):
    answer: str
    tools_used: list[str]
    provider: str
    model: str


class ChatResumeRequest(BaseModel):
    """Resume a chat that paused on an ask_user clarification.

    Symmetric with ChatRequest minus `prompt` (replaced with `reply`).
    Frontend should keep provider/model/tool_set/mcp_tool_names in sync
    with the original chat session — same agent factory must be reused
    so the resumed graph picks up where it paused.
    """
    thread_id: str
    reply: str
    provider: str = "openai"
    model: Optional[str] = None
    tool_set: str = "all"
    mcp_tool_names: list[str] = Field(default_factory=list)


class CreateThreadRequest(BaseModel):
    thread_id: str
    title: Optional[str] = None


class ModelsResponse(BaseModel):
    models: dict[str, list[str]]
