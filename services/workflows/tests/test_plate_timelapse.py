"""Frame rate and the burned-in timestamp.

The label exists so an irregular capture gap is visible in the video rather
than silent, so these are mostly about what the text says for gaps of
different sizes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pytest

import plate_timelapse as pt

T0 = datetime(2026, 8, 25, 9, 15, tzinfo=timezone.utc)


class TestPlateFps:
    def test_is_slow_enough_to_read_a_burned_timestamp(self):
        """A floor, not an exact pin — the rate is expected to be tuned once
        real videos are watched, and this should allow that without breaking."""
        assert 1.0 / pt.PLATE_FPS >= 0.125

    def test_the_rate_is_a_whole_number_of_frames_per_second(self):
        """ffmpeg takes -r as a rate; a fraction here would put frame times on
        a timebase that no longer divides evenly into seconds."""
        assert isinstance(pt.PLATE_FPS, int)
        assert pt.PLATE_FPS >= 1


class TestLabelFor:
    def test_names_the_time_zone(self):
        """A video is a file and cannot know who is watching, so it has to say
        which zone it used. The plate pages show local time and name no zone."""
        assert "PDT" in pt.label_for(T0, T0)

    def test_uses_the_scanners_zone_not_utc(self):
        """09:15 UTC is 02:15 where the scanners are. The plate pages render
        the same capture in the viewer's own zone instead (#734)."""
        assert pt.label_for(T0, T0).startswith("2026-08-25 02:15 PDT")

    def test_switches_to_standard_time_in_winter(self):
        winter = datetime(2026, 12, 15, 20, 37, tzinfo=timezone.utc)
        assert "PST" in pt.label_for(winter, winter)

    def test_afternoon_reads_on_a_24_hour_clock(self):
        afternoon = datetime(2026, 8, 25, 23, 5, tzinfo=timezone.utc)
        assert "16:05" in pt.label_for(afternoon, afternoon)

    def test_the_date_sorts_lexically(self):
        """ISO order, so no US/UK ambiguity about which number is the day."""
        early = datetime(2026, 1, 2, 20, 0, tzinfo=timezone.utc)
        assert pt.label_for(early, early).startswith("2026-01-02 12:00 PST")

    def test_the_first_frame_reads_as_zero_elapsed(self):
        assert pt.label_for(T0, T0) == "2026-08-25 02:15 PDT\n+00h 00m"

    @pytest.mark.parametrize(
        "gap,expected",
        [
            (timedelta(0), "+00h 00m"),
            (timedelta(minutes=7), "+00h 07m"),
            (timedelta(minutes=59), "+00h 59m"),
            (timedelta(hours=1), "+01h 00m"),
            (timedelta(hours=12), "+12h 00m"),
            (timedelta(hours=23, minutes=59), "+23h 59m"),
            (timedelta(days=1), "+1d 00h 00m"),
            (timedelta(days=3, hours=4, minutes=32), "+3d 04h 32m"),
        ],
    )
    def test_elapsed_reads_correctly_across_gap_sizes(self, gap, expected):
        assert pt.label_for(T0 + gap, T0).endswith(expected)

    def test_an_overnight_gap_is_visible_in_the_elapsed_line(self):
        """The whole point of the label: at a constant frame rate a 12-hour gap
        and a 7-minute one look identical without it. Compares the elapsed line
        alone — the absolute lines differ anyway, so comparing whole labels
        would pass even with the elapsed counter deleted."""
        short = pt.label_for(T0 + timedelta(minutes=7), T0).splitlines()[1]
        overnight = pt.label_for(T0 + timedelta(hours=12), T0).splitlines()[1]
        assert short == "+00h 07m"
        assert overnight == "+12h 00m"

    def test_days_only_appear_once_there_is_a_day(self):
        assert "d " not in pt.label_for(T0 + timedelta(hours=23), T0)
        assert "1d " in pt.label_for(T0 + timedelta(hours=24), T0)

    @pytest.mark.parametrize(
        "seconds,expected",
        [
            (-1, "-00h 00m"),
            (-59, "-00h 00m"),
            (-60, "-00h 01m"),
            (-90, "-00h 01m"),
            (-1800, "-00h 30m"),
        ],
    )
    def test_a_frame_before_the_first_rounds_the_same_way_forwards_and_back(
        self, seconds, expected
    ):
        """Flooring the signed value rounds negatives away from zero, so -90s
        reads as two minutes. Frames are ordered by capture_date so this should
        not arise, but it must not read as a different elapsed time if it does."""
        assert expected in pt.label_for(T0 + timedelta(seconds=seconds), T0)

    def test_elapsed_follows_real_time_across_a_dst_boundary(self):
        """US DST ends 2026-11-01, so local clocks repeat an hour. A plate
        imaged from 00:30 to 03:30 local grew for four hours, not three, and
        the label has to say four — that interval is the measurement."""
        ny = ZoneInfo("America/New_York")
        before = datetime(2026, 11, 1, 0, 30, tzinfo=ny)
        after = datetime(2026, 11, 1, 3, 30, tzinfo=ny)

        # Three hours on the wall clock...
        assert after.hour - before.hour == 3
        # ...four hours of actual growth.
        assert pt.label_for(after, before).endswith("+04h 00m")

    def test_a_naive_datetime_is_refused(self):
        """capture_date is TIMESTAMPTZ, so a naive value means the zone was
        lost upstream. Either argument is enough to make the label wrong."""
        naive = datetime(2026, 8, 25, 9, 15)
        for args in ((naive, naive), (naive, T0), (T0, naive)):
            with pytest.raises(ValueError, match="timezone-aware"):
                pt.label_for(*args)

    def test_a_lost_zone_cannot_be_read_as_a_seven_hour_gap(self):
        """The same instant written two ways used to read as +07h 00m — the
        offset of the zone that was guessed, on the run's own first frame."""
        same_instant_naive = datetime(2026, 8, 25, 2, 15)
        with pytest.raises(ValueError):
            pt.label_for(T0, same_instant_naive)

    def test_an_offset_datetime_is_converted_not_relabelled(self):
        """A +02:00 timestamp is 07:15 UTC, so 12:15 AM in the scanners' zone."""
        offset = datetime(2026, 8, 25, 9, 15, tzinfo=timezone(timedelta(hours=2)))
        assert pt.label_for(offset, offset).startswith("2026-08-25 00:15 PDT")


