"""Progress output: a long download must not sit silent for hours."""

from __future__ import annotations

import click
import pytest
from click.testing import CliRunner
from test_download_metadata import SCAN
from test_download_session_resume import _Client, _frame, _images, _patch_cli

import bloomctl.cyl.download as dl
from bloomctl.cli import cli


def test_progress_line_reads_as_frames_and_a_percentage():
    assert dl.format_progress("downloading", 12480, 413926) == "12,480/413,926 frames (3.0%)"
    assert dl.format_progress("listing", 5750, 5750) == "Listing frames: 5,750/5,750 scans (100.0%)"


def test_progress_line_has_no_percentage_when_there_is_nothing_to_do():
    assert dl.format_progress("downloading", 0, 0) == "0/0 frames"


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_progress_is_throttled_so_a_fast_run_does_not_spam(capsys):
    clock = _Clock()
    report = dl.ProgressReporter(interval=5.0, now=clock)

    for done in range(1, 101):
        report("downloading", done, 100)

    lines = capsys.readouterr().err.strip().splitlines()
    assert len(lines) == 2, "first frame and last frame only, with the clock frozen"
    assert "1/100" in lines[0]
    assert "100/100 frames (100.0%)" in lines[1]


def test_progress_reports_again_once_the_interval_has_passed(capsys):
    clock = _Clock()
    report = dl.ProgressReporter(interval=5.0, now=clock)

    report("downloading", 1, 100)
    clock.t = 4.0
    report("downloading", 2, 100)  # too soon
    clock.t = 9.0
    report("downloading", 3, 100)  # interval elapsed

    lines = capsys.readouterr().err.strip().splitlines()
    assert [line.split("/")[0].strip() for line in lines] == ["1", "3"]


def test_each_phase_announces_itself(capsys):
    clock = _Clock()
    report = dl.ProgressReporter(interval=5.0, now=clock)

    report("listing", 1, 10)
    report("downloading", 1, 10)  # new phase prints even though no time passed

    err = capsys.readouterr().err
    assert "Listing frames: 1/10 scans" in err
    assert "1/10 frames" in err


def test_download_images_reports_both_phases(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(3))
    seen: list[tuple[str, int, int]] = []

    dl.download_images(
        _Client(), [SCAN], tmp_path, workers=1, on_progress=lambda *a: seen.append(a)
    )

    assert ("listing", 1, 1, 0) in seen
    assert [s for s in seen if s[0] == "downloading"] == [
        ("downloading", 1, 3, 0),
        ("downloading", 2, 3, 0),
        ("downloading", 3, 3, 0),
    ]


def test_every_frame_is_counted_once_when_concurrent(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(200))
    counted: list[int] = []

    dl.download_images(
        _Client(),
        [SCAN],
        tmp_path,
        workers=8,
        on_progress=lambda phase, done, total, failed: counted.append(done)
        if phase == "downloading"
        else None,
    )

    assert counted == list(range(1, 201)), "counted once each, in order, from the main thread"


def test_download_images_stays_quiet_without_a_callback(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(3))

    dl.download_images(_Client(), [SCAN], tmp_path, workers=1)

    assert capsys.readouterr().err == "", "the library must not print on its own"


# --- failures must not read as progress -------------------------------------


class _DeadBucket:
    """Every frame fails, as it would on a full disk or a dead connection."""

    def download(self, object_path):
        raise RuntimeError("[Errno 28] No space left on device")


def test_a_run_where_everything_fails_does_not_report_success(tmp_path, monkeypatch):
    """Counting completions alone showed 100% on a run that downloaded nothing at all."""
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(20))
    client = _Client()
    client.bucket = _DeadBucket()
    lines: list[str] = []

    result = dl.download_images(
        client,
        [SCAN],
        tmp_path,
        workers=4,
        on_progress=lambda phase, done, total, failed: lines.append(
            dl.format_progress(phase, done, total, failed)
        )
        if phase == "downloading"
        else None,
    )

    assert result.ok == 0 and result.failed == 20
    assert lines[-1] == "20/20 frames (100.0%), 20 failed"
    assert all("failed" in line for line in lines), "every line must carry the bad news"


