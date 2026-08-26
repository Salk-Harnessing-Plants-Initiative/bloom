"""One stored image, turned into one frame of a plate time-lapse.

Real image bytes throughout — the thing under test is a decode, a resize and a
label, and a fake would only prove the calls were made in some order.
"""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

import plate_encode as pe
from plate_timelapse import LABEL_BAND_HEIGHT

LABEL = "2026-03-06 21:36 PST\n+01h 10m"


def _png(width, height, mode="RGB") -> bytes:
    """A patterned image, so a transpose or a flip is visible in the pixels."""
    bands = len(Image.new(mode, (1, 1)).getbands())
    rows = np.arange(height, dtype=np.uint16)[:, None, None]
    cols = np.arange(width, dtype=np.uint16)[None, :, None]
    chans = np.arange(bands, dtype=np.uint16)[None, None, :]
    data = ((rows * 37 + cols * 11 + chans * 5) % 251).astype(np.uint8)

    buf = io.BytesIO()
    Image.fromarray(data.squeeze() if bands == 1 else data, mode).save(buf, "PNG")
    return buf.getvalue()


def test_a_native_sized_plate_comes_back_at_the_target_width():
    frame = pe.prepare_frame(_png(4960, 6850), LABEL)
    assert frame.shape[1] == pe.PLATE_VIDEO_WIDTH


def test_the_aspect_ratio_is_kept():
    """4960x6850 is portrait; stretching it would distort every root.

    The picture height is read off the frame, not computed from the ratio —
    deriving it from the same arithmetic the code uses asserts nothing.
    """
    frame = pe.prepare_frame(_png(4960, 6850), LABEL)
    picture_height = frame.shape[0] - LABEL_BAND_HEIGHT

    assert abs(picture_height / frame.shape[1] - 6850 / 4960) < 0.01, (
        f"got {frame.shape[1]}x{picture_height}, which is not the source ratio"
    )


def test_the_label_is_added_after_the_downscale():
    """The band is a fixed height, so labelling first and shrinking after would
    scale the text down with the picture until it is unreadable."""
    frame = pe.prepare_frame(_png(4960, 6850), LABEL)
    picture_height = round(6850 * pe.PLATE_VIDEO_WIDTH / 4960)
    picture_height -= picture_height % 2

    band = frame[picture_height:]
    assert band.shape[0] >= 40, "the label band was shrunk with the picture"
    assert band.max() > 128, "no text was drawn in the band"


def test_the_result_is_8_bit_rgb():
    """`annotate` refuses anything else, and ffmpeg is told rgb24."""
    frame = pe.prepare_frame(_png(400, 600), LABEL)
    assert frame.dtype == np.uint8
    assert frame.ndim == 3 and frame.shape[2] == 3


@pytest.mark.parametrize("mode", ["RGB", "L", "RGBA", "P"])
def test_every_mode_the_scanners_might_write_becomes_rgb(mode):
    frame = pe.prepare_frame(_png(400, 600, mode=mode), LABEL)
    assert frame.shape[2] == 3


def test_a_16_bit_source_is_scaled_rather_than_clamped():
    """`convert("RGB")` clamps, so a 16-bit plate would come back as a white
    field wherever it exceeded 255 — the highlights, which is the tissue."""
    data = np.linspace(0, 65535, 400 * 600, dtype=np.uint16).reshape(600, 400)
    buf = io.BytesIO()
    Image.fromarray(data).save(buf, "PNG")

    frame = pe.prepare_frame(buf.getvalue(), LABEL)
    picture = frame[:600]
    assert picture.max() > 200, "the bright end was lost"
    assert picture.min() < 40, "the dark end was lost"
    assert 40 < np.median(picture) < 215, (
        "a clamped conversion leaves a bimodal frame, not a gradient"
    )


def test_a_plate_narrower_than_the_target_is_not_enlarged():
    """Interpolating up invents detail; the video is better small and honest."""
    frame = pe.prepare_frame(_png(600, 800), LABEL)
    assert frame.shape[1] == 600


def test_both_dimensions_come_back_even():
    """H.264 needs even dimensions, and VideoWriter pads an odd frame by
    repeating its edge — which would land on the label."""
    frame = pe.prepare_frame(_png(601, 803), LABEL)
    assert frame.shape[1] % 2 == 0
    assert frame.shape[0] % 2 == 0


def test_the_picture_is_not_drawn_over():
    """The band is added beneath, so every source pixel survives the round trip
    that a scientist measures from."""
    source = _png(400, 600)
    frame = pe.prepare_frame(source, LABEL)
    expected = np.asarray(Image.open(io.BytesIO(source)), dtype=np.uint8)
    assert np.array_equal(frame[:600], expected)


def test_a_different_label_changes_only_the_band():
    a = pe.prepare_frame(_png(400, 600), "2026-03-06 21:36 PST\n+01h 10m")
    b = pe.prepare_frame(_png(400, 600), "2026-03-07 09:00 PST\n+12h 34m")
    assert np.array_equal(a[:600], b[:600]), "the picture changed with the label"
    assert not np.array_equal(a[600:], b[600:]), "the band did not change"
