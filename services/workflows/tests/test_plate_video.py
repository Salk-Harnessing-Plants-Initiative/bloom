"""Which frames a plate time-lapse would be made from.

The fake client here records `select` and `order`, and keeps `is_` separate from
`not_.is_`. The one in test_video.py treats all three as no-ops, so a test built
on it cannot tell an ordered query from an unordered one — and ordering is the
whole correctness of a time-lapse.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import plate_video as pv

T0 = datetime(2026, 8, 25, 9, 15, tzinfo=timezone.utc)


class _Query:
    """Records what was asked for, so a test can assert the query's shape."""

    def __init__(self, rows):
        self._rows = rows
        self.recorded: dict = {}

    def select(self, columns):
        self.recorded["select"] = columns
        return self

    def eq(self, column, value):
        self.recorded.setdefault("eq", []).append((column, value))
        return self

    @property
    def not_(self):
        self.recorded["negated"] = True
        return self

    def is_(self, column, value):
        key = "not_is" if self.recorded.pop("negated", False) else "is"
        self.recorded.setdefault(key, []).append((column, value))
        return self

    def order(self, column, **kwargs):
        self.recorded["order"] = (column, kwargs.get("desc", False))
        return self

    def execute(self):
        return type("_R", (), {"data": self._rows})()


class _Client:
    def __init__(self, rows=None):
        self.q = _Query(rows or [])
        self.tables: list[str] = []

    def table(self, name):
        self.tables.append(name)
        return self.q


def _row(minutes, path, cycle=None):
    return {
        "capture_date": T0 + timedelta(minutes=minutes),
        "cycle_number": cycle,
        "gravi_images": {"object_path": path},
    }


def test_frames_come_back_oldest_first():
    """A time-lapse played out of order is not a time-lapse."""
    client = _Client([_row(0, "a.tif"), _row(7, "b.tif"), _row(14, "c.tif")])
    frames = pv.get_plate_frames(client, 12, "P7", 1)

    assert [f["object_path"] for f in frames] == ["a.tif", "b.tif", "c.tif"]
    assert client.q.recorded["order"] == ("capture_date", False)


def test_the_query_orders_by_capture_date_not_cycle_number():
    """`cycle_number` is nullable, so it cannot be the sort key. `capture_date`
    is NOT NULL and unique per (experiment, plate)."""
    client = _Client([_row(0, "a.tif")])
    pv.get_plate_frames(client, 12, "P7", 1)

    column, descending = client.q.recorded["order"]
    assert column == "capture_date"
    assert descending is False


def test_the_plate_is_identified_by_all_three_keys():
    """Plate ids repeat across waves and experiments, so two of the three would
    return another plate's captures."""
    client = _Client([_row(0, "a.tif")])
    pv.get_plate_frames(client, 12, "P7", 3)

    assert client.tables == ["gravi_scans"]
    assert set(client.q.recorded["eq"]) == {
        ("experiment_id", 12),
        ("plate_id", "P7"),
        ("wave_number", 3),
    }


def test_a_plate_with_no_wave_is_matched_with_is_null():
    """`wave_number = NULL` matches nothing, so a plate with no wave would come
    back empty and read as a plate with no frames."""
    client = _Client([_row(0, "a.tif")])
    frames = pv.get_plate_frames(client, 12, "P7", None)

    assert ("wave_number", "null") in client.q.recorded["is"]
    assert ("wave_number", None) not in client.q.recorded.get("eq", [])
    assert len(frames) == 1


def test_wave_zero_is_a_wave_not_a_missing_one():
    """The scanner app sends 0 when no wave is set, so 0 arrives in practice and
    `if wave_number:` would send it down the null branch."""
    client = _Client([_row(0, "a.tif")])
    pv.get_plate_frames(client, 12, "P7", 0)

    assert ("wave_number", 0) in client.q.recorded["eq"]
    assert "is" not in client.q.recorded


def test_a_capture_with_no_image_is_excluded_by_the_query():
    """Not skipped afterwards: `len(frames)` is compared against the count
    recorded for a stored video, so both sides have to count the same rows."""
    client = _Client([_row(0, "a.tif")])
    pv.get_plate_frames(client, 12, "P7", 1)

    assert "gravi_images!inner" in client.q.recorded["select"], (
        "the join must be inner, or captures whose image never arrived are counted"
    )