def test_a_partly_failing_run_shows_the_failure_count_growing(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(10))

    class _EveryOtherFails:
        def download(self, object_path):
            if int(object_path.split("/")[-1].split(".")[0]) % 2:
                raise RuntimeError("boom")
            return b"x"

    client = _Client()
    client.bucket = _EveryOtherFails()
    seen: list[tuple[int, int]] = []

    dl.download_images(
        client,
        [SCAN],
        tmp_path,
        workers=1,
        on_progress=lambda phase, done, total, failed: seen.append((done, failed))
        if phase == "downloading"
        else None,
    )

    assert seen[-1] == (10, 5)
    assert [f for _, f in seen] == [0, 1, 1, 2, 2, 3, 3, 4, 4, 5], "failures counted as they happen"


def test_a_healthy_run_says_nothing_about_failures():
    assert dl.format_progress("downloading", 500, 1000) == "500/1,000 frames (50.0%)"
    assert dl.format_progress("downloading", 500, 1000, 0) == "500/1,000 frames (50.0%)"


def test_the_reporter_passes_the_failure_count_through(capsys):
    report = dl.ProgressReporter(interval=0.0)
    report("downloading", 7, 10, 3)
    assert "7/10 frames (70.0%), 3 failed" in capsys.readouterr().err


# --- a full disk stops the run rather than downloading into nowhere ----------


class _DiskFillsAfter:
    """Serves frames until the disk fills, as a real one would."""

    def __init__(self, capacity: int):
        self.capacity = capacity
        self.calls = 0

    def download(self, object_path):
        self.calls += 1
        return b"x"


def _fills_disk_after(
    monkeypatch, capacity: int, code: int | None = None, message: str | None = None
):
    """Make the write fail once `capacity` frames land, with ENOSPC unless told otherwise."""
    import errno
    from pathlib import Path

    code = errno.ENOSPC if code is None else code
    message = message or "No space left on device"
    written = {"n": 0}
    real = Path.write_bytes

    def _write(self, data):
        written["n"] += 1
        if written["n"] > capacity:
            raise OSError(code, message)
        return real(self, data)

    monkeypatch.setattr(Path, "write_bytes", _write)


def test_a_full_disk_stops_further_downloads(tmp_path, monkeypatch):
    """Continuing would pull hundreds of GB off the server only to discard them."""
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(400))
    client = _Client()
    client.bucket = _DiskFillsAfter(50)
    _fills_disk_after(monkeypatch, 50)

    result = dl.download_images(client, [SCAN], tmp_path, workers=1)

    assert result.ok == 50
    assert result.failed == 350
    assert client.bucket.calls == 51, "one frame discovers the full disk; none after it are fetched"


def test_the_frames_after_a_full_disk_say_why(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(10))
    client = _Client()
    client.bucket = _DiskFillsAfter(2)
    _fills_disk_after(monkeypatch, 2)

    result = dl.download_images(client, [SCAN], tmp_path, workers=1)

    assert "No space left on device" in result.frames[2].error
    assert "nothing further was downloaded" in result.frames[-1].error


@pytest.mark.skipif(not hasattr(__import__("errno"), "EDQUOT"), reason="no EDQUOT on this platform")
def test_a_spent_quota_stops_the_run_the_way_a_full_disk_does(tmp_path, monkeypatch):
    """Shared lab storage is usually quota-limited, and the kernel says EDQUOT, never ENOSPC.

    Matching only ENOSPC left such a run fetching every remaining frame off the server and
    throwing each one away for want of somewhere to put it.
    """
    import errno

    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(40))
    client = _Client()
    client.bucket = _DiskFillsAfter(3)
    _fills_disk_after(monkeypatch, 3, code=errno.EDQUOT, message="Disc quota exceeded")

    result = dl.download_images(client, [SCAN], tmp_path, workers=1)

    assert result.disk_full, "the run has to stop; there is nowhere left to write"
    assert client.bucket.calls == 4, "one frame discovers the quota; none after it are fetched"
    assert "Disc quota exceeded" in result.frames[3].error, "the log says which it was"


