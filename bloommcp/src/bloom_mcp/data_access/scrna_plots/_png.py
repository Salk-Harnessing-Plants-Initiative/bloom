"""One plot drawn to a fixed-size PNG: matplotlib's Figure API only, at most 2 at once.

pyplot keeps one process-wide figure list that threads race on, so nothing here imports it.
Drawing never raises: a plot that can't be drawn comes back as a reason for the reply's note.
"""

from __future__ import annotations

import io
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from PIL import Image

from bloom_mcp.data_access import image_slots

logger = logging.getLogger(__name__)

WIDTH, HEIGHT, DPI = 1200, 900, 100
MAX_PNG_BYTES = 1_500_000
# Drawing a large UMAP holds the CPU for about a second, so few run at once.
MAX_DRAWINGS = 2
_slots = threading.BoundedSemaphore(MAX_DRAWINGS)

BUSY = "No plot: other plots were being drawn. Ask again."
OUT_OF_TIME = "No plot: the call ran out of time."
TOO_LARGE = "No plot: the image came out over its size limit."
FAILED = "No plot: it could not be drawn."


@dataclass(frozen=True)
class Drawn:
    png: bytes | None
    reason: str | None


def new_figure() -> Figure:
    return Figure(figsize=(WIDTH / DPI, HEIGHT / DPI), dpi=DPI)


def _render(fig: Figure) -> bytes:
    canvas = FigureCanvasAgg(fig)
    canvas.draw()
    image = Image.frombuffer(
        "RGBA", canvas.get_width_height(), canvas.buffer_rgba()
    ).convert("RGB")
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()


def draw(build: Callable[[], Figure], deadline: float) -> Drawn:
    """`build`'s figure as PNG bytes, or the reason there is none. A slot is waited for
    as long as image_slots allows, never past `deadline`."""
    try:
        with image_slots.slot(_slots, deadline):
            png = _render(build())
    except image_slots.NoSlot as no:
        return Drawn(None, OUT_OF_TIME if no.reason == "time" else BUSY)
    except Exception as exc:  # noqa: BLE001 — logged by type; the numbers still return
        logger.warning("scRNA plot failed: %s", type(exc).__name__)
        return Drawn(None, FAILED)
    if len(png) > MAX_PNG_BYTES:
        return Drawn(None, TOO_LARGE)
    return Drawn(png, None)