class TestAnnotate:
    def _frame(self, h=200, w=300, fill=128):
        return np.full((h, w, 3), fill, dtype=np.uint8)

    def _band(self, label=None, h=200, w=300):
        """The rows annotate added, which is everything below the frame."""
        out = pt.annotate(self._frame(h=h, w=w), label or pt.label_for(T0, T0))
        return out[h:]

    @staticmethod
    def _ink(band):
        """Which rows and columns carry text, found by brightness rather than
        by the fill constants so a change to either shows up here."""
        bright = band > 128
        return (
            np.flatnonzero(bright.any(axis=(1, 2))),
            np.flatnonzero(bright.any(axis=(0, 2))),
            int(bright.all(axis=2).sum()),
        )

    def test_the_band_is_added_below_rather_than_drawn_over(self):
        """The whole point: the specimen is never touched."""
        frame = self._frame()
        out = pt.annotate(frame, pt.label_for(T0, T0))
        assert out.shape == (200 + pt.LABEL_BAND_HEIGHT, 300, 3)
        assert np.array_equal(out[:200], frame)

    def test_the_specimen_survives_every_pixel_value(self):
        """A gradient, not a flat fill — a lossy round-trip would show here."""
        frame = np.arange(256, dtype=np.uint8).reshape(1, 256, 1)
        frame = np.repeat(np.repeat(frame, 40, axis=0), 3, axis=2)
        out = pt.annotate(frame, pt.label_for(T0, T0))
        assert np.array_equal(out[:40], frame)

    def test_the_band_is_the_full_width_and_black(self):
        """Compared against literal black, not _BAND_FILL — an expectation read
        from the constant it checks moves with any change to that constant."""
        band = self._band()
        assert band.shape[1] == 300
        # Every column carries band in all three channels, not just the half
        # the text sits on.
        assert (band == 0).all(axis=2).any(axis=0).all()

    def test_the_band_is_a_fixed_height_regardless_of_the_label(self):
        """A band that grew with the text would change the video's dimensions
        mid-stream."""
        short = pt.annotate(self._frame(), "x")
        long = pt.annotate(self._frame(), "a very long label\nwith two lines")
        assert short.shape == long.shape

    def test_the_band_height_is_a_real_number_of_rows(self):
        """The band is real rows, and a plausible number of them."""
        out = pt.annotate(self._frame(h=100), "x")
        assert out.shape[0] == 100 + pt.LABEL_BAND_HEIGHT
        assert 20 <= pt.LABEL_BAND_HEIGHT <= 80

    def test_the_text_is_actually_drawn_not_just_the_band(self):
        blank = pt.annotate(self._frame(), "")
        labelled = pt.annotate(self._frame(), pt.label_for(T0, T0))
        assert not np.array_equal(blank[200:], labelled[200:])

    def test_a_different_label_produces_a_different_band(self):
        a = pt.annotate(self._frame(), pt.label_for(T0, T0))
        b = pt.annotate(self._frame(), pt.label_for(T0 + timedelta(hours=12), T0))
        assert not np.array_equal(a[200:], b[200:])

    def test_the_label_is_legible_against_the_band(self):
        """A contrast floor and an ink floor: legible, not merely present."""
        band = self._band()
        _, _, ink = self._ink(band)
        assert int(band.max()) - int(band.min()) >= 128
        assert ink >= 400

    def test_both_label_lines_fit_inside_the_band(self, monkeypatch):
        """Both lines are drawn inside LABEL_BAND_HEIGHT rows.

        Measured on an oversized band, because a band exactly as tall as the
        text it holds clips that text to its own last row — so the ink stops
        where the band stops whether it fits or not.
        """
        label = pt.label_for(T0 + timedelta(hours=12), T0)
        height = pt.LABEL_BAND_HEIGHT
        roomy = height * 3

        monkeypatch.setattr(pt, "LABEL_BAND_HEIGHT", roomy)
        rows, _, _ = self._ink(self._band(label))

        assert rows[-1] < roomy - 1, "the oversized band clipped too — raise it"
        # Two lines of text means two runs of inked rows with a gap between.
        assert len(np.flatnonzero(np.diff(rows) > 1)) == 1
        assert rows[-1] < height

    def test_the_text_does_not_touch_the_band_edge(self):
        """Literal margins: at LABEL_PADDING 0 the glyphs sit against the frame
        above them and against the video's left border."""
        rows, cols, _ = self._ink(self._band())
        assert rows[0] >= 8
        assert cols[0] >= 4
        assert pt.LABEL_BAND_HEIGHT - rows[-1] > 1, "the last line sits on the edge"

    def test_the_result_is_still_8_bit(self):
        """np.concatenate promotes: an int8 frame would come back int16 and
        write twice the bytes ffmpeg expects."""
        out = pt.annotate(self._frame(), pt.label_for(T0, T0))
        assert out.dtype == np.uint8

    def test_does_not_mutate_the_frame_it_was_given(self):
        """The encoder may reuse buffers."""
        frame = self._frame()
        before = frame.copy()
        pt.annotate(frame, pt.label_for(T0, T0))
        assert np.array_equal(frame, before)

    def test_the_result_is_writable(self):
        """np.asarray on a PIL image is read-only; the encoder pads frames in
        place."""
        out = pt.annotate(self._frame(), "x")
        out[0, 0] = 1

    def test_a_grayscale_frame_is_refused(self):
        """The scanners produce colour. Accepting 2D would also accept a
        palette image, whose indices would encode as brightness."""
        with pytest.raises(ValueError, match="3-channel RGB"):
            pt.annotate(np.full((200, 300), 128, dtype=np.uint8), "x")

    def test_a_deeper_frame_is_refused_rather_than_flattened(self):
        """An MP4 is 8-bit either way, but clamping 4000 to 255 blows out the
        highlights where scaling would not. Reduce it where the range is known."""
        for dtype in (np.uint16, np.int8, np.float32, np.bool_):
            with pytest.raises(ValueError, match="8-bit"):
                pt.annotate(np.zeros((200, 300, 3), dtype=dtype), "x")

    @pytest.mark.parametrize(
        "shape",
        [(5,), (200, 300), (4, 4, 4), (200, 300, 1), (200, 300, 4)],
    )
    def test_an_unsupported_shape_is_refused(self, shape):
        """Matched on the message: without that, numpy's own incidental errors
        ("not enough values to unpack") pass this with the guard removed."""
        with pytest.raises(ValueError, match="expected a 3-channel RGB frame"):
            pt.annotate(np.zeros(shape, np.uint8), "x")

    def test_a_frame_narrower_than_the_label_still_returns(self):
        """Text overflows rather than raising; the band is still the frame's
        width so the video stays rectangular."""
        out = pt.annotate(self._frame(w=40), pt.label_for(T0, T0))
        assert out.shape == (200 + pt.LABEL_BAND_HEIGHT, 40, 3)
