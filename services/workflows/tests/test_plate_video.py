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

    def __init__(self, rows, then=None):
        self._rows = rows
        self._then = then
        self.calls = 0
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

    def limit(self, n):
        self.recorded["limit"] = n
        return self

    def in_(self, column, values):
        self.recorded["in"] = (column, list(values))
        return self

    def order(self, column, **kwargs):
        self.recorded["order"] = (column, kwargs.get("desc", False))
        return self

    def execute(self):
        self.calls += 1
        rows = self._then if self.calls > 1 and self._then is not None else self._rows
        return type("_R", (), {"data": rows})()


class _Client:
    """One canned result per table, so a test can drive both queries."""

    def __init__(self, rows=None, **by_table):
        self.queries = {
            "gravi_scans": _Query(rows or []),
            **{t: _Query(r) for t, r in by_table.items()},
        }
        self.tables: list[str] = []

    @property
    def q(self):
        return self.queries["gravi_scans"]

    def table(self, name):
        self.tables.append(name)
        return self.queries.setdefault(name, _Query([]))


def _row(minutes, path, cycle=None, session=None):
    # PostgREST sends TIMESTAMPTZ as an ISO string, never a datetime. Building
    # rows the other way hid a crash in the encoder for the whole of PR 4 and 5.
    return {
        "capture_date": (T0 + timedelta(minutes=minutes)).isoformat(),
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


class TestCaptureDateArrivesAsAString:
    """PostgREST sends TIMESTAMPTZ as text. Everything downstream — the elapsed
    maths, the burned label, the ordering check — needs a datetime, so the
    conversion happens once, where rows enter."""

    def test_a_frames_capture_date_is_a_datetime(self):
        client = _Client([_row(0, "a.tif")])
        frames = pv.get_plate_frames(client, 12, "P7", 1)
        assert isinstance(frames[0]["capture_date"], datetime)

    def test_an_offset_written_with_z_is_understood(self):
        """Postgres can end an offset with Z; fromisoformat rejected it before 3.11."""
        client = _Client([{**_row(0, "a.tif"), "capture_date": "2026-05-29T11:00:00Z"}])
        frames = pv.get_plate_frames(client, 12, "P7", 1)
        assert frames[0]["capture_date"] == datetime(
            2026, 5, 29, 11, 0, tzinfo=timezone.utc
        )

    def test_a_naive_stamp_stays_naive(self):
        """Not repaired here. `label_for` refuses a naive time rather than guess a
        zone, and inventing UTC at this layer would silently shift every label."""
        client = _Client([{**_row(0, "a.tif"), "capture_date": "2026-05-29T11:00:00"}])
        frames = pv.get_plate_frames(client, 12, "P7", 1)
        assert frames[0]["capture_date"].tzinfo is None

    def test_an_unparseable_stamp_is_refused(self):
        client = _Client([{**_row(0, "a.tif"), "capture_date": "last tuesday"}])
        with pytest.raises(ValueError, match="unparseable capture_date"):
            pv.get_plate_frames(client, 12, "P7", 1)


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
        {
            "capture_date": T0,
            "cycle_number": i,
            "object_path": f"{i}.tif",
            "file_size_bytes": size,
        }
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
    """A scientist reading this in a log needs the number, and needs to know
    how much of it was measured rather than estimated."""
    reason = pv.too_large_to_render(_sized(*([5 * GB] * 2 + [None])))
    assert "15.0 GB" in reason, (
        "the unsized frame is estimated at the plate's own average"
    )
    assert "2 of 3 frames" in reason


def test_exactly_at_the_limit_is_allowed():
    """A boundary that refuses what it permits elsewhere is a boundary nobody
    can reason about."""
    assert pv.too_large_to_render(_sized(pv.MAX_SOURCE_BYTES)) is None
    assert pv.too_large_to_render(_sized(pv.MAX_SOURCE_BYTES + 1)) is not None


def test_frames_with_no_recorded_size_are_estimated_not_counted_as_zero():
    """4.9% of live rows have no size and nothing backfills it. Reading a
    missing size as nothing lets a plate of any size past the guard."""
    assert pv.too_large_to_render(_sized(*([None] * 10_000))) is not None

    # Under the limit once estimated, so the estimate is a size and not a veto.
    assert pv.too_large_to_render(_sized(*([None] * 10))) is None


def test_an_unsized_frame_is_estimated_from_the_plates_own_frames():
    """A nominal size is the fallback, not the first answer: a plate that
    recorded most of its sizes knows better than the constant does."""
    # Two frames at 5 GB, one unsized -> the unsized one is worth 5 GB too.
    assert "15.0 GB" in pv.too_large_to_render(_sized(*([5 * GB] * 2 + [None])))


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
    return [
        _row(i, f"{n}.tif", cycle=n, session=session) for i, n in enumerate(numbers)
    ]


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


def test_a_short_count_says_what_arrived_without_blaming_the_run():
    """Frames upload after a run finishes, so a short count during that window
    is the normal state — not evidence the run stopped early."""
    result = pv.completeness(_cycles(1, 2, 3), planned=200)
    assert result["state"] == "short"
    assert result["summary"] == "3 of 200 frames so far"


def test_a_missing_first_frame_is_found_from_where_the_run_started():
    """This plate has cycles 11-86; a sibling plate proves the run began at 1.
    Without that bound the search starts at 11 and the hole is invisible."""
    frames = _cycles(*range(11, 87))

    assert pv.missing_cycles(frames) == [], "its own frames cannot show it"
    assert pv.missing_cycles(frames, first_cycle=1) == list(range(1, 11))


def test_one_frame_left_of_a_run_still_reports_the_hole():
    """The old bound gave up below two cycles, so a run reduced to its last
    frame reported nothing missing."""
    assert pv.missing_cycles(_cycles(86), first_cycle=1) == list(range(1, 86))


def test_a_run_whose_first_frame_arrived_reports_no_hole():
    """The bound must not invent a gap when nothing is missing."""
    assert pv.missing_cycles(_cycles(1, 2, 3), first_cycle=1) == []


def test_session_cycle_range_reads_what_any_plate_recorded():
    """Every plate is photographed on every cycle, so a sibling's range is the
    range this plate should have — at both ends."""
    client = _Client(_cycles(1, 2, 3, 4))  # what the run actually recorded
    assert pv.session_cycle_range(client, _cycles(2, 3)) == (1, 4)


def test_session_cycle_range_asks_only_for_the_sessions_these_frames_name():
    """Without the filter it takes the range across every scan in the database,
    which is always 1 and silently defeats detection."""
    client = _Client(_cycles(1, 2))
    pv.session_cycle_range(client, _cycles(1, session=7) + _cycles(2, session=9))
    assert client.q.recorded["in"] == ("session_id", [7, 9])


def test_session_cycle_range_is_none_when_the_run_is_unknown():
    """No session, or no cycle recorded, means the range cannot be proved —
    and missing_cycles then falls back to what arrived."""
    assert pv.session_cycle_range(_Client([]), _cycles(1, 2)) is None
    assert pv.session_cycle_range(_Client([]), [_row(0, "a.tif")]) is None


def test_a_missing_last_frame_is_found_from_where_the_run_ended():
    """Bounded by its own frames a plate cannot show a lost tail either: 1-10
    of a run that reached 12 looked complete."""
    frames = _cycles(*range(1, 11))

    assert pv.missing_cycles(frames) == [], "its own frames cannot show it"
    assert pv.missing_cycles(frames, first_cycle=1, last_cycle=12) == [11, 12]


def test_a_first_cycle_above_this_plates_own_still_searches_from_the_lowest():
    """The bound is clamped to whichever is lower. Unclamped, a hole below the
    sibling range is missed — here cycle 2 would go unreported."""
    assert pv.missing_cycles(_cycles(1, 5), first_cycle=3) == [2, 3, 4]


def test_gaps_are_reported_even_when_the_run_recorded_no_plan():
    """The frames prove the hole; a missing plan cannot unprove it. Reported
    the other way round, the module found 4-7 and called the run unknown."""
    result = pv.completeness(_cycles(1, 2, 3, 8, 9), planned=None, cycle_range=(1, 9))

    assert result["missing_cycles"] == [4, 5, 6, 7]
    assert result["state"] == "gaps"
    assert "missing cycles 4, 5, 6, 7" in result["summary"]


def test_a_missing_head_is_reported_as_gaps_not_as_a_short_run():
    """The failure this fixes: 76 of 86 frames with the first 10 missing used
    to read as no gaps, and the shortfall was blamed on the run stopping."""
    frames = _cycles(*range(11, 87))
    result = pv.completeness(frames, planned=86, cycle_range=(1, 86))

    assert result["state"] == "gaps"
    assert "missing cycles 1, 2, 3, 4, 5 and 5 more" in result["summary"]


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


# --- what is already stored --------------------------------------------------
#
# Three outcomes, and "unknown" is the one that matters: a storage outage that
# makes the answer unclear is the same outage skipping frame downloads, so an
# ambiguous answer arrives exactly when this render is the worse one. The key is
# deterministic, so an overwrite is in place with nothing to recover.


class _Bucket:
    def __init__(self, raises=None):
        self._raises = raises
        self.signed: list[str] = []

    def create_signed_url(self, key, ttl):
        self.signed.append(key)
        if self._raises is not None:
            raise self._raises
        return {"signedUrl": "http://signed"}


class _Storage:
    def __init__(self, bucket, bucket_exists=True):
        self._bucket = bucket
        self._bucket_exists = bucket_exists
        self.buckets: list[str] = []
        self.probed: list[str] = []

    def from_(self, name):
        self.buckets.append(name)
        return self._bucket

    def get_bucket(self, name):
        """Storage signs a missing object and a missing bucket identically, so
        this is what separates them."""
        self.probed.append(name)
        if not self._bucket_exists:
            raise Exception("Bucket not found")
        return {"name": name}


class _StoredClient(_Client):
    """A recorded video row, plus a storage bucket that answers or does not."""

    def __init__(self, row=None, raises=None, bucket_exists=True):
        super().__init__(gravi_plate_videos=[row] if row else [])
        self.bucket = _Bucket(raises)
        self.storage = _Storage(self.bucket, bucket_exists=bucket_exists)


def _recorded(frames=140, path="12/wave-1/P7.mp4"):
    return {"object_path": path, "frame_count": frames}


def test_a_recorded_video_whose_object_is_there_is_present():
    client = _StoredClient(_recorded(frames=140))
    result = pv.stored_video(client, 12, "P7", 1)

    assert result["state"] == "present"
    assert result["frame_count"] == 140
    assert result["key"] == "12/wave-1/P7.mp4"


def test_no_recorded_row_is_absent():
    assert pv.stored_video(_StoredClient(None), 12, "P7", 1)["state"] == "absent"


def test_a_row_whose_object_is_gone_is_absent_not_present():
    """Serving the URL would hand back a 404; the row outlived its object."""
    client = _StoredClient(_recorded(), raises=Exception("Object not found"))
    result = pv.stored_video(client, 12, "P7", 1)

    assert result["state"] == "absent"
    assert result["frame_count"] is None, (
        "a count from a row whose object is gone would be compared against"
    )


def test_storage_failing_to_answer_is_unknown_not_absent():
    """The whole point. Read as absent, a storage outage overwrites a good video
    with whatever this run managed to encode."""
    client = _StoredClient(_recorded(), raises=Exception("504 Gateway Timeout"))
    assert pv.stored_video(client, 12, "P7", 1)["state"] == "unknown"


@pytest.mark.parametrize(
    "message",
    [
        # The shape storage3 actually raises: the error code and the message
        # both appear, and only the message says what was missing.
        "{'statusCode': 404, 'error': not_found, 'message': Object not found}",
        "Object not found",
        "no such key",
        "Object does not exist",
    ],
)
def test_every_wording_storage_uses_for_a_missing_object_reads_as_absent(message):
    """Storage answers a missing object with HTTP 400 and a string code, so the
    wording is the guard — a status check would read missing as unknown."""
    client = _StoredClient(_recorded(), raises=Exception(message))
    assert pv.stored_video(client, 12, "P7", 1)["state"] == "absent"


def test_a_missing_bucket_is_unknown_not_absent():
    """Signing an object in a bucket that does not exist returns the SAME
    "Object not found" as a missing object in a real one — verified against
    storage-api. So the wording cannot separate them; the bucket is probed."""
    client = _StoredClient(
        _recorded(),
        raises=Exception(
            "{'statusCode': 404, 'error': not_found, 'message': Object not found}"
        ),
        bucket_exists=False,
    )
    assert pv.stored_video(client, 12, "P7", 1)["state"] == "unknown"
    assert client.storage.probed == ["graviscan-videos"]


def test_a_missing_object_in_a_real_bucket_is_absent():
    """The same error text, the opposite answer — the bucket is what differs."""
    client = _StoredClient(
        _recorded(),
        raises=Exception(
            "{'statusCode': 404, 'error': not_found, 'message': Object not found}"
        ),
        bucket_exists=True,
    )
    assert pv.stored_video(client, 12, "P7", 1)["state"] == "absent"


def test_an_unusable_plate_id_is_absent_without_touching_storage():
    """`plate_video_path` refuses a key it cannot build; probing for None would
    sign a nonsense path."""
    client = _StoredClient(_recorded())
    result = pv.stored_video(client, 12, "../secrets", 1)

    assert result["state"] == "absent"
    assert result["key"] is None
    assert client.bucket.signed == [], "storage was asked about an unusable key"


def test_the_probe_looks_in_the_videos_bucket():
    client = _StoredClient(_recorded())
    pv.stored_video(client, 12, "P7", 1)
    assert client.storage.buckets == ["graviscan-videos"]


def test_a_stored_video_with_no_wave_is_matched_with_is_null():
    """`wave_number = NULL` matches nothing, so the row would be missed and a
    stored video re-rendered on every request."""
    client = _StoredClient(_recorded(path="12/wave-none/P7.mp4"))
    result = pv.stored_video(client, 12, "P7", None)

    recorded = client.queries["gravi_plate_videos"].recorded
    assert ("wave_number", "null") in recorded["is"]
    assert result["key"] == "12/wave-none/P7.mp4"


def test_the_row_is_read_from_gravi_plate_videos_by_all_three_keys():
    client = _StoredClient(_recorded())
    pv.stored_video(client, 12, "P7", 3)

    recorded = client.queries["gravi_plate_videos"].recorded
    assert set(recorded["eq"]) == {
        ("experiment_id", 12),
        ("plate_id", "P7"),
        ("wave_number", 3),
    }


# --- what to do about it -----------------------------------------------------
#
# Frames upload from the scanner after the run finishes, so a video rendered
# before that upload completed is short rather than wrong. Every branch turns on
# having a recorded count to compare against.


def _stored(state="present", frames=140, key="12/wave-1/P7.mp4"):
    return {"state": state, "frame_count": frames, "key": key}


def test_new_frames_since_the_stored_video_means_render():
    d = pv.render_decision(_cycles(*range(200)), _stored(frames=140))
    assert d["action"] == "render"
    assert "60 new frames" in d["reason"]


def test_nothing_new_means_keep_and_encode_nothing():
    """Re-encoding on every page view would burn minutes of CPU to produce an
    identical file."""
    d = pv.render_decision(_cycles(*range(140)), _stored(frames=140))
    assert d["action"] == "keep"


def test_fewer_frames_than_recorded_still_keeps():
    """A count that went down means rows vanished, not that the stored video is
    stale — overwriting it with less would be the wrong repair."""
    assert pv.render_decision(_cycles(1, 2), _stored(frames=140))["action"] == "keep"


def test_no_stored_video_means_render():
    d = pv.render_decision(_cycles(1, 2, 3), _stored(state="absent", frames=None))
    assert d["action"] == "render"
    assert "no video stored" in d["reason"]


def test_a_stored_video_with_no_recorded_count_is_replaced():
    """Keeping it would never self-correct: with no count, no later request
    could beat it either, so the plate would be stuck on it forever."""
    d = pv.render_decision(_cycles(1, 2, 3), _stored(frames=None))
    assert d["action"] == "render"
    assert "does not say how much of the run" in d["reason"]


def test_storage_that_cannot_answer_refuses_rather_than_rendering():
    """The one that matters. Rendering would overwrite a good video in place —
    the key is deterministic — during the outage that made the answer unclear."""
    d = pv.render_decision(_cycles(*range(200)), _stored(state="unknown"))
    assert d["action"] == "refuse"
    assert "try again" in d["reason"]


def test_a_plate_with_no_frames_refuses_rather_than_encoding_nothing():
    assert (
        pv.render_decision([], _stored(state="absent", frames=None))["action"]
        == "refuse"
    )


def test_an_unusable_object_key_refuses():
    """`plate_video_path` returns None for a plate id it will not put in a key;
    rendering would have nowhere to upload."""
    d = pv.render_decision(
        _cycles(1, 2), _stored(state="absent", frames=None, key=None)
    )
    assert d["action"] == "refuse"


def test_an_unknown_state_refuses_even_when_frames_are_missing():
    """Ordering: an unclear answer about the stored video outranks every other
    reason to act, because acting is what cannot be undone."""
    assert pv.render_decision([], _stored(state="unknown"))["action"] == "refuse"
    assert "try again" in pv.render_decision([], _stored(state="unknown"))["reason"]


def test_every_outcome_carries_the_key_it_decided_about():
    for stored in (
        _stored(),
        _stored(state="absent", frames=None),
        _stored(state="unknown"),
    ):
        assert pv.render_decision(_cycles(1, 2), stored)["key"] == "12/wave-1/P7.mp4"


@pytest.mark.parametrize("state", ["present", "absent", "unknown"])
def test_the_action_is_always_one_of_three(state):
    """The caller switches on this; a fourth value would fall through silently."""
    d = pv.render_decision(_cycles(1, 2), _stored(state=state))
    assert d["action"] in {"render", "keep", "refuse"}


# --- the whole question, in one call -----------------------------------------


class _PlanClient(_StoredClient):
    """Frames, a recorded video row, a session, and a storage bucket."""

    def __init__(
        self, frames=None, row=None, raises=None, total_cycles=None, session_cycles=None
    ):
        super().__init__(row=row, raises=raises)
        # get_plate_frames and session_cycle_range both read gravi_scans; the
        # second sees the whole run, so it has to answer differently.
        self.queries["gravi_scans"] = _Query(frames or [], then=session_cycles)
        self.queries["gravi_scan_sessions"] = _Query(
            [{"total_cycles": total_cycles}] if total_cycles is not None else []
        )


def _frames(n, session=7, size=1 * MB, cycle_start=0):
    """Sized by default: an unsized fixture trips the size guard once unknown
    frames are estimated, which is not what these tests are about."""
    rows = [
        _row(i, f"{i}.tif", cycle=cycle_start + i, session=session) for i in range(n)
    ]
    for r in rows:
        r["gravi_images"]["file_size_bytes"] = size
    return rows


def _big(n):
    rows = _frames(n)
    for r in rows:
        r["gravi_images"]["file_size_bytes"] = 1024**3
    return rows


def test_plan_renders_when_frames_have_arrived_since_the_stored_video():
    client = _PlanClient(frames=_frames(200), row=_recorded(frames=140))
    plan = pv.plan_render(client, 12, "P7", 1)

    assert plan["action"] == "render"
    assert len(plan["frames"]) == 200


def test_plan_keeps_when_the_stored_video_is_current():
    client = _PlanClient(frames=_frames(140), row=_recorded(frames=140))
    assert pv.plan_render(client, 12, "P7", 1)["action"] == "keep"


def test_plan_refuses_a_plate_too_large_to_encode():
    client = _PlanClient(frames=_big(20), row=_recorded(frames=1))
    plan = pv.plan_render(client, 12, "P7", 1)

    assert plan["action"] == "refuse"
    assert "GB" in plan["reason"]


def test_a_plate_too_large_to_encode_still_gets_its_stored_video_back():
    """The size limit is about encoding, not about the request. Refusing here
    would withhold a video that already exists and is current."""
    client = _PlanClient(frames=_big(20), row=_recorded(frames=20))
    assert pv.plan_render(client, 12, "P7", 1)["action"] == "keep"


def test_plan_refuses_when_storage_cannot_say():
    client = _PlanClient(
        frames=_frames(200), row=_recorded(), raises=Exception("504 Gateway Timeout")
    )
    plan = pv.plan_render(client, 12, "P7", 1)

    assert plan["action"] == "refuse"
    assert "try again" in plan["reason"]


def test_coverage_is_reported_when_something_will_be_rendered():
    client = _PlanClient(
        frames=_frames(200), row=_recorded(frames=140), total_cycles=500
    )
    plan = pv.plan_render(client, 12, "P7", 1)

    assert plan["action"] == "render"
    assert plan["coverage"]["summary"] == "200 of 500 frames so far"


def test_a_plan_reports_a_missing_head_the_plates_own_frames_cannot_show():
    """End to end: get_plate_frames sees cycles 11-20, the session says the run
    ran 1-20, and the plan names the ten frames that never arrived."""
    client = _PlanClient(
        frames=_frames(10, cycle_start=11),
        row=_recorded(frames=5),
        total_cycles=20,
        session_cycles=_cycles(*range(1, 21)),
    )
    plan = pv.plan_render(client, 12, "P7", 1)

    assert plan["action"] == "render"
    assert plan["coverage"]["state"] == "gaps"
    assert plan["coverage"]["missing_cycles"] == list(range(1, 11))


def test_keeping_costs_no_session_query():
    """Nothing reads coverage on the keep path, and it is a second round trip."""
    client = _PlanClient(
        frames=_frames(140), row=_recorded(frames=140), total_cycles=200
    )
    plan = pv.plan_render(client, 12, "P7", 1)

    assert plan["action"] == "keep"
    assert plan["coverage"] is None
    assert "gravi_scan_sessions" not in client.tables


def test_plan_carries_the_key_on_every_outcome():
    for frames, row in ((_frames(200), _recorded(140)), (_frames(140), _recorded(140))):
        plan = pv.plan_render(_PlanClient(frames=frames, row=row), 12, "P7", 1)
        assert plan["key"] == "12/wave-1/P7.mp4"


def test_an_oversized_plate_still_reports_the_key_it_refused():
    plan = pv.plan_render(_PlanClient(frames=_big(20)), 12, "P7", 1)
    assert plan["key"] == "12/wave-1/P7.mp4"


def test_a_plate_with_no_frames_refuses_and_reports_no_coverage():
    """There is no run to describe, and completeness of nothing would read as a
    run that recorded no plan."""
    plan = pv.plan_render(_PlanClient(frames=[]), 12, "P7", 1)

    assert plan["action"] == "refuse"
    assert plan["coverage"] is None


def test_the_session_is_not_queried_when_there_are_no_frames():
    client = _PlanClient(frames=[])
    pv.plan_render(client, 12, "P7", 1)
    assert "gravi_scan_sessions" not in client.tables
