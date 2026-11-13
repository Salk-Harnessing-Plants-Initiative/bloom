"""to_png: a stored scan image to a PNG Claude can view, crops at full resolution."""

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
    shape = (height, width, 3) if mode == "RGB" else (height, width)
    return Image.fromarray(rng.integers(0, 256, shape, dtype=np.uint8))


def _deep(pixels: np.ndarray) -> bytes:
    return _encode(Image.fromarray(pixels.astype(np.uint16)))


def _png_of(data: bytes, **kw) -> ic.Converted:
    return ic.to_png(data, page=kw.get("page", 0), crop=kw.get("crop"), max_bytes=BIG)


def test_a_small_tiff_comes_back_pixel_for_pixel():
    original = _noise(300, 200, "RGB")
    out = _png_of(_encode(original, compression="tiff_lzw"))
    assert (out.width, out.height) == (out.shown_width, out.shown_height) == (300, 200)
    assert np.array_equal(np.asarray(_open(out.png)), np.asarray(original))
    assert out.mode == "RGB" and not out.more_pages and out.full_scale is None


def test_a_large_image_is_scaled_to_the_longest_side_claude_sees():
    out = _png_of(_encode(_noise(4000, 1000)))
    assert (out.width, out.height) == (4000, 1000)
    assert (out.shown_width, out.shown_height) == (ic.MAX_SIDE, 392)
    assert _open(out.png).size == (ic.MAX_SIDE, 392)


def test_scaling_uses_lanczos_not_nearest():
    stripes = np.zeros((10, 3200), dtype=np.uint8)
    stripes[:, ::2] = 255
    shown = np.asarray(_open(_png_of(_encode(Image.fromarray(stripes))).png))
    # Averaging filters turn 1-pixel stripes into mid grey; nearest keeps 0 and 255.
    assert 50 < shown.mean() < 205 and len(np.unique(shown)) > 2


def test_an_image_exactly_the_longest_side_is_not_resampled():
    original = _noise(ic.MAX_SIDE, 10)
    out = _png_of(_encode(original))
    assert np.array_equal(np.asarray(_open(out.png)), np.asarray(original))


def test_a_crop_keeps_full_resolution():
    original = _noise(4000, 3000)
    crop = ic.Crop(x=1000, y=500, width=800, height=600)
    out = _png_of(_encode(original), crop=crop)
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
        _png_of(_encode(_noise(4000, 300)), crop=crop)


def test_a_crop_on_the_exact_edge_is_allowed():
    out = _png_of(
        _encode(_noise(400, 300)), crop=ic.Crop(x=300, y=200, width=100, height=100)
    )
    assert (out.shown_width, out.shown_height) == (100, 100)


def test_a_16_bit_scan_uses_a_fixed_full_scale_not_its_own_range():
    pixels = np.full((32, 64), 32768)
    pixels[0, 0] = 65535  # a hot pixel must not darken the rest
    out = _png_of(_deep(pixels))
    shown = np.asarray(_open(out.png))
    assert out.mode.startswith("I;16") and out.full_scale == 65535 and not out.dim
    assert shown[5, 5] == 128 and shown[0, 0] == 255


def test_a_flat_16_bit_scan_is_mid_grey_not_black():
    shown = np.asarray(_open(_png_of(_deep(np.full((8, 8), 32768))).png))
    assert set(np.unique(shown)) == {128}


class _Tagged:
    def __init__(self, bits):
        self.tag_v2 = {258: bits}


@pytest.mark.parametrize(
    "bits,expected", [(12, 12), ((14,), 14), (16, 16), (8, 16), (32, 16), (None, 16)]
)
def test_the_full_scale_comes_from_the_tiff_bit_depth(bits, expected):
    assert ic._bits(_Tagged(bits)) == expected


def test_a_dim_deep_scan_is_flagged():
    out = _png_of(_deep(np.full((8, 8), 1000)))
    assert out.dim and out.full_scale == 65535


def test_a_crop_and_the_whole_image_show_the_same_pixels_the_same():
    pixels = np.tile(np.linspace(0, 65535, 64), (64, 1))
    whole = np.asarray(_open(_png_of(_deep(pixels)).png))
    crop = ic.Crop(x=10, y=10, width=20, height=20)
    part = np.asarray(_open(_png_of(_deep(pixels), crop=crop).png))
    assert np.array_equal(part, whole[10:30, 10:30])


