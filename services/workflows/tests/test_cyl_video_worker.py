"""Unit tests for the cyl video worker's `render` command (stubs the render
seam — no DB, storage or ffmpeg), matching test_dispatch_worker.py's convention."""

import pytest
from fastapi import HTTPException

import cyl_video_worker as worker


def _argv(*extra):
    return ["render", "--experiment", "1", "--scan", "5", *extra]


@pytest.fixture
def stub_client(monkeypatch):
    client = object()
    monkeypatch.setattr(worker, "app_client", lambda: client)
    return client


def _stub_render(monkeypatch, result=None, raises=None, seen=None):
    def fake(experiment_id, scan_id, client=None):
        if seen is not None:
            seen.update(experiment_id=experiment_id, scan_id=scan_id, client=client)
        if raises is not None:
            raise raises
        return result

    monkeypatch.setattr(worker, "generate_experiment_scan_video", fake)


def _rendered(**overrides):
    """The shape `video._result` returns, so a stub cannot drift from it."""
    result = {
        "frames": 72,
        "frames_expected": 72,
        "truncated": False,
        "regenerated": True,
        "path": "cyl-videos/5.mp4",
        "download_url": "https://example.test/5.mp4",
    }
    return {**result, **overrides}


def test_parses_the_experiment_and_scan():
    args = worker.parse_args(_argv())
    assert (args.experiment, args.scan) == (1, 5)


def test_a_scan_is_required():
    with pytest.raises(SystemExit):
        worker.parse_args(["render", "--experiment", "1"])


@pytest.mark.parametrize("bad", ["0", "-1", "abc"])
def test_an_impossible_id_is_refused_before_any_render(bad):
    with pytest.raises(SystemExit):
        worker.parse_args(["render", "--experiment", "1", "--scan", bad])


def test_the_arguments_and_client_reach_the_renderer(monkeypatch, stub_client):
    seen = {}
    _stub_render(monkeypatch, result=_rendered(), seen=seen)
    worker.main(_argv())
    assert seen == {"experiment_id": 1, "scan_id": 5, "client": stub_client}


def test_a_rendered_scan_exits_ok_and_says_so(monkeypatch, stub_client, capsys):
    _stub_render(monkeypatch, result=_rendered())
    assert worker.main(_argv()) == worker.EXIT_OK == 0
    out = capsys.readouterr().out
    assert "rendered" in out and "72" in out and "cyl-videos/5.mp4" in out


def test_frames_that_could_not_be_read_are_reported(monkeypatch, stub_client, capsys):
    """An unreadable frame is skipped, not fatal. 60 of 72 angles is not a clean
    success, and the missing angle is invisible in the finished video."""
    _stub_render(monkeypatch, result=_rendered(frames=60))
    assert worker.main(_argv()) == worker.EXIT_OK
    assert (
        "rendered 60 frames (12 of 72 could not be read) to cyl-videos/5.mp4"
        in capsys.readouterr().out
    )


def test_a_kept_scan_does_not_claim_the_count_describes_the_file(
    monkeypatch, stub_client, capsys
):
    """`frames` is the image rows on one keep branch and the video record's own
    count on the others, and the result does not say which — so the line reports
    it as what the service said, not as a fact about the stored file."""
    _stub_render(monkeypatch, result=_rendered(regenerated=False))
    assert worker.main(_argv()) == worker.EXIT_OK
    out = capsys.readouterr().out
    assert "kept" in out and "was not remade" in out and "cyl-videos/5.mp4" in out
    assert "is current" not in out


@pytest.mark.parametrize("regenerated", [True, False])
def test_a_truncated_scan_says_so_whether_it_was_remade_or_kept(
    monkeypatch, stub_client, capsys, regenerated
):
    """A kept truncated scan is the case that hides: the video covers the cap
    and the rest of the scan is not in it and never will be."""
    _stub_render(monkeypatch, result=_rendered(truncated=True, regenerated=regenerated))
    assert worker.main(_argv()) == worker.EXIT_OK
    assert "more frames than the encoder's cap" in capsys.readouterr().out


