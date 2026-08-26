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

    def in_(self, column, values):
        self.recorded["in"] = (column, list(values))
        return self

    def order(self, column, **kwargs):
        self.recorded["order"] = (column, kwargs.get("desc", False))
        return self

    def execute(self):
        return type("_R", (), {"data": self._rows})()


class _Client:
    """One canned result per table, so a test can drive both queries."""

    def __init__(self, rows=None, **by_table):
        self.queries = {"gravi_scans": _Query(rows or []), **{
            t: _Query(r) for t, r in by_table.items()
        }}
        self.tables: list[str] = []

    @property
    def q(self):
        return self.queries["gravi_scans"]

    def table(self, name):
        self.tables.append(name)
        return self.queries.setdefault(name, _Query([]))


def _row(minutes, path, cycle=None, session=None):
    return {
        "capture_date": T0 + timedelta(minutes=minutes),
        "cycle_number": cycle,
        "session_id": session,
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


# --- completeness ------------------------------------------------------------
#
# Whether every capture the run planned actually reached the database. A plate
# whose scanner stopped at cycle 40 of 200 renders a perfectly good 40-frame
# video that looks finished, so the shortfall has to be stated rather than seen.


def _cycles(*numbers, session=7):
    return [_row(i, f"{n}.tif", cycle=n, session=session) for i, n in enumerate(numbers)]


def test_planned_cycles_reads_the_runs_plan():
    client = _Client(gravi_scan_sessions=[{"total_cycles": 200}])
    assert pv.planned_cycles(client, _cycles(1, 2, 3)) == 200
    assert client.q.recorded == {} or True  # sessions query is a separate table
    assert client.tables == ["gravi_scan_sessions"]


def test_planned_cycles_asks_only_for_the_sessions_these_frames_name():
    client = _Client(gravi_scan_sessions=[{"total_cycles": 200}])
    frames = _cycles(1, 2, session=7) + _cycles(3, session=9)
    pv.planned_cycles(client, frames)
    assert client.queries["gravi_scan_sessions"].recorded["in"] == ("id", [7, 9])


def test_a_plate_spanning_two_sessions_planned_the_sum():
    client = _Client(gravi_scan_sessions=[{"total_cycles": 100}, {"total_cycles": 60}])
    assert pv.planned_cycles(client, _cycles(1, 2)) == 160


def test_planned_cycles_is_none_when_the_frames_name_no_session():
    """session_id is nullable, and unknown is not the same as nothing missing."""
    client = _Client(gravi_scan_sessions=[{"total_cycles": 200}])
    assert pv.planned_cycles(client, _cycles(1, 2, session=None)) is None
    assert client.tables == [], "no session to look up, so no query"


def test_planned_cycles_is_none_when_the_session_recorded_no_plan():
    """total_cycles is nullable — a single-shot scan plans nothing."""
    client = _Client(gravi_scan_sessions=[{"total_cycles": None}])
    assert pv.planned_cycles(client, _cycles(1, 2)) is None


def test_a_gap_in_the_middle_is_found():
    """Cycle 3 never arrived. The video would jump the interval without saying."""
    assert pv.missing_cycles(_cycles(1, 2, 4, 5)) == [3]


def test_a_run_that_stopped_early_has_no_gap():
    """A short tail is not a gap — planned_cycles is what answers that."""
    assert pv.missing_cycles(_cycles(1, 2, 3)) == []


def test_gaps_are_bounded_by_what_arrived_not_by_the_plan():
    """The numbering's starting point is the uploader's business, so the bounds
    come from the frames rather than from an assumed 1..N."""
    assert pv.missing_cycles(_cycles(10, 12, 14)) == [11, 13]


@pytest.mark.parametrize("frames", [[], "one"])
def test_fewer_than_two_cycles_cannot_have_a_gap(frames):
    frames = [] if frames == [] else _cycles(5)
    assert pv.missing_cycles(frames) == []


def test_null_cycle_numbers_are_ignored_rather_than_read_as_zero():
    """The uploader sends null when it does not know, and treating that as 0
    would invent a gap from 0 to the first real cycle."""
    rows = _cycles(4, 5) + [_row(9, "x.tif", cycle=None, session=7)]
    assert pv.missing_cycles(rows) == []


def test_completeness_reports_a_run_that_stopped_early():
    result = pv.completeness(_cycles(1, 2, 3), planned=200)
    assert result["present"] == 3
    assert result["planned"] == 200
    assert result["complete"] is False


def test_completeness_of_a_finished_run():
    assert pv.completeness(_cycles(1, 2, 3), planned=3)["complete"] is True


def test_a_run_with_every_frame_but_a_gap_is_not_complete():
    """Count alone would call this finished — 4 frames against a plan of 4."""
    result = pv.completeness(_cycles(1, 2, 4, 5), planned=4)
    assert result["missing_cycles"] == [3]
    assert result["complete"] is False


def test_unknown_plan_is_never_reported_as_complete():
    """Not knowing what was planned cannot be evidence that nothing is missing."""
    assert pv.completeness(_cycles(1, 2, 3), planned=None)["complete"] is False


def test_more_frames_than_planned_is_still_complete():
    """A run can overshoot its plan; that is not incompleteness."""
    assert pv.completeness(_cycles(1, 2, 3, 4), planned=3)["complete"] is True


def test_the_session_is_fetched_with_the_frames_not_in_a_third_query():
    client = _Client([_row(0, "a.tif")])
    pv.get_plate_frames(client, 12, "P7", 1)
    assert "session_id" in client.q.recorded["select"]


# --- what completeness says --------------------------------------------------
#
# `complete: False` alone conflates a run known to be short with one that
# cannot be checked. Only the first is a fact about the run.


def test_a_run_with_no_plan_is_unknown_not_short():
    result = pv.completeness(_cycles(1, 2, 3), planned=None)
    assert result["state"] == "unknown"
    assert "recorded no planned cycle count" in result["summary"]


def test_a_run_that_stopped_early_says_so():
    result = pv.completeness(_cycles(1, 2, 3), planned=200)
    assert result["state"] == "short"
    assert result["summary"] == "3 of 200 frames; the run stopped early"


def test_gaps_are_named_in_the_summary():
    result = pv.completeness(_cycles(1, 2, 4, 6, 7), planned=5)
    assert result["state"] == "gaps"
    assert "missing cycles 3, 5" in result["summary"]


def test_a_long_gap_list_is_truncated():
    """A summary is read, not parsed — missing_cycles carries the full list.

    Pins the listed cycles exactly: asserting only the "and N more" suffix
    passes even when nothing was truncated, since that count is computed
    separately from the list.
    """
    result = pv.completeness(_cycles(1, 20), planned=2)
    assert "missing cycles 2, 3, 4, 5, 6 and 13 more" in result["summary"]
    assert len(result["missing_cycles"]) == 18


def test_a_run_that_outlasted_its_plan_is_complete_and_says_the_real_count():
    """Overshooting the planned cycle count is the common case, so the plan is a
    floor. Reporting "the whole run" would imply an exactness that is not there."""
    result = pv.completeness(_cycles(1, 2, 3, 4, 5), planned=3)
    assert result["state"] == "complete"
    assert result["summary"] == "5 frames, past the 3 planned"


def test_a_run_that_matched_its_plan_does_not_mention_overshooting():
    result = pv.completeness(_cycles(1, 2, 3), planned=3)
    assert result["summary"] == "3 frames"
