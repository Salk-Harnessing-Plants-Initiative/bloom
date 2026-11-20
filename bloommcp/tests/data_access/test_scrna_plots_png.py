"""scrna_plots._png: one fixed-size PNG per plot, at most 2 drawn at once, never an error."""

from __future__ import annotations

import io
import subprocess
import sys
import threading
import time

import matplotlib.pyplot as plt
import pytest
from matplotlib.figure import Figure
from PIL import Image

from bloom_mcp.data_access.scrna_plots import _png


def _figure() -> Figure:
    fig = _png.new_figure()
    ax = fig.add_subplot()
    ax.scatter([0, 1, 2], [2, 0, 1], s=40, c=["#c62828", "#1565c0", "#9e9e9e"])
    return fig


def _later(seconds=30.0):
    return time.monotonic() + seconds


def test_a_png_is_rgb_at_the_fixed_size_with_no_software_line():
    drawn = _png.draw(_figure, _later())
    assert drawn.reason is None
    image = Image.open(io.BytesIO(drawn.png))
    assert (image.format, image.mode, image.size) == ("PNG", "RGB", (1200, 900))
    assert "Software" not in image.info


def test_the_same_figure_gives_the_same_bytes():
    assert _png.draw(_figure, _later()).png == _png.draw(_figure, _later()).png


def test_no_pyplot_figure_is_left_open():
    before = plt.get_fignums()
    _png.draw(_figure, _later())
    assert plt.get_fignums() == before


def test_drawing_never_imports_pyplot():
    code = (
        "import sys; from bloom_mcp.data_access.scrna_plots import _png; "
        "f = _png.new_figure(); f.add_subplot().plot([0, 1]); "
        "import time; _png.draw(lambda: f, time.monotonic() + 30); "
        "print('matplotlib.pyplot' in sys.modules)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "False"


def test_a_png_over_the_cap_is_left_out(monkeypatch):
    monkeypatch.setattr(_png, "MAX_PNG_BYTES", 10)
    drawn = _png.draw(_figure, _later())
    assert (drawn.png, drawn.reason) == (None, _png.TOO_LARGE)


def test_a_drawing_error_is_a_reason_not_an_exception():
    def broken() -> Figure:
        raise ValueError("'teal-ish' is not a valid color")

    drawn = _png.draw(broken, _later())
    assert (drawn.png, drawn.reason) == (None, _png.FAILED)


def test_no_time_left_draws_nothing():
    called = []
    drawn = _png.draw(lambda: called.append(1) or _figure(), time.monotonic() - 1)
    assert (drawn.png, drawn.reason, called) == (None, _png.OUT_OF_TIME, [])


def test_a_third_drawing_waits_then_is_busy(monkeypatch):
    slots = threading.BoundedSemaphore(2)
    monkeypatch.setattr(_png, "_slots", slots)
    monkeypatch.setattr(_png, "SLOT_WAIT_SECONDS", 0.05)
    slots.acquire()
    slots.acquire()
    try:
        drawn = _png.draw(_figure, _later())
    finally:
        slots.release()
        slots.release()
    assert (drawn.png, drawn.reason) == (None, _png.BUSY)


def test_a_slot_is_freed_after_an_error(monkeypatch):
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(_png, "_slots", slots)
    _png.draw(lambda: 1 / 0, _later())
    assert _png.draw(_figure, _later()).reason is None


def test_drawings_in_threads_match_one_at_a_time():
    serial = _png.draw(_figure, _later()).png
    results: list[bytes | None] = [None] * 4

    def run(i):
        results[i] = _png.draw(_figure, _later()).png

    threads = [threading.Thread(target=run, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [serial] * 4


def test_limits_are_as_documented():
    assert (_png.WIDTH, _png.HEIGHT, _png.MAX_PNG_BYTES) == (1200, 900, 1_500_000)
    assert (_png.MAX_DRAWINGS, _png.SLOT_WAIT_SECONDS) == (2, 15)


@pytest.mark.parametrize("side", [_png.WIDTH, _png.HEIGHT])
def test_the_image_fits_read_files_budget(side):
    from bloom_mcp.data_access import image_convert

    assert side <= image_convert.MAX_SIDE
