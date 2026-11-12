"""to_png: any stored image to a lossless PNG Claude can view, crops at full resolution."""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from bloom_mcp.data_access import image_convert as ic

BIG = 10_000_000


def _encode(image: Image.Image, fmt: str = "TIFF", **kw) -> bytes:
    out = io.BytesIO()
    image.save(out, format=fmt, **kw)
    return out.getvalue()


def _open(png: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(png))
    assert image.format == "PNG"
    return image


def _noise(width: int, height: int, mode: str = "L", seed: int = 0) -> Image.Image:
    rng = np.random.default_rng(seed)
    if mode == "RGB":
        return Image.fromarray(rng.integers(0, 256, (height, width, 3), dtype=np.uint8))
    return Image.fromarray(rng.integers(0, 256, (height, width), dtype=np.uint8))


def test_a_small_tiff_comes_back_pixel_for_pixel():
    original = _noise(300, 200, "RGB")
    out = ic.to_png(
        _encode(original, compression="tiff_lzw"), page=0, crop=None, max_bytes=BIG
    )
    assert (out.width, out.height) == (out.shown_width, out.shown_height) == (300, 200)
    assert np.array_equal(np.asarray(_open(out.png)), np.asarray(original))
    assert out.mode == "RGB" and out.pages == 1 and not out.contrast_stretched


def test_a_large_image_is_scaled_to_the_longest_side_claude_sees():
    out = ic.to_png(_encode(_noise(4000, 1000)), page=0, crop=None, max_bytes=BIG)
    assert (out.width, out.height) == (4000, 1000)
    assert (out.shown_width, out.shown_height) == (ic.MAX_SIDE, 392)
    assert _open(out.png).size == (ic.MAX_SIDE, 392)


def test_a_crop_keeps_full_resolution():
    original = _noise(4000, 3000)
    crop = ic.Crop(x=1000, y=500, width=800, height=600)
    out = ic.to_png(_encode(original), page=0, crop=crop, max_bytes=BIG)
    assert (out.shown_width, out.shown_height) == (800, 600)
    expected = np.asarray(original)[500:1100, 1000:1800]
    assert np.array_equal(np.asarray(_open(out.png)), expected)


@pytest.mark.parametrize(
    "crop,reason",
    [
        (ic.Crop(x=3900, y=0, width=200, height=10), "past"),
        (ic.Crop(x=0, y=0, width=0, height=10), "width"),
        (ic.Crop(x=-1, y=0, width=10, height=10), "x, y"),
    ],
)
def test_a_crop_outside_the_image_is_refused(crop, reason):
    with pytest.raises(ic.BadImageRequest, match=reason):
        ic.to_png(_encode(_noise(4000, 300)), page=0, crop=crop, max_bytes=BIG)


def test_a_16_bit_scan_is_stretched_to_be_visible():
    pixels = np.linspace(1000, 3000, 64 * 32, dtype=np.uint16).reshape(32, 64)
    out = ic.to_png(_encode(Image.fromarray(pixels)), page=0, crop=None, max_bytes=BIG)
    shown = np.asarray(_open(out.png))
    assert out.mode.startswith("I;16") and out.contrast_stretched
    assert shown.dtype == np.uint8 and shown.min() == 0 and shown.max() == 255


def test_a_large_16_bit_scan_is_scaled_too():
    pixels = np.full((500, 4000), 2000, dtype=np.uint16)
    pixels[:, 2000:] = 4000
    out = ic.to_png(_encode(Image.fromarray(pixels)), page=0, crop=None, max_bytes=BIG)
    assert max(out.shown_width, out.shown_height) <= ic.MAX_SIDE
    shown = np.asarray(_open(out.png))
    assert shown[:, 0].max() == 0 and shown[:, -1].min() == 255


def test_pages_of_a_multi_page_tiff():
    frames = [Image.new("L", (40, 30), v) for v in (10, 200)]
    data = _encode(frames[0], save_all=True, append_images=frames[1:])
    second = ic.to_png(data, page=1, crop=None, max_bytes=BIG)
    assert second.pages == 2 and np.asarray(_open(second.png)).max() == 200
    with pytest.raises(ic.BadImageRequest, match="page 2"):
        ic.to_png(data, page=2, crop=None, max_bytes=BIG)


@pytest.mark.parametrize("mode", ["1", "P", "LA", "CMYK"])
def test_other_modes_become_viewable(mode):
    out = ic.to_png(
        _encode(Image.new(mode, (20, 10))), page=0, crop=None, max_bytes=BIG
    )
    assert _open(out.png).mode in ("L", "RGB", "RGBA")


def test_a_png_over_the_byte_cap_is_sent_smaller_still_lossless():
    big = ic.to_png(_encode(_noise(3000, 3000)), page=0, crop=None, max_bytes=BIG)
    out = ic.to_png(
        _encode(_noise(3000, 3000)), page=0, crop=None, max_bytes=len(big.png) - 1
    )
    assert out.shown_width in ic.FALLBACK_SIDES and len(out.png) < len(big.png)


def test_nothing_fits_the_byte_cap():
    with pytest.raises(ic.ImageUnreadable):
        ic.to_png(_encode(_noise(2000, 2000)), page=0, crop=None, max_bytes=100)


def test_not_an_image():
    with pytest.raises(ic.ImageUnreadable):
        ic.to_png(b"not an image", page=0, crop=None, max_bytes=BIG)


def test_too_many_pixels_is_refused_before_decoding(monkeypatch):
    monkeypatch.setattr(ic, "MAX_PIXELS", 100)
    with pytest.raises(ic.ImageUnreadable):
        ic.to_png(_encode(_noise(20, 20)), page=0, crop=None, max_bytes=BIG)