def test_the_object_path_is_flattened_onto_the_frame():
    client = _Client([_row(0, "12/wave-1/P7.tif")])
    assert pv.get_plate_frames(client, 12, "P7", 1)[0]["object_path"] == (
        "12/wave-1/P7.tif"
    )


def test_an_embedded_list_is_read_the_same_as_an_embedded_object():
    """PostgREST returns a to-one embed as an object, or a one-element list when
    it cannot prove the relationship is to-one."""
    row = _row(0, "a.tif")
    row["gravi_images"] = [{"object_path": "a.tif"}]
    assert pv.get_plate_frames(_Client([row]), 12, "P7", 1)[0]["object_path"] == "a.tif"


def test_a_plate_with_no_captures_is_an_empty_list_not_an_error():
    """`.execute().data` is None when nothing matched."""
    client = _Client(None)
    assert pv.get_plate_frames(client, 12, "P7", 1) == []


def test_first_capture_is_the_oldest_frame():
    frames = pv.get_plate_frames(
        _Client([_row(0, "a.tif"), _row(7, "b.tif")]), 12, "P7", 1
    )
    assert pv.first_capture(frames) == T0


def test_first_capture_of_nothing_is_none():
    """An empty frame set has no start; returning T0 or now() would label a
    video that does not exist."""
    assert pv.first_capture([]) is None


@pytest.mark.parametrize("cycle", [None, 0, 7])
def test_the_cycle_number_is_carried_through_even_when_null(cycle):
    """Gap detection reads it, and null is a real value the uploader sends."""
    client = _Client([_row(0, "a.tif", cycle=cycle)])
    assert pv.get_plate_frames(client, 12, "P7", 1)[0]["cycle_number"] == cycle


# --- the byte pre-flight -----------------------------------------------------
#
# A plate TIFF measured off the scanners is ~57 MB, so a long run is tens of
# gigabytes. The render is synchronous behind a 240s proxy timeout, so the size
# has to be known before anything is downloaded.

MB = 1024**2
GB = 1024**3


def _sized(*sizes):
    """One frame per size; None means the row did not record one."""
    return [
        {"capture_date": T0, "cycle_number": i, "object_path": f"{i}.tif",
         "file_size_bytes": size}
        for i, size in enumerate(sizes)
    ]


def test_source_bytes_totals_what_will_be_downloaded():
    assert pv.source_bytes(_sized(57 * MB, 57 * MB, 57 * MB)) == (171 * MB, 0)


def test_source_bytes_counts_the_frames_that_did_not_say():
    """`file_size_bytes` is nullable, so the caller has to know the total is a
    floor rather than a measurement."""
    assert pv.source_bytes(_sized(57 * MB, None, 57 * MB)) == (114 * MB, 1)


def test_source_bytes_of_nothing_is_zero_not_an_error():
    assert pv.source_bytes([]) == (0, 0)


def test_a_plate_within_the_limit_is_not_refused():
    """~86 frames at 57 MB is the design's expected ceiling, and must pass."""
    assert pv.too_large_to_render(_sized(*([57 * MB] * 86))) is None


def test_a_multi_day_plate_is_refused():
    """860 frames at 57 MB is ~49 GB — hours of downloading inside a request
    that gives up at 240s."""
    assert pv.too_large_to_render(_sized(*([57 * MB] * 860))) is not None


def test_the_refusal_says_how_big_and_how_much_was_measured():
    """A scientist reading this in a log needs the number, and needs to know it
    is a floor when some rows recorded no size."""
    reason = pv.too_large_to_render(_sized(*([5 * GB] * 2 + [None])))
    assert "10.0 GB" in reason
    assert "2 of 3 frames" in reason


def test_exactly_at_the_limit_is_allowed():
    """A boundary that refuses what it permits elsewhere is a boundary nobody
    can reason about."""
    assert pv.too_large_to_render(_sized(pv.MAX_SOURCE_BYTES)) is None
    assert pv.too_large_to_render(_sized(pv.MAX_SOURCE_BYTES + 1)) is not None


def test_frames_with_no_recorded_size_cannot_trip_the_limit_alone():
    """Nulls sum to nothing, so a plate whose sizes were never recorded is
    allowed through rather than refused on a number nobody measured."""
    assert pv.too_large_to_render(_sized(*([None] * 10_000))) is None


def test_the_size_is_fetched_with_the_frames_not_in_a_second_query():
    client = _Client([_row(0, "a.tif")])
    pv.get_plate_frames(client, 12, "P7", 1)
    assert "file_size_bytes" in client.q.recorded["select"]