@pytest.mark.parametrize(
    "status,detail",
    [
        (404, "Scan 5 not found in experiment 1"),
        (404, "No images found for scan 5"),
    ],
)
def test_the_renderer_declining_exits_refused(
    monkeypatch, stub_client, capsys, status, detail
):
    _stub_render(monkeypatch, raises=HTTPException(status_code=status, detail=detail))
    assert worker.main(_argv()) == worker.EXIT_REFUSED
    assert detail in capsys.readouterr().out


@pytest.mark.parametrize(
    "status,detail",
    [
        (500, "Video encoding failed for scan 5"),
        (500, "Could not create a download URL for scan 5"),
        (503, "Could not check the recorded video for scan 5."),
    ],
)
def test_this_service_failing_exits_failed_not_refused(
    monkeypatch, stub_client, capsys, status, detail
):
    """ "Refused" means nothing happened. The download-URL failure is raised after
    the object was uploaded, so calling it a refusal tells the operator the
    opposite of what occurred."""
    _stub_render(monkeypatch, raises=HTTPException(status_code=status, detail=detail))
    assert worker.main(_argv()) == worker.EXIT_FAILED
    out = capsys.readouterr().out
    assert "failed" in out and detail in out


def test_refused_is_not_argparse_s_own_exit_code():
    assert worker.EXIT_REFUSED != 2
    assert worker.EXIT_OK == 0 and worker.EXIT_FAILED == 1


def test_the_two_workers_agree_on_their_exit_codes():
    """PR 3's claim loops read these codes to decide retry from dead-letter."""
    import plate_video_worker as plate

    assert (worker.EXIT_OK, worker.EXIT_FAILED, worker.EXIT_REFUSED) == (
        plate.EXIT_OK,
        plate.EXIT_FAILED,
        plate.EXIT_REFUSED,
    )


def test_an_unexpected_failure_exits_failed(monkeypatch, stub_client):
    _stub_render(monkeypatch, raises=RuntimeError("ffmpeg exited -9"))
    assert worker.main(_argv()) == worker.EXIT_FAILED


def test_a_complete_render_says_nothing_about_unread_frames(
    monkeypatch, stub_client, capsys
):
    """Widening the condition to `<=` would make every clean render claim 0 of
    72 could not be read."""
    _stub_render(monkeypatch, result=_rendered())
    worker.main(_argv())
    assert "could not be read" not in capsys.readouterr().out


def test_a_result_without_regenerated_is_treated_as_a_render(
    monkeypatch, stub_client, capsys
):
    """The default must not report a fresh render as kept."""
    result = _rendered()
    del result["regenerated"]
    _stub_render(monkeypatch, result=result)
    assert worker.main(_argv()) == worker.EXIT_OK
    assert "rendered" in capsys.readouterr().out


def test_the_refusal_names_the_status(monkeypatch, stub_client, capsys):
    _stub_render(
        monkeypatch, raises=HTTPException(status_code=404, detail="No images found")
    )
    worker.main(_argv())
    assert "refused (404)" in capsys.readouterr().out


def test_a_failure_after_the_upload_warns_that_the_record_may_disagree(
    monkeypatch, stub_client, capsys
):
    """Signing the URL is the last step before the row is written, so a 5xx can
    leave the object stored and the record stale."""
    _stub_render(
        monkeypatch,
        raises=HTTPException(status_code=500, detail="Could not create a download URL"),
    )
    assert worker.main(_argv()) == worker.EXIT_FAILED
    assert "check the stored video against its record" in capsys.readouterr().out


def test_help_exits_zero_and_carries_the_hold():
    with pytest.raises(SystemExit) as exit_info:
        worker.parse_args(["--help"])
    assert exit_info.value.code == 0
    assert "until the render queue lands" in worker.build_parser().epilog


def test_an_oversized_id_is_accepted_because_the_columns_are_bigint():
    """The cyl id columns are BIGINT and the route bounds nothing, so a bound
    here would refuse ids the database accepts. It finds no row and is refused."""
    assert worker.identifier("99999999999999999999") == 99999999999999999999
