"""list_available_experiments's n_traits staleness messaging (bloom#637 round 8, bloom#708).

Round 8 review found that a bare `(as of {ts})` string looks identical whether
the cache refreshed a minute ago or was never refreshed again after a
one-time migration-time population. This locks in that an elapsed time past
`_STALE_AFTER` gets an explicit staleness flag instead of reading like
ordinary bounded lag.

bloom#708: production now refreshes automatically on a daily schedule while
staging remains on-demand (dispatch) only -- `_traits_note` has no way to
tell which environment a given row came from (design.md D8's addendum), so
its flag wording is deliberately environment-neutral rather than asserting
"not automatically" unconditionally, which would be false for a production
row.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from bloom_mcp.sections.core.list_available_experiments import _traits_note

_NOW = datetime(2026, 8, 19, 12, 0, 0, tzinfo=UTC)


def test_never_refreshed_is_unchanged():
    assert _traits_note(None, now=_NOW) == " (never refreshed)"


def test_recently_refreshed_shows_plain_timestamp():
    ts = (_NOW - timedelta(hours=6)).isoformat()
    assert _traits_note(ts, now=_NOW) == f" (as of {ts})"


def test_just_under_the_stale_threshold_shows_plain_timestamp():
    ts = (_NOW - timedelta(days=2) + timedelta(minutes=1)).isoformat()
    note = _traits_note(ts, now=_NOW)
    assert note == f" (as of {ts})"
    assert "may be older than the environment's own refresh cadence" not in note


def test_past_the_stale_threshold_flags_it():
    ts = (_NOW - timedelta(days=5)).isoformat()
    note = _traits_note(ts, now=_NOW)
    assert ts in note
    assert "5d ago" in note
    # bloom#708: must NOT unconditionally claim "not automatically" -- that's
    # false for a production row, which now refreshes on a daily schedule.
    assert "not automatically" not in note
    assert "may be older than the environment's own refresh cadence" in note


def test_postgrest_z_suffix_is_handled():
    # PostgREST returns timestamptz as an ISO-8601 string with a trailing Z,
    # which `datetime.fromisoformat` cannot parse directly pre-3.11.
    ts = "2026-08-17T06:00:00Z"
    note = _traits_note(ts, now=_NOW)
    assert "2d ago" in note or "3d ago" in note
    assert "may be older than the environment's own refresh cadence" in note


def test_unparseable_timestamp_falls_back_without_raising():
    note = _traits_note("not-a-real-timestamp", now=_NOW)
    assert note == " (as of not-a-real-timestamp)"
