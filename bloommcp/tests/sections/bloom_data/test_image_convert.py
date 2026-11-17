"""to_png: a stored scan image to a PNG Claude can view, crops at full resolution."""

from __future__ import annotations

import io
import struct
import zlib

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


def _png_of(data: bytes, crop: ic.Crop | None = None) -> ic.Converted:
    return ic.to_png(data, crop=crop, max_bytes=BIG)


def test_a_small_tiff_comes_back_pixel_for_pixel():
    original = _noise(300, 200, "RGB")
    out = _png_of(_encode(original, compression="tiff_lzw"))
    assert (out.width, out.height) == (out.shown_width, out.shown_height) == (300, 200)
    assert np.array_equal(np.asarray(_open(out.png)), np.asarray(original))
    assert out.mode == "RGB" and out.bits == 8 and not out.more_pages
    assert out.full_scale is None and out.peak is None and not out.dim


def test_a_large_image_is_scaled_to_the_longest_side_claude_sees():
    out = _png_of(_encode(_noise(4000, 1000)))
    assert (out.width, out.height) == (4000, 1000)
    assert (out.shown_width, out.shown_height) == (ic.MAX_SIDE, 392)
    assert _open(out.png).size == (ic.MAX_SIDE, 392)


def test_scaling_matches_lanczos():
    original = _noise(3200, 40)
    shown = np.asarray(_open(_png_of(_encode(original)).png))
    for other in (Image.Resampling.NEAREST, Image.Resampling.BILINEAR):
        assert not np.array_equal(
            shown, np.asarray(original.resize(shown.shape[::-1], other))
        )
    expected = original.resize(shown.shape[::-1], Image.Resampling.LANCZOS)
    assert np.array_equal(shown, np.asarray(expected))


def test_an_image_exactly_the_longest_side_is_not_resampled():
    original = _noise(ic.MAX_SIDE, 10)
    out = _png_of(_encode(original))
    assert np.array_equal(np.asarray(_open(out.png)), np.asarray(original))


def test_a_crop_keeps_full_resolution():
    original = _noise(4000, 3000)
    out = _png_of(_encode(original), ic.Crop(x=1000, y=500, width=800, height=600))
    assert (out.shown_width, out.shown_height) == (800, 600)
    expected = np.asarray(original)[500:1100, 1000:1800]
    assert np.array_equal(np.asarray(_open(out.png)), expected)


@pytest.mark.parametrize(
    "crop,reason",
    [
        (ic.Crop(x=3900, y=0, width=200, height=10), "past"),
        (ic.Crop(x=0, y=250, width=10, height=100), "past"),
        (ic.Crop(x=0, y=0, width=0, height=10), "width"),
        (ic.Crop(x=-1, y=0, width=10, height=10), "x, y"),
    ],
)
def test_a_crop_outside_the_image_is_refused(crop, reason):
    with pytest.raises(ic.BadImageRequest, match=reason):
        _png_of(_encode(_noise(4000, 300)), crop)


def test_a_crop_on_the_exact_edge_is_allowed():
    out = _png_of(
        _encode(_noise(400, 300)), ic.Crop(x=300, y=200, width=100, height=100)
    )
    assert (out.shown_width, out.shown_height) == (100, 100)


def test_a_16_bit_scan_uses_the_fixed_full_scale_plate_videos_use():
    pixels = np.full((32, 64), 32768)
    pixels[0, 0] = 65535  # a hot pixel must not darken the rest
    out = _png_of(_deep(pixels))
    shown = np.asarray(_open(out.png))
    assert out.mode.startswith("I;16") and out.bits == 16
    assert (out.full_scale, out.peak, out.dim) == (65535, 65535, False)
    assert shown[5, 5] == 128 and shown[0, 0] == 255


def test_a_flat_16_bit_scan_is_mid_grey_not_black():
    shown = np.asarray(_open(_png_of(_deep(np.full((8, 8), 32768))).png))
    assert set(np.unique(shown)) == {128}


@pytest.mark.parametrize("peak,dim", [(32766, True), (32767, False), (4095, True)])
def test_a_deep_scan_under_half_scale_is_dim(peak, dim):
    out = _png_of(_deep(np.full((8, 8), peak)))
    assert out.dim is dim and out.peak == peak


def test_32_bit_values_past_the_full_scale_are_refused_not_clipped():
    pixels = np.full((8, 8), 70000, dtype=np.int32)
    with pytest.raises(ic.ImageUnreadable, match="outside 0 to 65535"):
        _png_of(_encode(Image.fromarray(pixels, mode="I")))


def _png48(width: int, height: int, value: int) -> bytes:
    """A 16-bit-per-channel RGB PNG, which Pillow opens as 8-bit RGB."""

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + kind
            + body
            + struct.pack(">I", zlib.crc32(kind + body))
        )

    row = b"\0" + struct.pack(f">{width * 3}H", *[value] * (width * 3))
    header = struct.pack(">IIBBBBB", width, height, 16, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(row * height))
        + chunk(b"IEND", b"")
    )


def test_16_bit_colour_is_reported_as_16_bit():
    out = _png_of(_png48(12, 10, 32768))
    assert out.mode == "RGB" and out.bits == 16 and out.full_scale == 65535
    assert np.asarray(_open(out.png)).max() == 128 and not out.dim
    dark = _png_of(_png48(12, 10, 4096))
    assert dark.dim and dark.peak < 32767


