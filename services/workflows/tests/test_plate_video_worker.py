"""Unit tests for the plate video worker's `render` command (stubs the render
seam — no DB, storage or ffmpeg), matching test_dispatch_worker.py's convention."""

import pytest

import plate_video_worker as worker
from plate_encode import (
    EncoderBusy,
    FrameDepthUnsupported,
    FrameSizeMismatch,
    FrameTooLarge,
    NotRecorded,
    PlateBusy,
    PlateMismatch,
    VideoNotStored,
)
from plate_request import MAX_EXPERIMENT_ID, MAX_WAVE_NUMBER


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


@pytest.mark.parametrize("spelling", ["none", "NONE", "  none  "])
def test_a_none_wave_is_no_wave(spelling):
    """A plate with no wave is a real case, and the renderer takes None for it."""
    assert worker.parse_args(_argv("--wave", spelling)).wave is None


def test_an_omitted_wave_is_no_wave():
    assert worker.parse_args(_argv()).wave is None


def test_wave_zero_is_a_wave_not_a_missing_one():
    assert worker.parse_args(_argv("--wave", "0")).wave == 0


@pytest.mark.parametrize("bad", ["-1", "99999999999999999999", "abc", ""])
def test_an_impossible_wave_is_refused_before_any_render(bad):
    """The route bounds these; unbounded, an oversized wave overflows an INT
    column and the failure reads as a database outage instead of a typo."""
    with pytest.raises(SystemExit):
        worker.parse_args(_argv("--wave", bad))


@pytest.mark.parametrize("bad", ["0", "-1", "99999999999999999999"])
def test_an_impossible_experiment_id_is_refused_before_any_render(bad):
    with pytest.raises(SystemExit):
        worker.parse_args(["render", "--experiment", bad, "--plate", "Plate_1"])


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


def test_a_rendered_plate_exits_ok_and_says_so(monkeypatch, stub_client, capsys):
    _stub_render(
        monkeypatch,
        outcome={"action": "rendered", "recorded": {"frame_count": 86}, "key": "k.mp4"},
    )
    assert worker.main(_argv("--wave", "13")) == worker.EXIT_OK == 0
    out = capsys.readouterr().out
    assert "rendered" in out and "86" in out and "k.mp4" in out


def test_a_kept_plate_exits_ok_and_reports_the_stored_count(
    monkeypatch, stub_client, capsys
):
    _stub_render(
        monkeypatch,
        outcome={
            "action": "keep",
            "stored_frames": 86,
            # No digits in the reason: the count has to come from `frames_in`.
            "reason": "the stored video already covers every frame",
        },
    )
    assert worker.main(_argv("--wave", "13")) == worker.EXIT_OK
    assert "kept (86 frames)" in capsys.readouterr().out


def test_a_kept_plate_says_why_it_was_kept(monkeypatch, stub_client, capsys):
    """Three different situations keep a video, and two of them are anomalies an
    operator running a batch needs to see — frames that are not visible, and
    frames that have gone missing from the database."""
    _stub_render(
        monkeypatch,
        outcome={
            "action": "keep",
            "stored_frames": 86,
            "reason": "the stored video covers 86 frames; only 3 are in the database now",
        },
    )
    worker.main(_argv("--wave", "13"))
    assert "only 3 are in the database now" in capsys.readouterr().out


def test_a_refusal_exits_refused_and_names_its_code(monkeypatch, stub_client, capsys):
    _stub_render(
        monkeypatch,
        outcome={
            "action": "refuse",
            "code": "no_frames",
            "reason": "this plate has no frames yet",
        },
    )
    assert worker.main(_argv("--wave", "13")) == worker.EXIT_REFUSED
    assert "no_frames" in capsys.readouterr().out


def test_refused_is_not_argparse_s_own_exit_code():
    """argparse exits 2 on a usage error. A caller looping over plates treats
    refusal as "noted, carry on", so a typo'd flag must not look like one."""
    assert worker.EXIT_REFUSED != 2
    assert worker.EXIT_OK == 0 and worker.EXIT_FAILED == 1


def test_an_encoder_failure_exits_failed(monkeypatch, stub_client):
    _stub_render(monkeypatch, raises=RuntimeError("ffmpeg exited -9"))
    assert worker.main(_argv("--wave", "13")) == worker.EXIT_FAILED


@pytest.mark.parametrize(
    "busy", [EncoderBusy("an encode is running"), PlateBusy("mine")]
)
def test_a_busy_renderer_is_reported_as_busy_not_refused(
    monkeypatch, stub_client, capsys, busy
):
    """`EncoderBusy` is documented as "not a failure — a reason to come back",
    so it must not carry the code that says do not retry."""
    _stub_render(monkeypatch, raises=busy)
    assert worker.main(_argv("--wave", "13")) == worker.EXIT_FAILED
    assert "busy" in capsys.readouterr().out