def test_the_log_records_what_a_full_disk_did(tmp_path, monkeypatch):
    """The log is the artefact a user sends us, so it has to name the cause and the counts.

    The write itself is undone first: on a genuinely full disk it fails too, which is the
    CLI's problem rather than the log's — covered separately below.
    """
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(10))
    client = _Client()
    client.bucket = _DiskFillsAfter(3)
    _fills_disk_after(monkeypatch, 3)

    result = dl.download_images(client, [SCAN], tmp_path, workers=1)
    monkeypatch.undo()
    log = tmp_path / "log.txt"
    dl.write_download_log(result, log)

    text = log.read_text()
    assert "3/10 frames present" in text
    assert "7 failed" in text
    assert "No space left on device" in text
    assert result.incomplete
    summary = text.strip().splitlines()[-1]
    assert "the disk filled up" in summary, "the footer is what people read on a huge log"


def test_the_summary_gives_no_cause_when_the_disk_was_never_the_problem(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(2))

    result = dl.download_images(_Client(budget=1), [SCAN], tmp_path, workers=1)
    log = tmp_path / "log.txt"
    dl.write_download_log(result, log)

    summary = log.read_text().strip().splitlines()[-1]
    assert "1 failed" in summary
    assert "filled up" not in summary


def test_an_ordinary_write_error_does_not_stop_the_run(tmp_path, monkeypatch):
    """Only a full disk is everyone's problem; a one-off write failure is just one frame."""
    import errno
    from pathlib import Path

    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(10))
    seen = {"n": 0}
    real = Path.write_bytes

    def _write(self, data):
        seen["n"] += 1
        if seen["n"] == 3:
            raise OSError(errno.EIO, "I/O error")
        return real(self, data)

    monkeypatch.setattr(Path, "write_bytes", _write)
    client = _Client()

    result = dl.download_images(client, [SCAN], tmp_path, workers=1)

    assert result.failed == 1 and result.ok == 9
    assert client.bucket.calls == 10, "every frame was still attempted"


def test_work_already_in_flight_finishes_after_the_disk_fills(tmp_path, monkeypatch):
    """The stop is checked when a frame starts, so frames already running carry on. What
    matters is that the overshoot is bounded by the worker count rather than unbounded."""
    workers = 8
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(500))
    client = _Client()
    client.bucket = _DiskFillsAfter(20)
    _fills_disk_after(monkeypatch, 20)

    result = dl.download_images(client, [SCAN], tmp_path, workers=workers)

    assert result.ok == 20
    # 20 land, then the frames already running when the flag went up also make their request.
    assert 20 < client.bucket.calls <= 20 + workers, (
        f"{client.bucket.calls} requests — overshoot should be bounded by {workers} workers"
    )
    assert client.bucket.calls < 500, "the rest were never fetched"


# --- a full disk must not take the reporting down with it --------------------


def test_the_result_records_that_the_disk_filled(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(10))
    client = _Client()
    client.bucket = _DiskFillsAfter(3)
    _fills_disk_after(monkeypatch, 3)

    result = dl.download_images(client, [SCAN], tmp_path, workers=1)

    assert result.disk_full, "the run knows why it stopped, so the error can say so"


def test_a_run_that_ends_normally_does_not_claim_the_disk_filled(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(2))

    result = dl.download_images(_Client(), [SCAN], tmp_path, workers=1)

    assert not result.disk_full


