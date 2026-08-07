"""Progress output: a long download must not sit silent for hours."""

from __future__ import annotations

from test_download_metadata import SCAN
from test_download_session_resume import _Client, _images

import bloomctl.cyl.download as dl


def test_progress_line_reads_as_frames_and_a_percentage():
    assert dl.format_progress("downloading", 12480, 413926) == "12,480/413,926 frames (3%)"
    assert dl.format_progress("listing", 5750, 5750) == "Listing frames: 5,750/5,750 scans (100%)"


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
    assert "100/100 frames (100%)" in lines[1]


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

    assert ("listing", 1, 1) in seen
    assert [s for s in seen if s[0] == "downloading"] == [
        ("downloading", 1, 3),
        ("downloading", 2, 3),
        ("downloading", 3, 3),
    ]


def test_every_frame_is_counted_once_when_concurrent(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(200))
    counted: list[int] = []

    dl.download_images(
        _Client(),
        [SCAN],
        tmp_path,
        workers=8,
        on_progress=lambda phase, done, total: counted.append(done)
        if phase == "downloading"
        else None,
    )

    assert counted == list(range(1, 201)), "counted once each, in order, from the main thread"


def test_download_images_stays_quiet_without_a_callback(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(3))

    dl.download_images(_Client(), [SCAN], tmp_path, workers=1)

    assert capsys.readouterr().err == "", "the library must not print on its own"
