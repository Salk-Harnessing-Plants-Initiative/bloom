"""Validating a plate-video request and choosing a status for the answer.

No TestClient here — this is the layer between HTTP and the render, so it is
tested directly. `tests/test_main.py` covers the wiring.
"""

from __future__ import annotations

import logging

import pytest
from fastapi import HTTPException

import plate_encode as pe
import plate_request as pr
from plate_encode import EncoderBusy, FrameUnreadable, NotRecorded
from video_writer import VideoEncodeError


@pytest.fixture(autouse=True)
def _no_real_client(monkeypatch):
    monkeypatch.setattr(pr, "app_client", lambda: object())


def _renders(outcome):
    return lambda *a, **k: outcome


def _rendered(**over):
    return {
        "action": "rendered",
        "reason": "no video stored; encoding 3 frames",
        "key": "12/wave-1/P7.mp4",
        "code": "",
        "frames": [{}, {}, {}],
        "coverage": {"state": "complete", "summary": "3 frames"},
        **over,
    }


def test_a_render_reports_what_was_made(monkeypatch):
    monkeypatch.setattr(pr, "render_plate_video", _renders(_rendered()))
    result = pr.render(12, {"plate_id": "P7", "wave_number": 1})

    assert result["action"] == "rendered"
    assert result["object_path"] == "12/wave-1/P7.mp4"
    assert result["frames"] == 3
    assert result["coverage"]["summary"] == "3 frames"


def test_the_request_keys_are_echoed_back(monkeypatch):
    """The caller sent two of them in the body; the response is the record of
    which plate this answer is about."""
    monkeypatch.setattr(pr, "render_plate_video", _renders(_rendered()))
    result = pr.render(12, {"plate_id": "P7", "wave_number": 3})

    assert (result["experiment_id"], result["plate_id"], result["wave_number"]) == (12, "P7", 3)


def test_keeping_a_current_video_is_success_not_an_error(monkeypatch):
    """Nothing was encoded, and nothing went wrong."""
    monkeypatch.setattr(
        pr, "render_plate_video", _renders(_rendered(action="keep", reason="already covers 3"))
    )
    assert pr.render(12, {"plate_id": "P7", "wave_number": 1})["action"] == "keep"


@pytest.mark.parametrize(
    "code,status",
    [
        ("storage_unavailable", 503),
        ("no_frames", 404),
        ("unusable_plate", 400),
        ("too_large", 413),
        ("something_new", 409),
    ],
)
def test_each_refusal_gets_a_status_a_caller_can_act_on(monkeypatch, code, status):
    """A transient storage failure is worth retrying; a plate with no captures
    is not. One status for both would make them indistinguishable."""
    monkeypatch.setattr(
        pr, "render_plate_video", _renders(_rendered(action="refuse", code=code, reason="no"))
    )
    with pytest.raises(HTTPException) as ei:
        pr.render(12, {"plate_id": "P7", "wave_number": 1})
    assert ei.value.status_code == status


def test_a_busy_encoder_says_come_back_rather_than_failing(monkeypatch):
    def busy(*a, **k):
        raise EncoderBusy("4 plate videos are already encoding")

    monkeypatch.setattr(pr, "render_plate_video", busy)
    with pytest.raises(HTTPException) as ei:
        pr.render(12, {"plate_id": "P7", "wave_number": 1})

    assert ei.value.status_code == 429
    assert ei.value.headers["Retry-After"] == "30"