def test_a_crop_and_the_whole_image_show_the_same_pixels_the_same():
    pixels = np.tile(np.linspace(0, 65535, 64), (64, 1))
    whole = np.asarray(_open(_png_of(_deep(pixels)).png))
    part = np.asarray(
        _open(_png_of(_deep(pixels), ic.Crop(x=10, y=10, width=20, height=20)).png)
    )
    assert np.array_equal(part, whole[10:30, 10:30])


@pytest.mark.parametrize("width", [1569, 4000])
def test_a_large_16_bit_scan_is_shown_at_the_full_longest_side(width):
    out = _png_of(_deep(np.full((100, width), 30000)))
    assert out.shown_width == ic.MAX_SIDE


def test_a_large_16_bit_crop_is_shown_at_the_full_longest_side():
    crop = ic.Crop(x=0, y=0, width=2000, height=1000)
    out = _png_of(_deep(np.full((1200, 2500), 30000)), crop)
    assert (out.shown_width, out.shown_height) == (ic.MAX_SIDE, 784)


def test_a_multi_page_tiff_shows_its_first_page_and_says_there_are_more():
    frames = [Image.new("L", (40, 30), 10), Image.new("L", (60, 50), 200)]
    out = _png_of(_encode(frames[0], save_all=True, append_images=frames[1:]))
    assert out.more_pages and (out.width, out.height) == (40, 30)
    shown = _open(out.png)
    assert shown.size == (40, 30) and np.asarray(shown).max() == 10


@pytest.mark.parametrize(
    "mode,fmt,shown",
    [
        ("1", "TIFF", "L"),
        ("P", "PNG", "RGB"),
        ("CMYK", "TIFF", "RGB"),
        ("LAB", "TIFF", "RGB"),
        ("LA", "PNG", "RGBA"),
    ],
)
def test_other_modes_become_viewable(mode, fmt, shown):
    out = _png_of(_encode(Image.new(mode, (20, 10)), fmt))
    assert _open(out.png).mode == shown and out.full_scale is None


def test_rgbx_becomes_rgb():
    image, stretched = ic._viewable(Image.new("RGBX", (4, 4), (10, 20, 30, 0)))
    assert image.mode == "RGB" and image.getpixel((0, 0)) == (10, 20, 30)
    assert stretched is None


def test_a_float_image_is_refused_not_guessed():
    with pytest.raises(ic.ImageUnreadable, match="floating-point"):
        _png_of(_encode(Image.new("F", (10, 10))))


def test_a_png_over_the_byte_cap_is_sent_smaller_still_lossless():
    big = _png_of(_encode(_noise(3000, 3000)))
    out = ic.to_png(_encode(_noise(3000, 3000)), crop=None, max_bytes=len(big.png) - 1)
    assert out.shown_width == ic.FALLBACK_SIDES[0] and len(out.png) < len(big.png)


def test_the_smallest_fallback_is_tried_last():
    data = _encode(_noise(3000, 3000))
    middle = len(ic.to_png(data, crop=None, max_bytes=BIG).png)
    sizes = {}
    for side in ic.FALLBACK_SIDES:
        fitted = ic._fit(Image.open(io.BytesIO(data)), side)
        sizes[side] = len(ic._png(fitted))
    out = ic.to_png(data, crop=None, max_bytes=sizes[900])
    assert out.shown_width == 900 and middle > sizes[1200] > sizes[900]


def test_nothing_fits_the_byte_cap():
    with pytest.raises(ic.PngOverBudget):
        ic.to_png(_encode(_noise(2000, 2000)), crop=None, max_bytes=100)


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


def test_memory_is_counted_like_plate_videos():
    assert ic.decoded_bytes(10, 10, "L") == 10 * 10 * 7
    assert ic.decoded_bytes(10, 10, "I;16") == 10 * 10 * 15
    # The largest real plate region, 8818 x 8818 at 1600 dpi, fits.
    assert ic.decoded_bytes(8818, 8818, "RGB") <= ic.MAX_DECODED_BYTES


def test_an_image_too_large_to_decode_is_refused_before_decoding(monkeypatch):
    monkeypatch.setattr(ic, "MAX_DECODED_BYTES", 20 * 20 * 7)
    _png_of(_encode(_noise(20, 20)))
    with pytest.raises(ic.ImageTooLarge) as err:
        _png_of(_encode(_noise(21, 20)))
    assert not isinstance(err.value, ic.PngOverBudget)


def test_pillows_own_bomb_check_reads_as_too_large(monkeypatch):
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 50)
    with pytest.raises(ic.ImageTooLarge):
        _png_of(_encode(_noise(20, 20)))


def test_a_jpeg_with_too_many_scans_never_reaches_pillow(monkeypatch):
    def must_not_open(*args, **kwargs):
        raise AssertionError("Pillow opened a crafted JPEG")

    crafted = b"\xff\xd8\xff\xe0" + b"\xff\xda\x00\x08" * (ic.MAX_JPEG_SCANS + 1)
    monkeypatch.setattr(ic.Image, "open", must_not_open)
    with pytest.raises(ic.ImageTooLarge, match="scans"):
        _png_of(crafted)


@pytest.mark.parametrize("progressive", [False, True])
def test_real_jpegs_pass_the_scan_check(progressive):
    data = _encode(_noise(200, 100, "RGB"), "JPEG", progressive=progressive)
    assert data.count(b"\xff\xda") <= ic.MAX_JPEG_SCANS
    assert _png_of(data).shown_width == 200
