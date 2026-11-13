"""@as_mcp_tool sends a tool's images next to its validated JSON."""

from __future__ import annotations

import base64

import pytest
from fastmcp.tools import ToolResult
from pydantic import BaseModel, ConfigDict

from bloom_mcp.contract import BloomMCPError, ToolImage, WithImages, as_mcp_tool


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")
    n: int


class _Out(BaseModel):
    n: int


def _tool(images: list[ToolImage], result: object = None):
    @as_mcp_tool(input_model=_In, output_model=_Out)
    def tool(params: _In):
        return WithImages(result if result is not None else _Out(n=params.n), images)

    return tool


def test_images_follow_the_json():
    image = ToolImage(b"\x89PNG-bytes", "image/png")
    out = _tool([image])({"n": 3})
    assert isinstance(out, ToolResult)
    assert out.structured_content == {"n": 3}
    text, picture = out.content
    assert text.type == "text" and text.text == '{"n":3}'
    assert picture.type == "image" and picture.mimeType == "image/png"
    assert base64.b64decode(picture.data) == b"\x89PNG-bytes"


def test_images_keep_their_order():
    images = [ToolImage(bytes([i]), "image/jpeg") for i in range(3)]
    out = _tool(images)({"n": 1})
    assert [base64.b64decode(c.data) for c in out.content[1:]] == [b"\0", b"\1", b"\2"]


def test_the_json_is_still_validated():
    with pytest.raises(BloomMCPError) as err:
        _tool([], result={"n": "not a number"})({"n": 1})
    assert err.value.code == "internal_output_contract"


def test_a_plain_result_is_unchanged():
    @as_mcp_tool(input_model=_In, output_model=_Out)
    def tool(params: _In) -> _Out:
        return _Out(n=params.n)

    assert tool({"n": 2}) == _Out(n=2)


def test_a_label_is_sent_just_before_its_image():
    images = [ToolImage(b"a", "image/png", "scan 1"), ToolImage(b"b", "image/png")]
    out = _tool(images)({"n": 1})
    kinds = [(c.type, getattr(c, "text", None)) for c in out.content]
    assert kinds == [
        ("text", '{"n":1}'),
        ("text", "scan 1"),
        ("image", None),
        ("image", None),
    ]