def test_an_action_this_command_does_not_know_is_not_a_success(
    monkeypatch, stub_client, capsys
):
    """A fourth action would otherwise be reported as a render that happened."""
    _stub_render(monkeypatch, outcome={"action": "deferred"})
    assert worker.main(_argv("--wave", "13")) == worker.EXIT_FAILED
    assert "deferred" in capsys.readouterr().out


def test_a_rendered_plate_with_no_recorded_count_falls_back_to_its_frames(
    monkeypatch, stub_client, capsys
):
    """Same fallback order as the route, so the two never disagree."""
    _stub_render(
        monkeypatch,
        outcome={"action": "rendered", "frames": [{"n": 1}, {"n": 2}], "key": "k.mp4"},
    )
    worker.main(_argv("--wave", "13"))
    assert "rendered 2 frames" in capsys.readouterr().out


@pytest.mark.parametrize("code", ["storage_unavailable", "database_unavailable"])
def test_a_transient_refusal_tells_the_caller_to_retry(
    monkeypatch, stub_client, capsys, code
):
    """The route answers 503 for these two. Exit 3 would tell a batch loop to
    stop retrying while the message on the same line says try again."""
    _stub_render(
        monkeypatch,
        outcome={
            "action": "refuse",
            "code": code,
            "reason": "this video cannot be made right now. Try again shortly",
        },
    )
    assert worker.main(_argv("--wave", "13")) == worker.EXIT_FAILED
    out = capsys.readouterr().out
    assert out.startswith("failed"), "the word must agree with the exit code"
    assert code in out


@pytest.mark.parametrize("code", ["no_frames", "unusable_plate", "too_large"])
def test_a_permanent_refusal_says_do_not_retry(monkeypatch, stub_client, capsys, code):
    _stub_render(
        monkeypatch, outcome={"action": "refuse", "code": code, "reason": "no"}
    )
    assert worker.main(_argv("--wave", "13")) == worker.EXIT_REFUSED
    assert capsys.readouterr().out.startswith("refused")


@pytest.mark.parametrize(
    "exc",
    [
        FrameTooLarge("this plate scanned past the limit"),
        FrameDepthUnsupported("a frame carries no fixed full scale"),
        FrameSizeMismatch("does not match the rest of the plate"),
    ],
)
def test_a_permanent_frame_problem_is_refused_not_retried(
    monkeypatch, stub_client, exc
):
    """These are permanent properties of the plate's own images; the route
    answers 413/422. A loop that retries re-downloads and re-encodes forever."""
    _stub_render(monkeypatch, raises=exc)
    assert worker.main(_argv("--wave", "13")) == worker.EXIT_REFUSED


@pytest.mark.parametrize(
    "exc",
    [
        VideoNotStored("storage did not answer"),
        PlateMismatch("refusing to store a crossed identity"),
    ],
)
def test_this_service_failing_is_retryable(monkeypatch, stub_client, exc):
    _stub_render(monkeypatch, raises=exc)
    assert worker.main(_argv("--wave", "13")) == worker.EXIT_FAILED


def test_a_stored_but_unrecorded_video_says_so(monkeypatch, stub_client, capsys):
    """The object exists and the row does not: re-rendering is not the fix, and
    an operator needs to know which half landed."""
    _stub_render(monkeypatch, raises=NotRecorded("1886/wave-13/Plate_1.mp4"))
    assert worker.main(_argv("--wave", "13")) == worker.EXIT_FAILED
    assert "stored but not recorded" in capsys.readouterr().out


def test_the_legal_maximum_wave_and_experiment_are_accepted():
    """The off-by-one the "way too big" cases cannot see."""
    assert worker.wave(str(MAX_WAVE_NUMBER)) == MAX_WAVE_NUMBER
    args = worker.parse_args(
        ["render", "--experiment", str(MAX_EXPERIMENT_ID), "--plate", "P1"]
    )
    assert args.experiment == MAX_EXPERIMENT_ID


def test_help_exits_zero_and_carries_the_hold():
    """`--help` is the container's default command, so it is the one surface an
    operator meets by default."""
    with pytest.raises(SystemExit) as exit_info:
        worker.parse_args(["--help"])
    assert exit_info.value.code == 0


def test_the_hold_is_stated_where_help_will_print_it():
    """The module docstring is not what argparse shows; the epilog is."""
    epilog = worker.build_parser().epilog

    assert "until the render queue lands" in epilog
    assert "3 refused" in epilog
