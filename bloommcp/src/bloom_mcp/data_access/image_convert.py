"""Turns a stored scan image (TIFF, PNG, JPEG) into a PNG Claude can view.

Limits and the 16-bit reduction follow services/workflows/plate_encode.py, which
measured them on real plate scans, so a plate looks the same here as in its video.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np
from PIL import Image, UnidentifiedImageError

# Claude's vision scales a longer side down to about this, so larger gains no detail.
MAX_SIDE = 1568
# Smaller sides tried, in turn, when the PNG is over the per-image byte cap.
FALLBACK_SIDES = (1200, 900)
# Peak memory per source pixel, and the most one image may use (plate_encode's).
SHALLOW_BYTES_PER_PIXEL = 7
DEEP_BYTES_PER_PIXEL = 15
MAX_DECODED_BYTES = 560 * 1024**2
# Scan formats only; Pillow's other decoders are never reached.
FORMATS = ("PNG", "TIFF", "JPEG")
# Deep images are reduced from this fixed scale; one peaking under half of it is dim.
FULL_SCALE = 65535
DIM_PEAK = FULL_SCALE // 2
_TIFF_BITS_PER_SAMPLE = 258

_DEEP = frozenset({"I;16", "I;16B", "I;16L", "I;16N", "I"})
_TO_RGB = frozenset({"P", "CMYK", "YCbCr", "LAB", "HSV", "RGBX"})
_TO_RGBA = frozenset({"LA", "PA", "La", "RGBa"})


class ImageUnreadable(Exception):
    """Not an image this tool can show; the message says why, for Claude."""


class ImageTooLarge(ImageUnreadable):
    """Too large to decode within this tool's memory limit."""


class PngOverBudget(ImageTooLarge):
    """Decoded fine, but even the smallest PNG is over the bytes allowed."""


class BadImageRequest(Exception):
    """The crop doesn't fit this image; the message says why."""


@dataclass(frozen=True)
class Crop:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class Converted:
    png: bytes
    width: int
    height: int
    shown_width: int
    shown_height: int
    mode: str
    bits: int
    more_pages: bool
    # Deep images were reduced from 0..FULL_SCALE; None for 8-bit images.
    full_scale: int | None
    peak: int | None
    dim: bool


def to_png(data: bytes, *, crop: Crop | None, max_bytes: int) -> Converted:
    """The first page, cropped at full resolution, scaled to MAX_SIDE at most, as PNG."""
    try:
        image = Image.open(io.BytesIO(data), formats=FORMATS)
        width, height, mode = image.width, image.height, image.mode
        if decoded_bytes(width, height, mode) > MAX_DECODED_BYTES:
            raise ImageTooLarge("too large to decode")
        bits = _bits(image, data)
        more_pages = _has_second_page(image)
        image.load()
        if crop is not None:
            image = image.crop(_box(crop, width, height))
        shown, peak = _viewable(image)
        deep = mode in _DEEP or bits > 8
        if deep and peak is None:
            peak = int(np.asarray(shown).max()) * 257 if shown.size[0] else 0
        for side in (MAX_SIDE, *FALLBACK_SIDES):
            fitted = _fit(shown, side)
            png = _png(fitted)
            if len(png) <= max_bytes:
                return Converted(
                    png,
                    width,
                    height,
                    *fitted.size,
                    mode,
                    bits,
                    more_pages,
                    FULL_SCALE if deep else None,
                    peak if deep else None,
                    deep and peak < DIM_PEAK,
                )
        raise PngOverBudget("over the bytes allowed even when scaled")
    except Image.DecompressionBombError:
        raise ImageTooLarge("too many pixels") from None
    except (UnidentifiedImageError, OSError, ValueError, TypeError, SyntaxError) as exc:
        raise ImageUnreadable(
            f"the file is damaged or not a scan image ({type(exc).__name__})"
        ) from None


def decoded_bytes(width: int, height: int, mode: str) -> int:
    """Roughly what converting this image costs in memory, from its header alone."""
    per_pixel = DEEP_BYTES_PER_PIXEL if mode in _DEEP else SHALLOW_BYTES_PER_PIXEL
    return width * height * per_pixel


def _bits(image: Image.Image, data: bytes) -> int:
    """Bits per sample of the stored file: 16-bit colour opens as 8-bit RGB."""
    if image.mode in _DEEP:
        return 16
    if image.format == "PNG" and len(data) > 24:
        return data[24]  # the IHDR bit depth
    tags = getattr(image, "tag_v2", None)
    bits = tags.get(_TIFF_BITS_PER_SAMPLE) if tags is not None else None
    if isinstance(bits, tuple):
        bits = bits[0]
    return bits if isinstance(bits, int) and bits > 0 else 8


def _has_second_page(image: Image.Image) -> bool:
    try:
        image.seek(1)
    except EOFError:
        return False
    image.seek(0)
    return True


def _box(crop: Crop, width: int, height: int) -> tuple[int, int, int, int]:
    right, bottom = crop.x + crop.width, crop.y + crop.height
    if crop.x < 0 or crop.y < 0 or crop.width < 1 or crop.height < 1:
        raise BadImageRequest("crop needs x, y >= 0 and width, height >= 1")
    if right > width or bottom > height:
        raise BadImageRequest(f"crop goes past the image's {width} x {height} pixels")
    return crop.x, crop.y, right, bottom


def _viewable(image: Image.Image) -> tuple[Image.Image, int | None]:
    """8-bit grey, RGB or RGBA, and a deep image's raw peak."""
    if image.mode in ("L", "RGB", "RGBA"):
        return image, None
    if image.mode == "1":
        return image.convert("L"), None
    if image.mode in _TO_RGB:
        return image.convert("RGB"), None
    if image.mode in _TO_RGBA:
        return image.convert("RGBA"), None
    if image.mode not in _DEEP:
        raise ImageUnreadable("32-bit floating-point images have no fixed scale")
    pixels = np.asarray(image)
    peak, low = (int(pixels.max()), int(pixels.min())) if pixels.size else (0, 0)
    if peak > FULL_SCALE or low < 0:
        raise ImageUnreadable(f"values run outside 0 to {FULL_SCALE}")
    # The same scale for every image, as plate videos use, so one hot pixel can't
    # darken the rest and brightness compares between images.
    reduced = _block_mean(pixels, max(1, max(image.size) // MAX_SIDE))
    eight = (reduced * (255.0 / FULL_SCALE)).round().astype(np.uint8)
    return Image.fromarray(eight), peak


def _block_mean(pixels: np.ndarray, k: int) -> np.ndarray:
    """Averages k x k blocks, so a large deep image shrinks before any float copy."""
    if k <= 1:
        return pixels.astype(np.float32)
    h, w = pixels.shape[0] // k * k, pixels.shape[1] // k * k
    blocks = pixels[:h, :w].reshape(h // k, k, w // k, k)
    return blocks.mean(axis=(1, 3), dtype=np.float32)


def _fit(image: Image.Image, side: int) -> Image.Image:
    if max(image.size) <= side:
        return image
    scale = side / max(image.size)
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    return image.resize(size, Image.Resampling.LANCZOS)


def _png(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, format="PNG", compress_level=6)
    return out.getvalue()
