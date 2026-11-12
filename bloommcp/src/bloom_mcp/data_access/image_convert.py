"""Turns a stored image (TIFF, PNG, JPEG...) into a lossless PNG Claude can view."""

from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np
from PIL import Image, UnidentifiedImageError

# Claude's vision scales a longer side down to about this, so larger gains no detail.
MAX_SIDE = 1568
# Smaller sides tried, in turn, when the PNG is over the per-image byte cap.
FALLBACK_SIDES = (1200, 900)
# Larger images are linked, not decoded (about 12,000 x 12,000).
MAX_PIXELS = 150_000_000


class ImageUnreadable(Exception):
    """Not an image Pillow can open."""


class ImageTooLarge(ImageUnreadable):
    """Too many pixels to decode, or too many bytes even when scaled."""


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
    pages: int
    contrast_stretched: bool


def to_png(data: bytes, *, page: int, crop: Crop | None, max_bytes: int) -> Converted:
    """The page, cropped at full resolution, scaled to MAX_SIDE at most, as PNG."""
    try:
        image = Image.open(io.BytesIO(data))
        width, height = image.size
        if width * height > MAX_PIXELS:
            raise ImageTooLarge("too many pixels")
        pages = getattr(image, "n_frames", 1)
        if not 0 <= page < pages:
            raise BadImageRequest(f"page {page} is outside 0 to {pages - 1}")
        image.seek(page)
        width, height = image.size
        mode = image.mode
        if crop is not None:
            image = image.crop(_box(crop, width, height))
        image, stretched = _viewable(image)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ImageUnreadable(type(exc).__name__) from None
    for side in (MAX_SIDE, *FALLBACK_SIDES):
        shown = _fit(image, side)
        png = _png(shown)
        if len(png) <= max_bytes:
            return Converted(png, width, height, *shown.size, mode, pages, stretched)
    raise ImageTooLarge("too large even when scaled")


def _box(crop: Crop, width: int, height: int) -> tuple[int, int, int, int]:
    right, bottom = crop.x + crop.width, crop.y + crop.height
    if crop.x < 0 or crop.y < 0 or crop.width < 1 or crop.height < 1:
        raise BadImageRequest("crop needs x, y >= 0 and width, height >= 1")
    if right > width or bottom > height:
        raise BadImageRequest(f"crop goes past the image's {width} x {height} pixels")
    return crop.x, crop.y, right, bottom


def _viewable(image: Image.Image) -> tuple[Image.Image, bool]:
    """8-bit grey, RGB or RGBA; deeper images stretched from their min to max."""
    if image.mode in ("L", "RGB", "RGBA"):
        return image, False
    if image.mode in ("1", "P", "LA", "PA", "CMYK", "YCbCr", "LAB", "HSV"):
        return image.convert("RGBA" if "A" in image.mode else "RGB"), False
    pixels = np.asarray(image)
    if pixels.ndim == 3:
        pixels = pixels[..., :3]
    pixels = _block_mean(pixels, -(-max(image.size) // MAX_SIDE))
    low, high = float(pixels.min()), float(pixels.max())
    scale = 255.0 / (high - low) if high > low else 0.0
    stretched = ((pixels - low) * scale).clip(0, 255).astype(np.uint8)
    return Image.fromarray(stretched), True


def _block_mean(pixels: np.ndarray, k: int) -> np.ndarray:
    """Averages k x k blocks, so a deep image is scaled before it is copied to floats."""
    k = min(k, pixels.shape[0], pixels.shape[1])
    if k <= 1:
        return pixels.astype(np.float32)
    h, w = pixels.shape[0] // k * k, pixels.shape[1] // k * k
    blocks = pixels[:h, :w].reshape(h // k, k, w // k, k, *pixels.shape[2:])
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