def test_an_unreadable_frame_names_it_without_naming_the_cause(monkeypatch):
    """"A frame failed" sends someone to the scanner. Naming it sends them to it.

    The path only. The rest of the message is the storage client's own error,
    which in the real failure carries the internal gateway host and port, the
    database role and PostgREST's codes — and Caddy publishes this service
    directly, so whatever goes in `detail` reaches the caller unfiltered.
    """
    cause = (
        "{'statusCode':'403','error':'InvalidJwt','message':'jwt expired for role "
        "bloom_workflows at http://kong:8000/storage/v1/object/graviscan-images/...'}"
    )

    def unreadable(*a, **k):
        raise FrameUnreadable(
            f"could not download 12/wave-1/P7_40.tif: {cause}", "12/wave-1/P7_40.tif"
        )

    monkeypatch.setattr(pr, "render_plate_video", unreadable)
    with pytest.raises(HTTPException) as ei:
        pr.render(12, {"plate_id": "P7", "wave_number": 1})

    assert ei.value.status_code == 502
    assert "P7_40.tif" in ei.value.detail, "the caller cannot tell which frame failed"
    for leaked in ("kong:8000", "bloom_workflows", "InvalidJwt", "403"):
        assert leaked not in ei.value.detail, f"{leaked!r} reached the caller"


def test_a_frame_failure_carrying_no_path_still_answers(monkeypatch):
    """`FrameUnreadable` is also raised for a whole run — "no frames to encode"
    has no object to name, and must not render as "None could not be read"."""

    def unreadable(*a, **k):
        raise FrameUnreadable("no frames to encode")

    monkeypatch.setattr(pr, "render_plate_video", unreadable)
    with pytest.raises(HTTPException) as ei:
        pr.render(12, {"plate_id": "P7", "wave_number": 1})

    assert "None" not in ei.value.detail
    assert "frame could not be read" in ei.value.detail


def test_an_unreadable_frame_is_logged_as_well_as_returned(monkeypatch, caplog):
    """The response body is not a record, and it no longer carries the reason —
    only the frame. This log line is the operator's only copy of why."""
    def unreadable(*a, **k):
        raise FrameUnreadable("could not download 12/wave-1/P7_40.tif: connection reset")

    monkeypatch.setattr(pr, "render_plate_video", unreadable)
    with caplog.at_level(logging.WARNING, logger=pr.logger.name):
        with pytest.raises(HTTPException):
            pr.render(12, {"plate_id": "P7", "wave_number": 1})

    assert "P7_40.tif" in caplog.text


def test_a_stored_but_unrecorded_video_is_a_server_error(monkeypatch):
    def unrecorded(*a, **k):
        raise NotRecorded("12/wave-1/P7.mp4 was stored but recording it failed")

    monkeypatch.setattr(pr, "render_plate_video", unrecorded)
    with pytest.raises(HTTPException) as ei:
        pr.render(12, {"plate_id": "P7", "wave_number": 1})
    assert ei.value.status_code == 500


@pytest.mark.parametrize(
    "plate_id",
    ["../secrets", "a/b", "a\\b", ".hidden", "", "P" * 65, None, 7, "a b"],
)
def test_a_plate_id_that_cannot_become_an_object_key_is_refused(monkeypatch, plate_id):
    """Re-validated here rather than trusted from the Next proxy: Caddy
    publishes this service directly, so that hop is not a boundary."""
    monkeypatch.setattr(pr, "render_plate_video", _renders(_rendered()))
    with pytest.raises(HTTPException) as ei:
        pr.render(12, {"plate_id": plate_id, "wave_number": 1})
    assert ei.value.status_code == 400


def test_the_render_is_never_reached_for_a_bad_plate_id(monkeypatch):
    """A 400 alone does not prove the value never travelled."""
    reached = []
    monkeypatch.setattr(pr, "render_plate_video", lambda *a, **k: reached.append(a))

    with pytest.raises(HTTPException):
        pr.render(12, {"plate_id": "../secrets", "wave_number": 1})
    assert reached == []


def test_a_plate_with_no_wave_is_accepted(monkeypatch):
    monkeypatch.setattr(pr, "render_plate_video", _renders(_rendered(key="12/wave-none/P7.mp4")))
    assert pr.render(12, {"plate_id": "P7", "wave_number": None})["wave_number"] is None


