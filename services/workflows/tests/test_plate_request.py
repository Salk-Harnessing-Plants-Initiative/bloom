"""Validating a plate-video request and choosing a status for the answer.

No TestClient here — this is the layer between HTTP and the render, so it is
tested directly. `tests/test_main.py` covers the wiring.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

import plate_request as pr
from plate_encode import EncoderBusy, FrameUnreadable, NotRecorded


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


def test_an_unreadable_frame_names_it_in_the_response(monkeypatch):
    """"A frame failed" sends someone to the scanner. Naming it sends them to it."""
    def unreadable(*a, **k):
        raise FrameUnreadable("could not download 12/wave-1/P7_40.tif: connection reset")

    monkeypatch.setattr(pr, "render_plate_video", unreadable)
    with pytest.raises(HTTPException) as ei:
        pr.render(12, {"plate_id": "P7", "wave_number": 1})

    assert ei.value.status_code == 502
    assert "P7_40.tif" in ei.value.detail


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
