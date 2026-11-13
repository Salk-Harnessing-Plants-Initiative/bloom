"""tiff_problem: a crafted TIFF is refused from its header, before Pillow opens it."""

from __future__ import annotations

import io
import struct

import numpy as np
import pytest
from PIL import Image

from bloom_mcp.data_access import image_convert as ic
from bloom_mcp.data_access import tiff_header as th


def _classic(entries: list[tuple[int, int]], order: str = "<", second=None) -> bytes:
    """A classic TIFF header: one directory of (tag, value count) entries."""
    magic = b"II" if order == "<" else b"MM"
    body = struct.pack(order + "H", len(entries))
    for tag, count in entries:
        body += struct.pack(order + "HHII", tag, 4, count, 0)
    next_at = 8 + len(body) + 4 if second else 0
    body += struct.pack(order + "I", next_at)
    data = magic + struct.pack(order + "HI", 42, 8) + body
    if second:
        tail = struct.pack(order + "H", len(second))
        for tag, count in second:
            tail += struct.pack(order + "HHII", tag, 4, count, 0)
        data += tail + struct.pack(order + "I", 0)
    return data


def _bigtiff(entries: list[tuple[int, int]]) -> bytes:
    body = struct.pack("<Q", len(entries))
    for tag, count in entries:
        body += struct.pack("<HHQQ", tag, 4, count, 0)
    body += struct.pack("<Q", 0)
    return b"II" + struct.pack("<HHHQ", 43, 8, 0, 16) + body


def _tiff(image: Image.Image, **kw) -> bytes:
    out = io.BytesIO()
    image.save(out, format="TIFF", **kw)
    return out.getvalue()


@pytest.mark.parametrize(
    "data",
    [
        _classic([(256, 1), (273, 20_000_000), (279, 20_000_000)]),
        _classic([(324, 5_000_000)], order=">"),
        _classic([(256, 1)], second=[(273, 20_000_000)]),
        _bigtiff([(273, 20_000_000)]),
    ],
)
def test_millions_of_strips_or_tiles_are_refused(data):
    assert "strips or tiles" in th.tiff_problem(data)


def test_a_directory_with_too_many_entries_is_refused():
    data = _classic([(300 + i, 1) for i in range(th.MAX_ENTRIES + 1)])
    assert "entries" in th.tiff_problem(data)


def test_one_strip_per_row_of_the_largest_plate_is_fine():
    assert th.tiff_problem(_classic([(273, 8818), (279, 8818)])) is None


@pytest.mark.parametrize(
    "image,kw",
    [
        (Image.new("L", (300, 200), 9), {}),
        (Image.new("RGB", (300, 200)), {"compression": "tiff_lzw"}),
        (Image.fromarray(np.zeros((50, 60), dtype=np.uint16)), {}),
    ],
)
def test_real_tiffs_pass(image, kw):
    assert th.tiff_problem(_tiff(image, **kw)) is None


def test_a_real_multi_page_tiff_passes():
    frames = [Image.new("L", (40, 30)), Image.new("L", (60, 50))]
    assert (
        th.tiff_problem(_tiff(frames[0], save_all=True, append_images=frames[1:]))
        is None
    )


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\x89PNG\r\n\x1a\n" + b"\0" * 20,
        b"II*\0",
        b"II*\0\xff\xff\xff\xff" + b"\0" * 8,
    ],
)
def test_anything_else_is_left_to_pillow(data):
    assert th.tiff_problem(data) is None


def test_pillow_never_opens_a_crafted_tiff(monkeypatch):
    def must_not_open(*args, **kwargs):
        raise AssertionError("Pillow opened a crafted TIFF")

    monkeypatch.setattr(ic.Image, "open", must_not_open)
    with pytest.raises(ic.ImageTooLarge, match="strips or tiles"):
        ic.to_png(_classic([(273, 20_000_000)]), crop=None, max_bytes=10_000_000)