@pytest.mark.parametrize("width", [1569, 4000])
def test_a_large_16_bit_scan_is_shown_at_the_full_longest_side(width):
    out = _png_of(_deep(np.full((100, width), 30000)))
    assert out.shown_width == ic.MAX_SIDE


def test_a_large_16_bit_crop_is_shown_at_the_full_longest_side():
    crop = ic.Crop(x=0, y=0, width=2000, height=1000)
    out = _png_of(_deep(np.full((1200, 2500), 30000)), crop=crop)
    assert (out.shown_width, out.shown_height) == (ic.MAX_SIDE, 784)


def test_pages_of_a_multi_page_tiff():
    frames = [Image.new("L", (40, 30), 10), Image.new("L", (60, 50), 200)]
    data = _encode(frames[0], save_all=True, append_images=frames[1:])
    first = _png_of(data)
    second = _png_of(data, page=1)
    assert first.more_pages and not second.more_pages
    assert (second.width, second.height) == (60, 50)
    assert np.asarray(_open(second.png)).max() == 200
    with pytest.raises(ic.BadImageRequest, match="page 2"):
        _png_of(data, page=2)


def test_a_later_page_is_checked_against_the_size_limit(monkeypatch):
    frames = [Image.new("L", (10, 10)), Image.new("L", (100, 100))]
    data = _encode(frames[0], save_all=True, append_images=frames[1:])
    monkeypatch.setattr(ic, "MAX_PIXELS", 1000)
    assert _png_of(data).width == 10
    with pytest.raises(ic.ImageTooLarge):
        _png_of(data, page=1)


@pytest.mark.parametrize("mode", ["1", "P", "CMYK", "RGBX", "LAB"])
def test_eight_bit_modes_become_rgb(mode):
    out = _png_of(_encode(Image.new(mode, (20, 10)), "TIFF" if mode != "P" else "PNG"))
    assert _open(out.png).mode == "RGB" and out.full_scale is None


def test_grey_with_alpha_keeps_it():
    out = _png_of(_encode(Image.new("LA", (20, 10)), "PNG"))
    assert _open(out.png).mode == "RGBA"


def test_a_float_image_is_refused_not_guessed():
    with pytest.raises(ic.ImageUnreadable, match="no fixed scale"):
        _png_of(_encode(Image.new("F", (10, 10))))


def test_a_png_over_the_byte_cap_is_sent_smaller_still_lossless():
    big = _png_of(_encode(_noise(3000, 3000)))
    out = ic.to_png(
        _encode(_noise(3000, 3000)), page=0, crop=None, max_bytes=len(big.png) - 1
    )
    assert out.shown_width in ic.FALLBACK_SIDES and len(out.png) < len(big.png)


def test_nothing_fits_the_byte_cap():
    with pytest.raises(ic.ImageTooLarge):
        ic.to_png(_encode(_noise(2000, 2000)), page=0, crop=None, max_bytes=100)


@pytest.mark.parametrize("fmt", ["PNG", "TIFF", "JPEG"])
def test_a_truncated_file_is_unreadable_not_a_crash(fmt):
    whole = _encode(_noise(500, 500), fmt)
    with pytest.raises(ic.ImageUnreadable) as err:
        _png_of(whole[: len(whole) // 3])
    assert not isinstance(err.value, ic.ImageTooLarge) and "damaged" in str(err.value)


def test_not_an_image():
    with pytest.raises(ic.ImageUnreadable) as err:
        _png_of(b"not an image")
    assert not isinstance(err.value, ic.ImageTooLarge)


def test_formats_other_than_scan_formats_are_not_parsed():
    with pytest.raises(ic.ImageUnreadable):
        _png_of(_encode(_noise(10, 10), "BMP"))


def test_too_many_pixels_is_refused_before_decoding(monkeypatch):
    monkeypatch.setattr(ic, "MAX_PIXELS", 100)
    with pytest.raises(ic.ImageTooLarge):
        _png_of(_encode(_noise(20, 20)))


def test_too_many_decoded_bytes_is_refused(monkeypatch):
    monkeypatch.setattr(ic, "MAX_DECODED_BYTES", 400)
    _png_of(_encode(_noise(20, 20)))  # 400 bytes as 8-bit grey
    with pytest.raises(ic.ImageTooLarge):
        _png_of(_deep(np.zeros((20, 20))))  # 800 bytes as 16-bit


def test_pillows_own_bomb_check_reads_as_too_large(monkeypatch):
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 50)
    with pytest.raises(ic.ImageTooLarge):
        _png_of(_encode(_noise(20, 20)))