def test_a_missing_wave_key_is_the_same_as_a_null_one(monkeypatch):
    monkeypatch.setattr(pr, "render_plate_video", _renders(_rendered()))
    assert pr.render(12, {"plate_id": "P7"})["wave_number"] is None


def test_wave_zero_is_a_wave(monkeypatch):
    """The scanner app sends 0 when none is set, so it arrives in practice."""
    seen = {}
    monkeypatch.setattr(
        pr,
        "render_plate_video",
        lambda c, e, p, w: seen.update(wave=w) or _rendered(),
    )
    pr.render(12, {"plate_id": "P7", "wave_number": 0})
    assert seen["wave"] == 0


@pytest.mark.parametrize("wave", [True, "1", 1.5, -1])
def test_a_wave_that_is_not_a_whole_non_negative_number_is_refused(monkeypatch, wave):
    """`True` is an int subclass and would silently become wave 1."""
    monkeypatch.setattr(pr, "render_plate_video", _renders(_rendered()))
    with pytest.raises(HTTPException) as ei:
        pr.render(12, {"plate_id": "P7", "wave_number": wave})
    assert ei.value.status_code == 400


@pytest.mark.parametrize(
    "failure,status,detail",
    [
        (
            pe.FrameDepthUnsupported("a I;16 frame carries no fixed full scale"),
            422,
            "full scale",
        ),
        (
            VideoEncodeError("ffmpeg accepted no frame for 120.0s and was killed"),
            500,
            "could not be encoded",
        ),
        (BrokenPipeError("broken pipe"), 500, "could not be encoded"),
        (
            pe.PlateMismatch("refusing to store 12/wave-1/P8.mp4"),
            500,
            "could not be stored",
        ),
    ],
)
def test_every_encoder_failure_gets_a_status_rather_than_a_bare_500(
    monkeypatch, failure, status, detail
):
    """Each of these reached the caller as an unexplained 500.

    The worst was VideoEncodeError: it is what the stall watchdog raises, so
    the likeliest genuine failure arrived after two minutes as
    {"detail": "Internal Server Error"} with its own explanation discarded.
    """

    def fails(*args, **kwargs):
        raise failure

    monkeypatch.setattr(pr, "render_plate_video", fails)

    with pytest.raises(HTTPException) as ei:
        pr.render(12, {"plate_id": "P7", "wave_number": 1})

    assert ei.value.status_code == status
    assert detail in ei.value.detail


def test_an_encoder_failure_is_logged_with_its_real_reason(monkeypatch, caplog):
    """The detail is generic, so the log is the only place the reason survives —
    a caller cannot be handed ffmpeg's stderr, which carries internal paths."""

    def fails(*args, **kwargs):
        raise VideoEncodeError("ffmpeg exited 1: No space left on device")

    monkeypatch.setattr(pr, "render_plate_video", fails)

    with caplog.at_level("ERROR"):
        with pytest.raises(HTTPException) as ei:
            pr.render(12, {"plate_id": "P7", "wave_number": 1})

    assert "No space left on device" in caplog.text, "the reason reached nobody"
    assert "No space left" not in ei.value.detail, "ffmpeg's output reached the caller"


def test_an_unsupported_depth_is_not_reported_as_an_upstream_failure(monkeypatch):
    """FrameDepthUnsupported subclasses FrameUnreadable, so without its own
    branch it lands on 502 — asserting a gateway failure that did not happen,
    and sending someone to rescan a plate whose file is intact."""

    def fails(*args, **kwargs):
        raise pe.FrameDepthUnsupported("12/wave-1/P7_37.tif: a I frame peaks at 2**30")

    monkeypatch.setattr(pr, "render_plate_video", fails)

    with pytest.raises(HTTPException) as ei:
        pr.render(12, {"plate_id": "P7", "wave_number": 1})

    assert ei.value.status_code != 502, "an intact file was blamed on an upstream failure"
    assert ei.value.status_code == 422
