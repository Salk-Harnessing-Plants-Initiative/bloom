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


def test_parses_the_experiment_and_scan():
    args = worker.parse_args(_argv())
    assert (args.experiment, args.scan) == (1, 5)


def test_a_scan_is_required():
    with pytest.raises(SystemExit):
        worker.parse_args(["render", "--experiment", "1"])


def test_the_arguments_and_client_reach_the_renderer(monkeypatch, stub_client):
    seen = {}
    _stub_render(monkeypatch, result={"regenerated": True, "frames": 72}, seen=seen)
    worker.main(_argv())
    assert seen == {"experiment_id": 1, "scan_id": 5, "client": stub_client}


def test_a_rendered_scan_exits_zero_and_says_so(monkeypatch, stub_client, capsys):
    _stub_render(
        monkeypatch,
        result={"regenerated": True, "frames": 72, "truncated": False},
    )
    assert worker.main(_argv()) == 0
    out = capsys.readouterr().out
    assert "rendered" in out and "72" in out


def test_a_kept_scan_exits_zero_and_says_kept(monkeypatch, stub_client, capsys):
    _stub_render(
        monkeypatch,
        result={"regenerated": False, "frames": 72, "truncated": False},
    )
    assert worker.main(_argv()) == 0
    assert "kept" in capsys.readouterr().out


def test_a_truncated_scan_says_it_was_truncated(monkeypatch, stub_client, capsys):
    """A scan past the frame cap renders its capped set; silence would hide that."""
    _stub_render(
        monkeypatch,
        result={
            "regenerated": True,
            "frames": 72,
            "frames_expected": 72,
            "truncated": True,
        },
    )
    assert worker.main(_argv()) == 0
    assert "truncated" in capsys.readouterr().out


def test_a_scan_outside_the_experiment_exits_non_zero(monkeypatch, stub_client, capsys):
    _stub_render(
        monkeypatch,
        raises=HTTPException(
            status_code=404, detail="Scan 5 not found in experiment 1"
        ),
    )
    code = worker.main(_argv())
    assert code != 0
    assert "not found" in capsys.readouterr().out


def test_an_encoder_failure_exits_non_zero(monkeypatch, stub_client):
    _stub_render(monkeypatch, raises=RuntimeError("ffmpeg exited -9"))
    assert worker.main(_argv()) != 0
