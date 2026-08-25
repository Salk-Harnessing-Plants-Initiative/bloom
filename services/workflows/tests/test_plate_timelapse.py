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
        # A frame must stay up long enough to read its label. Below ~8 fps a
        # frame is at least an eighth of a second, which is the floor for that.
        assert pt.PLATE_FPS == 4
        assert 1.0 / pt.PLATE_FPS >= 0.125

    def test_duration_scales_with_the_run_rather_than_being_fixed(self):
        """A fixed rate means twice the frames is twice the video, so two
        plates are comparable by length. Written without a literal duration so
        the rate stays tunable — nothing here depends on its value."""
        assert (200 / pt.PLATE_FPS) == 5 * (40 / pt.PLATE_FPS)


class TestLabelFor:
    def test_names_the_time_zone(self):
        """A video is a file and cannot know who is watching, so it has to say
        which zone it used. The plate pages show local time and name no zone."""
        assert "PDT" in pt.label_for(T0, T0)

    def test_uses_the_scanners_zone_not_utc(self):
        """09:15 UTC is 02:15 in the scanners' zone, and that is what the plate
        pages render for the same capture."""
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

    def test_an_overnight_gap_is_visible_as_a_jump(self):
        """The whole point of the label: at a constant frame rate a 12-hour gap
        and a 7-minute one look identical without it."""
        short = pt.label_for(T0 + timedelta(minutes=7), T0)
        overnight = pt.label_for(T0 + timedelta(hours=12), T0)
        assert short != overnight

    def test_days_only_appear_once_there_is_a_day(self):
        assert "d " not in pt.label_for(T0 + timedelta(hours=23), T0)
        assert "1d " in pt.label_for(T0 + timedelta(hours=24), T0)

    def test_a_frame_before_the_first_reads_as_negative(self):
        """Frames are ordered by capture_date, so this should not happen — but
        it should not silently read as elapsed time either."""
        assert "-00h 30m" in pt.label_for(T0 - timedelta(minutes=30), T0)

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

    def test_a_naive_datetime_is_read_as_utc_then_converted(self):
        """capture_date is TIMESTAMPTZ, so a naive value means the driver lost
        the zone — reading it as UTC is the only safe assumption."""
        naive = datetime(2026, 8, 25, 9, 15)
        assert pt.label_for(naive, naive).startswith("2026-08-25 02:15 PDT")

    def test_an_offset_datetime_is_converted_not_relabelled(self):
        """A +02:00 timestamp is 07:15 UTC, so 12:15 AM in the scanners' zone."""
        offset = datetime(2026, 8, 25, 9, 15, tzinfo=timezone(timedelta(hours=2)))
        assert pt.label_for(offset, offset).startswith("2026-08-25 00:15 PDT")


class TestAnnotate:
    def _frame(self, h=200, w=300):
        return np.zeros((h, w, 3), dtype=np.uint8)

    def test_dimensions_and_dtype_are_unchanged(self):
        """The encoder is fed these directly; a changed shape breaks it."""
        frame = self._frame()
        out = pt.annotate(frame, "x")
        assert out.shape == frame.shape
        assert out.dtype == frame.dtype

    def test_the_specimen_above_the_band_is_untouched(self):
        frame = np.full((200, 300, 3), 128, dtype=np.uint8)
        out = pt.annotate(frame, pt.label_for(T0, T0))
        above = 200 - pt.LABEL_BAND_HEIGHT
        assert np.array_equal(out[:above], frame[:above])

    def test_the_band_is_drawn(self):
        frame = np.full((200, 300, 3), 128, dtype=np.uint8)
        out = pt.annotate(frame, pt.label_for(T0, T0))
        band = out[200 - pt.LABEL_BAND_HEIGHT :]
        assert not np.array_equal(band, frame[200 - pt.LABEL_BAND_HEIGHT :])

    def test_the_text_is_actually_drawn_not_just_the_band(self):
        """A band with no text would still change those pixels."""
        blank = pt.annotate(self._frame(), "")
        labelled = pt.annotate(self._frame(), pt.label_for(T0, T0))
        assert not np.array_equal(blank, labelled)

    def test_a_different_label_produces_a_different_frame(self):
        a = pt.annotate(self._frame(), pt.label_for(T0, T0))
        b = pt.annotate(self._frame(), pt.label_for(T0 + timedelta(hours=12), T0))
        assert not np.array_equal(a, b)

    def test_the_band_sits_in_the_same_place_every_frame(self):
        """A band that moved between frames would jitter through the video."""
        tall = pt.annotate(self._frame(h=200), "x")
        same = pt.annotate(self._frame(h=200), "yy\nzz")
        top = 200 - pt.LABEL_BAND_HEIGHT
        assert np.array_equal(tall[:top], same[:top])

    def test_a_grayscale_frame_stays_grayscale(self):
        frame = np.zeros((200, 300), dtype=np.uint8)
        out = pt.annotate(frame, pt.label_for(T0, T0))
        assert out.shape == frame.shape

    def test_a_frame_shorter_than_the_band_still_works(self):
        out = pt.annotate(self._frame(h=10), pt.label_for(T0, T0))
        assert out.shape == (10, 300, 3)

    def test_a_frame_of_the_wrong_rank_is_refused(self):
        with pytest.raises(ValueError):
            pt.annotate(np.zeros((5,), dtype=np.uint8), "x")
