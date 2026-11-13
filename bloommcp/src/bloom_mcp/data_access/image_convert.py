"""Turns a stored scan image (TIFF, PNG, JPEG) into a lossless PNG Claude can view."""

from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np
from PIL import Image, UnidentifiedImageError

# Claude's vision scales a longer side down to about this, so larger gains no detail.
MAX_SIDE = 1568
# Smaller sides tried, in turn, when the PNG is over the per-image byte cap.
FALLBACK_SIDES = (1200, 900)
# Larger pages are linked, not decoded: pixels, and bytes once decoded.
MAX_PIXELS = 150_000_000
MAX_DECODED_BYTES = 300_000_000
# Scan formats only; Pillow's other decoders are never reached.
FORMATS = ("PNG", "TIFF", "JPEG")
# A deep image dimmer than this share of its full scale is flagged as dim.
DIM_SHARE = 1 / 16

_BYTES_PER_PIXEL = {"1": 1, "L": 1, "P": 1, "LA": 2, "PA": 2, "La": 2, "RGB": 3}
_DEEP = frozenset({"I;16", "I;16B", "I;16L", "I;16N", "I"})
_TO_RGB = frozenset({"1", "P", "CMYK", "YCbCr", "LAB", "HSV", "RGBX"})
_TO_RGBA = frozenset({"LA", "PA", "La", "RGBa"})


class ImageUnreadable(Exception):
    """Not an image this tool can show; the message says why, for Claude."""


class ImageTooLarge(ImageUnreadable):
    """Too many pixels or bytes to decode, or too many bytes even when scaled."""


class BadImageRequest(Exception):
    """The page or crop doesn't fit this image; the message says why."""


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
    more_pages: bool
    # A deep image's 0..full_scale was mapped to 0..255; None for 8-bit images.
    full_scale: int | None
    dim: bool


def to_png(data: bytes, *, page: int, crop: Crop | None, max_bytes: int) -> Converted:
    """The page, cropped at full resolution, scaled to MAX_SIDE at most, as PNG."""
    try:
        image = Image.open(io.BytesIO(data), formats=FORMATS)
        _seek(image, page)
        width, height, mode = image.width, image.height, image.mode
        _check_size(width, height, mode)
        more_pages = _has_page(image, page + 1)
        _seek(image, page)
        image.load()
        if crop is not None:
            image = image.crop(_box(crop, width, height))
        shown, full_scale, dim = _viewable(image)
        for side in (MAX_SIDE, *FALLBACK_SIDES):
            fitted = _fit(shown, side)
            png = _png(fitted)
            if len(png) <= max_bytes:
                return Converted(
                    png, width, height, *fitted.size, mode, more_pages, full_scale, dim
                )
    except Image.DecompressionBombError:
        raise ImageTooLarge("too many pixels") from None
    except (ImageUnreadable, BadImageRequest):
        raise
    except (UnidentifiedImageError, OSError, ValueError, TypeError, SyntaxError) as exc:
        raise ImageUnreadable(
            f"the file is damaged or not a scan image ({type(exc).__name__})"
        ) from None
    raise ImageTooLarge("too large even when scaled")


def _seek(image: Image.Image, page: int) -> None:
    try:
        image.seek(page)
    except EOFError:
        raise BadImageRequest(f"page {page} doesn't exist in this image") from None


def _has_page(image: Image.Image, page: int) -> bool:
    try:
        image.seek(page)
    except EOFError:
        return False
    return True


def _check_size(width: int, height: int, mode: str) -> None:
    decoded = width * height * _BYTES_PER_PIXEL.get(mode, 4)
    if width * height > MAX_PIXELS or decoded > MAX_DECODED_BYTES:
        raise ImageTooLarge("too large to decode")


def _box(crop: Crop, width: int, height: int) -> tuple[int, int, int, int]:
    right, bottom = crop.x + crop.width, crop.y + crop.height
    if crop.x < 0 or crop.y < 0 or crop.width < 1 or crop.height < 1:
        raise BadImageRequest("crop needs x, y >= 0 and width, height >= 1")
    if right > width or bottom > height:
        raise BadImageRequest(f"crop goes past the image's {width} x {height} pixels")
    return crop.x, crop.y, right, bottom


def _viewable(image: Image.Image) -> tuple[Image.Image, int | None, bool]:
    """8-bit grey, RGB or RGBA; a deeper image is mapped from its fixed full scale."""
    if image.mode in ("L", "RGB", "RGBA"):
        return image, None, False
    if image.mode in _TO_RGB:
        return image.convert("RGB"), None, False
    if image.mode in _TO_RGBA:
        return image.convert("RGBA"), None, False
    if image.mode not in _DEEP:
        raise ImageUnreadable(
            f"{image.mode} images have no fixed scale to show them by"
        )
    # The same scale for every image of a depth, as plate videos use, so one hot pixel
    # can't darken the rest and brightness compares between images.
    full_scale = (1 << _bits(image)) - 1
    pixels = np.asarray(image)
    k = max(1, max(image.size) // MAX_SIDE)
    reduced = _block_mean(pixels, k)
    peak = float(reduced.max()) if reduced.size else 0.0
    eight = (np.clip(reduced, 0, full_scale) * (255.0 / full_scale)).round()
    shown = Image.fromarray(eight.astype(np.uint8))
    return shown, full_scale, peak < full_scale * DIM_SHARE


def _bits(image: Image.Image) -> int:
    """Bits per sample from the TIFF tag when present, else the container's 16."""
    tags = getattr(image, "tag_v2", None)
    bits = tags.get(258) if tags is not None else None
    if isinstance(bits, tuple):
        bits = bits[0]
    return bits if isinstance(bits, int) and 8 < bits <= 16 else 16


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
