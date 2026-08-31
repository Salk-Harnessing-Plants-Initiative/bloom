"""Tests for the per-run Box report.

The report is the only part of this job a person reads without server access,
so these assertions pin the two properties that make it useful: the filename
survives Box's character rules and sorts chronologically, and the body still
records a run that failed.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import backup_lib as lib
import report


def make_report(**overrides) -> report.RunReport:
    defaults = dict(
        env="prod",
        run_id=42,
        started_at=datetime(2026, 8, 31, 2, 17, 3, tzinfo=timezone.utc),
        finished_at=datetime(2026, 8, 31, 6, 45, 12, tzinfo=timezone.utc),
        outcome="ok",
        box_root="Bloom-Backups/BloomV2-Data-Backup/prod/storage",
        stats={"listed": 10, "copied": 8, "failed": 0, "skipped": 2, "already_current": 0},
        failures=[],
    )
    defaults.update(overrides)
    return report.RunReport(**defaults)


class TestFilename:
    def test_carries_no_character_box_rejects(self):
        name = make_report().filename()
        assert not (set(name) & lib.BOX_ILLEGAL_CHARS), name
        assert not name.endswith(tuple(lib.BOX_TRAILING_ILLEGAL))

    def test_has_no_colon_despite_being_a_timestamp(self):
        # The obvious ISO format would be 2026-08-31T02:17:03Z; ':' is illegal
        # on Box, so the time is compacted instead.
        assert ":" not in make_report().filename()

    def test_sorts_chronologically_as_a_string(self):
        early = make_report(
            started_at=datetime(2026, 8, 24, 2, 17, tzinfo=timezone.utc), run_id=41
        ).filename()
        late = make_report(
            started_at=datetime(2026, 8, 31, 2, 17, tzinfo=timezone.utc), run_id=42
        ).filename()
        assert sorted([late, early]) == [early, late]

    def test_two_runs_the_same_day_do_not_collide(self):
        first = make_report(run_id=1).filename()
        second = make_report(run_id=2).filename()
        assert first != second

    def test_names_the_environment(self):
        assert "-prod-" in make_report(env="prod").filename()
        assert "-staging-" in make_report(env="staging").filename()

    def test_normalises_a_non_utc_timestamp(self):
        pacific = timezone(timedelta(hours=-7))
        name = make_report(
            started_at=datetime(2026, 8, 30, 19, 17, 3, tzinfo=pacific)
        ).filename()
        # 19:17 -07:00 is 02:17Z the next day.
        assert name.startswith("2026-08-31T021703Z")


class TestBody:
    def test_records_the_stats_verbatim(self):
        body = json.loads(make_report().to_json())
        assert body["stats"]["copied"] == 8
        assert body["outcome"] == "ok"
        assert body["env"] == "prod"
        assert body["run_id"] == 42

    def test_computes_the_duration(self):
        body = json.loads(make_report().to_json())
        assert body["duration_seconds"] == 16089.0

    def test_a_failed_run_still_produces_a_body(self):
        body = json.loads(make_report(outcome="error", stats={}).to_json())
        assert body["outcome"] == "error"

    def test_names_failures_so_they_can_be_acted_on(self):
        body = json.loads(make_report(failures=["images/a.png", "images/b.png"]).to_json())
        assert body["failures"] == ["images/a.png", "images/b.png"]
        assert body["failures_truncated"] is False
        assert body["failure_count"] == 2

    def test_truncates_a_flood_of_failures_but_keeps_the_true_count(self):
        many = [f"images/{n}.png" for n in range(report.MAX_REPORTED_FAILURES + 50)]
        body = json.loads(make_report(failures=many).to_json())
        assert len(body["failures"]) == report.MAX_REPORTED_FAILURES
        assert body["failures_truncated"] is True
        assert body["failure_count"] == report.MAX_REPORTED_FAILURES + 50

    def test_is_valid_json_and_ends_with_a_newline(self):
        text = make_report().to_json()
        assert text.endswith("\n")
        json.loads(text)


class TestPaths:
    def test_box_path_sits_under_the_run_root(self):
        entry = make_report()
        assert report.box_remote_path(entry) == (
            "Bloom-Backups/BloomV2-Data-Backup/prod/storage/_runs/" + entry.filename()
        )

    def test_box_path_tolerates_a_root_with_stray_slashes(self):
        entry = make_report(box_root="/Bloom-Backups/prod/")
        assert report.box_remote_path(entry).startswith("Bloom-Backups/prod/_runs/")

    def test_box_path_handles_an_empty_root(self):
        entry = make_report(box_root="")
        assert report.box_remote_path(entry) == "_runs/" + entry.filename()

    def test_write_local_creates_the_directory_and_file(self, tmp_path: Path):
        entry = make_report()
        written = report.write_local(entry, tmp_path)
        assert written.parent.name == report.REPORTS_DIRNAME
        assert json.loads(written.read_text())["run_id"] == 42

    def test_write_local_is_repeatable(self, tmp_path: Path):
        entry = make_report()
        report.write_local(entry, tmp_path)
        again = report.write_local(entry, tmp_path)
        assert again.exists()