def test_a_failed_log_write_leaves_the_previous_log_intact(tmp_path, monkeypatch):
    """write_text() truncates before writing, so a full disk would leave an empty file."""
    import errno
    from pathlib import Path

    log = tmp_path / "download_log.txt"
    log.write_text("the log from the run before\n")

    real = Path.write_bytes

    def _no_space(self, data):
        # Create the file first, then fail — a real ENOSPC leaves a temp file behind, and
        # failing before creating it would make the cleanup assertion below unfalsifiable.
        real(self, b"")
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(Path, "write_bytes", _no_space)

    with pytest.raises(OSError):
        dl.write_download_log(dl.DownloadResult([]), log)

    assert log.read_text() == "the log from the run before\n"
    assert not list(tmp_path.glob(".dl-*.tmp")), "no temp file left behind"


def test_a_full_disk_reports_counts_and_cause_instead_of_a_traceback(tmp_path, monkeypatch):
    """The real #629 failure: the log write is what fails, and it took the summary with it."""
    out = tmp_path / "out"
    _patch_cli(monkeypatch, _Client())
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(10))
    _fills_disk_after(monkeypatch, 4)

    result = CliRunner().invoke(
        cli, ["cyl", "download", str(out), "--experiment-id", "17957", "--workers", "1"]
    )

    assert result.exit_code != 0
    assert not isinstance(result.exception, OSError), "a full disk is a message, not a traceback"
    assert "frames present" in result.output, "the counts survive the log write failing"
    assert "Could not write download_log.txt: No space left on device" in result.output
    assert "the disk filled up" in result.output
    assert "Re-running the same command" in result.output


# --- a bad output path should fail before any of the work ---------------------


def test_an_unwritable_output_directory_fails_before_signing_in(tmp_path, monkeypatch):
    """Nothing is written until after every metadata query, so without this check a typo in
    the path costs the whole metadata phase before it is reported."""
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    reached = []
    monkeypatch.setattr(
        "bloomctl.credentials.load_credentials", lambda *a, **k: reached.append("signed in")
    )

    result = CliRunner().invoke(
        cli, ["cyl", "download", str(locked / "out"), "--experiment-id", "17957"]
    )

    locked.chmod(0o700)
    assert result.exit_code != 0
    assert not isinstance(result.exception, OSError), "a bad path is a message, not a traceback"
    assert "cannot write to" in result.output
    assert "permission" in result.output
    assert reached == [], "it gave up before signing in or querying anything"


def test_a_writable_directory_is_created_and_left_clean(tmp_path):
    out = tmp_path / "new"

    dl.ensure_writable(out)

    assert out.is_dir()
    assert list(out.iterdir()) == [], "the probe file is cleaned up"


def test_a_path_whose_parent_is_missing_is_refused_rather_than_built(tmp_path):
    """An unmounted drive is the case this exists for.

    Building the whole chain meant `cyl download /Volumes/LabDrive/run3` with the drive
    unmounted created that path on the boot disk and filled it with an experiment the
    scientist meant to put on the drive.
    """
    unmounted = tmp_path / "Volumes" / "LabDrive" / "run3"

    with pytest.raises(click.ClickException) as failure:
        dl.ensure_writable(unmounted)

    assert "does not exist" in str(failure.value)
    assert "mounted" in str(failure.value), "name the cause a scientist will actually have hit"
    assert not unmounted.exists(), "nothing was created"
    assert not (tmp_path / "Volumes").exists()


