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
import plate_video as pv
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


def test_a_wave_number_too_large_for_the_column_is_refused(monkeypatch):
    """`gravi_scans.wave_number` is a Postgres INT. Sent through, a bigger
    number makes the query error out and the caller gets an unexplained 500
    instead of being told the wave is not one."""
    monkeypatch.setattr(pr, "render_plate_video", _renders(_rendered()))
    with pytest.raises(HTTPException) as ei:
        pr.render(12, {"plate_id": "P7", "wave_number": 10**400})

    assert ei.value.status_code == 400
    assert "wave_number" in ei.value.detail


def test_the_largest_wave_the_column_holds_is_still_accepted(monkeypatch):
    """The bound is the column's, not a guess about how many waves a study has."""
    monkeypatch.setattr(pr, "render_plate_video", _renders(_rendered()))
    result = pr.render(12, {"plate_id": "P7", "wave_number": pr.MAX_WAVE_NUMBER})

    assert result["wave_number"] == pr.MAX_WAVE_NUMBER


def test_a_render_reports_the_frames_the_encoder_wrote(monkeypatch):
    """Not the number planned. They agree today — one unreadable frame fails
    the whole render — so a test that used a plan of the same length would pass
    whichever was reported."""
    outcome = _rendered(frames=[{}] * 9, recorded={"frame_count": 7})
    monkeypatch.setattr(pr, "render_plate_video", _renders(outcome))

    assert pr.render(12, {"plate_id": "P7", "wave_number": 1})["frames"] == 7


def test_a_keep_reports_the_frames_the_plate_has(monkeypatch):
    """Nothing was encoded, so there is no encoder count to prefer."""
    outcome = _rendered(action="keep", reason="already covers 4", frames=[{}] * 4)
    monkeypatch.setattr(pr, "render_plate_video", _renders(outcome))

    assert pr.render(12, {"plate_id": "P7", "wave_number": 1})["frames"] == 4


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


def test_a_recording_failure_names_the_video_without_naming_the_database(monkeypatch):
    """The message wraps the database client's own error.

    Measured on the real route before this: the caller received the SQLSTATE
    and "permission denied for function record_gravi_plate_video" — the schema
    and the role, to anyone with an account. The object key is the caller's own
    plate and is worth naming; the rest belongs in the log.
    """
    cause = "{'code':'42501','message':'permission denied for function record_gravi_plate_video'}"

    def unrecorded(*a, **k):
        raise NotRecorded(
            f"12/wave-1/P7.mp4 was stored but recording it failed: {cause}",
            "12/wave-1/P7.mp4",
        )

    monkeypatch.setattr(pr, "render_plate_video", unrecorded)
    with pytest.raises(HTTPException) as ei:
        pr.render(12, {"plate_id": "P7", "wave_number": 1})

    assert ei.value.status_code == 500
    assert "12/wave-1/P7.mp4" in ei.value.detail
    for leaked in ("42501", "permission denied", "record_gravi_plate_video"):
        assert leaked not in ei.value.detail, f"{leaked!r} reached the caller"


def test_a_recording_failure_still_reaches_the_log_in_full(monkeypatch, caplog):
    """The detail is now narrow, so the log is the only copy of the reason."""

    def unrecorded(*a, **k):
        raise NotRecorded(
            "12/wave-1/P7.mp4 was stored but recording it failed: permission denied",
            "12/wave-1/P7.mp4",
        )

    monkeypatch.setattr(pr, "render_plate_video", unrecorded)
    with caplog.at_level(logging.ERROR, logger=pr.logger.name):
        with pytest.raises(HTTPException):
            pr.render(12, {"plate_id": "P7", "wave_number": 1})

    assert "permission denied" in caplog.text, "the reason reached nobody"


# --------------------------------------------------------------------------
# The code contract, end to end
# --------------------------------------------------------------------------
#
# `plate_video` attaches a code to a refusal; this module maps it to a status.
# Both halves were tested, and neither read the other: the mapping test types
# the code strings as literals of its own, with the producer mocked. So
# renaming a literal on either side, or dropping the key from the outcome
# entirely, changed every refusal to 409 with nothing failing.

from test_plate_video import (  # noqa: E402
    _PlanClient,
    _big,
    _frames as _plan_frames,
    _outage,
    _recorded,
)


def _through_the_route(client, monkeypatch):
    """A real plan, all the way to the status a caller receives."""
    monkeypatch.setattr(pr, "app_client", lambda: client)
    with pytest.raises(HTTPException) as ei:
        pr.render(12, {"plate_id": "P7", "wave_number": 1})
    return ei.value


def test_a_plate_with_no_captures_is_a_404_through_the_real_plan(monkeypatch):
    """Not "typed the string 404 next to the string no_frames" — the refusal is
    produced by plate_video and the status read off what it produced."""
    failure = _through_the_route(_PlanClient(frames=[]), monkeypatch)

    assert failure.status_code == 404


def test_a_run_too_large_to_render_is_a_413_through_the_real_plan(monkeypatch):
    failure = _through_the_route(_PlanClient(frames=_big(200)), monkeypatch)

    assert failure.status_code == 413


def test_storage_that_did_not_answer_is_a_503_through_the_real_plan(monkeypatch):
    """Transient, and distinguishable from a plate that has nothing — one is
    worth retrying and the other never will be."""
    # A recorded row, so the object is probed at all — with nothing recorded
    # the answer is "absent" and storage is never asked.
    failure = _through_the_route(
        _PlanClient(
            frames=_plan_frames(3),
            row=_recorded(frames=3),
            raises=Exception("504 Gateway Timeout"),
        ),
        monkeypatch,
    )

    assert failure.status_code == 503


def test_a_database_that_did_not_answer_is_a_503_through_the_real_plan(monkeypatch):
    """The same answer storage gets. Both are a read that failed and neither
    changed anything, so a 500 would tell the caller to stop trying."""
    failure = _through_the_route(_outage("gravi_scans"), monkeypatch)

    assert failure.status_code == 503


def test_every_code_the_planner_emits_has_a_status(monkeypatch):
    """A refusal carrying an unmapped code falls to 409 Conflict, which says
    nothing true about any of these. Read out of the source so a new refusal
    added to plate_video without a status here fails rather than defaults.
    """
    import re
    from pathlib import Path

    source = Path(pv.__file__).read_text()
    emitted = set(re.findall(r'code[=:]\s*"([a-z_]+)"', source))

    assert emitted, "no codes found — the pattern this reads has changed"
    missing = emitted - set(pr._REFUSAL_STATUS)
    assert not missing, f"{sorted(missing)} would answer 409 Conflict"
