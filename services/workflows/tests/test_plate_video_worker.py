"""Unit tests for the plate video worker's `render` command (stubs the render
seam — no DB, storage or ffmpeg), matching test_dispatch_worker.py's convention."""

import pytest
from fastapi import HTTPException

import plate_video_worker as worker


def _argv(*extra):
    return ["render", "--experiment", "1886", "--plate", "Plate_1", *extra]


@pytest.fixture
def stub_client(monkeypatch):
    """The command builds its own app-user client; give it one that is never used."""
    client = object()
    monkeypatch.setattr(worker, "app_client", lambda: client)
    return client


def _stub_render(monkeypatch, outcome=None, raises=None, seen=None):
    def fake(client, experiment_id, plate_id, wave_number):
        if seen is not None:
            seen.update(
                client=client,
                experiment_id=experiment_id,
                plate_id=plate_id,
                wave_number=wave_number,
            )
        if raises is not None:
            raise raises
        return outcome

    monkeypatch.setattr(worker, "render_plate_video", fake)


def test_parses_the_experiment_plate_and_wave():
    args = worker.parse_args(_argv("--wave", "13"))
    assert (args.experiment, args.plate, args.wave) == (1886, "Plate_1", 13)


def test_a_none_wave_is_no_wave():
    """A plate with no wave is a real case, and the renderer takes None for it."""
    assert worker.parse_args(_argv("--wave", "none")).wave is None


def test_an_omitted_wave_is_no_wave():
    assert worker.parse_args(_argv()).wave is None


def test_wave_zero_is_a_wave_not_a_missing_one():
    assert worker.parse_args(_argv("--wave", "0")).wave == 0


def test_a_negative_wave_is_refused_before_any_render():
    with pytest.raises(SystemExit):
        worker.parse_args(_argv("--wave", "-1"))


def test_the_arguments_reach_the_renderer(monkeypatch, stub_client):
    seen = {}
    _stub_render(monkeypatch, outcome={"action": "rendered"}, seen=seen)
    worker.main(_argv("--wave", "13"))
    assert seen == {
        "client": stub_client,
        "experiment_id": 1886,
        "plate_id": "Plate_1",
        "wave_number": 13,
    }


def test_a_rendered_plate_exits_zero_and_says_so(monkeypatch, stub_client, capsys):
    _stub_render(
        monkeypatch,
        outcome={"action": "rendered", "recorded": {"frame_count": 86}},
    )
    assert worker.main(_argv("--wave", "13")) == 0
    out = capsys.readouterr().out
    assert "rendered" in out and "86" in out


def test_a_kept_plate_exits_zero_and_says_kept(monkeypatch, stub_client, capsys):
    _stub_render(monkeypatch, outcome={"action": "keep", "stored_frames": 86})
    assert worker.main(_argv("--wave", "13")) == 0
    assert "kept" in capsys.readouterr().out


def test_a_refusal_exits_non_zero_and_names_its_code(monkeypatch, stub_client, capsys):
    _stub_render(
        monkeypatch,
        outcome={
            "action": "refuse",
            "code": "no_frames",
            "reason": "this plate has no frames yet",
        },
    )
    code = worker.main(_argv("--wave", "13"))
    assert code != 0
    assert "no_frames" in capsys.readouterr().out


def test_an_encoder_failure_exits_non_zero(monkeypatch, stub_client):
    _stub_render(monkeypatch, raises=RuntimeError("ffmpeg exited -9"))
    assert worker.main(_argv("--wave", "13")) != 0


def test_a_refused_request_exits_non_zero(monkeypatch, stub_client):
    """The renderer answers some refusals by raising, not by returning."""
    _stub_render(monkeypatch, raises=HTTPException(status_code=429, detail="busy"))
    assert worker.main(_argv("--wave", "13")) != 0