def test_a_path_blocked_by_a_file_says_so_rather_than_raising(tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("this is a file")

    with pytest.raises(click.ClickException) as failure:
        dl.ensure_writable(blocker / "out")

    assert "cannot write to" in str(failure.value)


# --- pace: how fast, and how much longer -------------------------------------


def test_a_percentage_below_one_still_moves():
    """Integer division sat at 0% for the first ~600 frames of a 60,000-frame run."""
    assert dl.format_progress("downloading", 349, 60336) == "349/60,336 frames (0.6%)"
    assert dl.format_progress("downloading", 1, 60336) == "1/60,336 frames (0.0%)"


def test_the_rate_carries_a_decimal_only_where_it_matters():
    assert dl.format_rate(6.34) == "6.3/s"
    assert dl.format_rate(0.4) == "0.4/s"
    assert dl.format_rate(44.2) == "44/s"
    assert dl.format_rate(1500.0) == "1,500/s"


def test_time_remaining_is_coarse_on_purpose():
    assert dl.format_duration(9540) == "2h39m"
    assert dl.format_duration(3600) == "1h00m"
    assert dl.format_duration(1020) == "17m"
    assert dl.format_duration(45) == "45s"
    assert dl.format_duration(0) == "0s"


def test_the_line_reads_as_pace_then_problems():
    line = dl.format_progress(
        "downloading", 349, 60336, 33, rate=6.34, seconds_left=9540, new_failures=12
    )

    assert line == "349/60,336 frames (0.6%)  6.3/s  ~2h39m left, 33 failed (+12)"


def test_a_finished_phase_does_not_claim_time_remaining():
    line = dl.format_progress("downloading", 60336, 60336, rate=44.0, seconds_left=None)

    assert "left" not in line
    assert "44/s" in line


def test_the_reporter_works_out_the_rate_from_its_own_clock(capsys):
    clock = _Clock()
    report = dl.ProgressReporter(interval=5.0, now=clock)

    report("downloading", 0, 1000)
    clock.t = 10.0
    report("downloading", 100, 1000)  # 100 frames in 10s -> 10/s, 900 left -> 90s

    line = capsys.readouterr().err.strip().splitlines()[-1]
    assert "10/s" in line
    assert "~1m left" in line


def test_the_rate_follows_a_connection_that_slows_down(capsys):
    """A whole-run average would still be boasting about the first minute an hour later."""
    clock = _Clock()
    report = dl.ProgressReporter(interval=1.0, now=clock)

    for step in range(1, dl.RATE_WINDOW_SAMPLES + 2):  # fast: 100 frames/s
        clock.t = step * 1.0
        report("downloading", step * 100, 100000)
    fast = capsys.readouterr().err.strip().splitlines()[-1]

    done = (dl.RATE_WINDOW_SAMPLES + 1) * 100
    for step in range(1, dl.RATE_WINDOW_SAMPLES + 2):  # then a crawl: 1 frame/s
        clock.t += 1.0
        report("downloading", done + step, 100000)
    slow = capsys.readouterr().err.strip().splitlines()[-1]

    assert "100/s" in fast
    assert "1.0/s" in slow, f"the window should have caught up, got {slow!r}"


# --- failures: recoverable, and whether they are still arriving ---------------


def test_the_retry_hint_is_said_once_when_failures_first_appear(capsys):
    report = dl.ProgressReporter(interval=0.0)

    report("downloading", 1, 10)
    report("downloading", 2, 10, 1)
    report("downloading", 3, 10, 2)

    err = capsys.readouterr().err
    assert err.count(dl.RETRY_HINT) == 1, "said once, not on every line"
    assert dl.RETRY_HINT not in err.splitlines()[0], "not before anything has failed"


def test_no_retry_hint_on_a_run_where_nothing_fails(capsys):
    report = dl.ProgressReporter(interval=0.0)

    for done in range(1, 4):
        report("downloading", done, 3)

    assert dl.RETRY_HINT not in capsys.readouterr().err


def test_growing_failures_are_marked_and_a_plateau_is_not(capsys):
    """The real run climbed to 987 and stopped; nothing on screen said it had stopped."""
    clock = _Clock()
    report = dl.ProgressReporter(interval=0.0, now=clock)

    report("downloading", 100, 1000, 33)
    report("downloading", 200, 1000, 193)  # still failing
    report("downloading", 300, 1000, 193)  # stopped
    report("downloading", 400, 1000, 193)

    lines = [ln for ln in capsys.readouterr().err.splitlines() if "/1,000" in ln]
    assert "(+33)" in lines[0]
    assert "(+160)" in lines[1]
    assert "(+" not in lines[2], "no marker once they stop arriving"
    assert "193 failed" in lines[2], "the total still stands"
    assert "(+" not in lines[3]


def test_the_percentage_never_reads_complete_while_frames_are_outstanding():
    """`.1f` rounds up, so 60,335/60,336 displayed as 100.0% — the same kind of lie as 0%."""
    assert dl.format_progress("downloading", 60335, 60336) == "60,335/60,336 frames (99.9%)"
    assert dl.format_progress("downloading", 1999, 2000) == "1,999/2,000 frames (99.9%)"
    # the low end stays accurate rather than being floored
    assert dl.format_progress("downloading", 349, 60336) == "349/60,336 frames (0.6%)"
    assert dl.format_progress("downloading", 60336, 60336) == "60,336/60,336 frames (100.0%)"


def test_a_resumed_run_stops_quoting_the_skipped_frames(capsys):
    """Frames already on disk complete in an instant. Carrying that rate into the estimate
    said "~0s left" with ten minutes of downloading to go."""
    clock = _Clock()
    report = dl.ProgressReporter(interval=5.0, now=clock)

    clock.t = 1.0
    report("downloading", 55000, 60336)  # the skip burst
    clock.t = 6.0
    report("downloading", 55040, 60336)  # real downloads begin
    clock.t = 11.0
    report("downloading", 55080, 60336)

    last = capsys.readouterr().err.strip().splitlines()[-1]
    assert "8.0/s" in last, f"should have forgotten the burst, got {last!r}"
    assert "~0s left" not in last
    assert "~10m left" in last


def test_the_rate_from_the_listing_phase_does_not_leak_into_the_download_estimate(capsys):
    """Listing runs at a completely different pace; carrying it over misreports the download."""
    clock = _Clock()
    report = dl.ProgressReporter(interval=0.0, now=clock)

    clock.t = 1.0
    report("listing", 500, 500)  # 500 scans listed fast
    clock.t = 2.0
    report("downloading", 10, 1000)
    clock.t = 12.0
    report("downloading", 20, 1000)

    last = capsys.readouterr().err.strip().splitlines()[-1]
    assert "1.0/s" in last, f"download pace, not the listing pace, got {last!r}"


def test_a_full_disk_is_caught_by_the_writability_check(tmp_path, monkeypatch):
    """`touch()` creates a 0-byte file, which needs no data blocks and so succeeds on a full
    volume — the check has to write something to mean anything."""
    _fills_disk_after(monkeypatch, 0)

    with pytest.raises(click.ClickException) as failure:
        dl.ensure_writable(tmp_path / "out")

    assert "cannot write to" in str(failure.value)
    assert "No space left on device" in str(failure.value)


def test_frames_already_on_disk_are_still_reported_present_when_the_disk_fills(
    tmp_path, monkeypatch
):
    """A resumed run that runs out of space must not disown what it already fetched.

    Checking `stop` before looking at the file reported every remaining frame as missing —
    including the ones sitting complete on disk — in the log we ask people to send us.
    """
    import errno

    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(6))
    client = _Client()
    dl.download_images(client, [SCAN], tmp_path, workers=1)  # first run: everything lands
    _frame(tmp_path, 0).unlink()  # one frame goes missing

    client = _Client()
    client.bucket = _DiskFillsAfter(0)
    _fills_disk_after(monkeypatch, 0, code=errno.ENOSPC)

    result = dl.download_images(client, [SCAN], tmp_path, workers=1)

    assert result.disk_full, "the run still stops; there is nowhere to write the missing frame"
    assert result.failed == 1, "only the frame that actually needed writing failed"
    assert result.skipped == 5, "the five still on disk are present, not missing"
