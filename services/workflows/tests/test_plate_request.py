"""Validating a plate-video request and queueing it.

No TestClient here — this is the layer between HTTP and the queue, so it is
tested directly. `tests/test_main.py` covers the wiring.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

import plate_request as pr
import plate_video as pv

USER = "6f1c2c8e-8d0b-4a57-9d8e-6f0b3c1a2b3c"


@pytest.fixture
def queued(monkeypatch):
    """Stand in for the enqueue wrapper and record what reached it."""
    calls = []

    def enqueue(client, queue, params):
        calls.append((queue.enqueue, params))
        return {"job_id": "job-1", "created": True}

    monkeypatch.setattr(pr, "app_client", lambda **kwargs: object())
    monkeypatch.setattr(pr, "enqueue_job", enqueue)
    return calls


def test_a_request_queues_the_plate_and_answers_with_the_job(queued):
    assert pr.enqueue(12, {"plate_id": "P7", "wave_number": 1}, USER) == {
        "job_id": "job-1",
        "created": True,
    }
    assert queued == [
        (
            "enqueue_gravi_plate_video",
            {
                "p_experiment_id": 12,
                "p_plate_id": "P7",
                "p_wave_number": 1,
                "p_requested_by": USER,
            },
        )
    ]


def test_joining_a_queued_job_is_not_an_error(monkeypatch, queued):
    monkeypatch.setattr(
        pr, "enqueue_job", lambda *a: {"job_id": "job-0", "created": False}
    )
    assert pr.enqueue(12, {"plate_id": "P7"}, USER) == {
        "job_id": "job-0",
        "created": False,
    }


@pytest.mark.parametrize("experiment_id", [1, pr.MAX_EXPERIMENT_ID])
def test_an_experiment_id_the_column_can_hold_is_accepted(queued, experiment_id):
    pr.enqueue(experiment_id, {"plate_id": "P7", "wave_number": 1}, USER)
    assert queued[0][1]["p_experiment_id"] == experiment_id


@pytest.mark.parametrize("experiment_id", [0, -1, 10**400])
def test_an_experiment_id_the_column_cannot_hold_is_refused(queued, experiment_id):
    with pytest.raises(HTTPException) as ei:
        pr.enqueue(experiment_id, {"plate_id": "P7", "wave_number": 1}, USER)
    assert ei.value.status_code == 400
    assert queued == []


def test_the_id_bounds_are_the_columns_they_stand_for():
    """Either could be widened to a 64-bit limit with the rest of the suite green,
    reintroducing the unexplained 500 from a Postgres INT overflow."""
    assert pr.MAX_WAVE_NUMBER == 2**31 - 1
    assert pr.MAX_EXPERIMENT_ID == 2**31 - 1


@pytest.mark.parametrize(
    "plate_id",
    ["../secrets", "a/b", "a\\b", ".hidden", "-x", "", "P" * 65, None, 7, "a b"],
)
def test_a_plate_id_that_cannot_become_an_object_key_is_refused(queued, plate_id):
    """Re-validated here rather than trusted from the Next proxy: Caddy
    publishes this service directly, so that hop is not a boundary."""
    with pytest.raises(HTTPException) as ei:
        pr.enqueue(12, {"plate_id": plate_id, "wave_number": 1}, USER)
    assert ei.value.status_code == 400
    assert queued == [], "a refused plate id reached the queue"


def test_the_plate_id_refusal_names_the_real_rule(queued):
    with pytest.raises(HTTPException) as ei:
        pr.enqueue(12, {"plate_id": "-x"}, USER)
    assert "must begin with a letter or digit" in ei.value.detail


def test_a_plate_with_no_wave_is_queued_with_none(queued):
    pr.enqueue(12, {"plate_id": "P7", "wave_number": None}, USER)
    assert queued[0][1]["p_wave_number"] is None


def test_a_missing_wave_key_is_the_same_as_a_null_one(queued):
    pr.enqueue(12, {"plate_id": "P7"}, USER)
    assert queued[0][1]["p_wave_number"] is None


def test_wave_zero_is_a_wave(queued):
    """The scanner app sends 0 when none is set, so it arrives in practice."""
    pr.enqueue(12, {"plate_id": "P7", "wave_number": 0}, USER)
    assert queued[0][1]["p_wave_number"] == 0


def test_the_largest_wave_the_column_holds_is_accepted(queued):
    pr.enqueue(12, {"plate_id": "P7", "wave_number": pr.MAX_WAVE_NUMBER}, USER)
    assert queued[0][1]["p_wave_number"] == pr.MAX_WAVE_NUMBER


@pytest.mark.parametrize("wave", [True, "1", 1.5, -1, pr.MAX_WAVE_NUMBER + 1])
def test_a_wave_that_is_not_a_whole_column_sized_number_is_refused(queued, wave):
    """`True` is an int subclass and would silently become wave 1."""
    with pytest.raises(HTTPException) as ei:
        pr.enqueue(12, {"plate_id": "P7", "wave_number": wave}, USER)
    assert ei.value.status_code == 400


def test_a_body_that_is_not_an_object_is_refused(queued):
    with pytest.raises(HTTPException) as ei:
        pr.enqueue(12, ["P7"], USER)
    assert ei.value.status_code == 400


def test_a_queue_failure_is_a_503_naming_no_internals(monkeypatch, queued, caplog):
    """The database's error names the role and PostgREST's codes; the log keeps it."""

    def fails(*a):
        raise RuntimeError("permission denied for role bloom_workflows at kong:8000")

    monkeypatch.setattr(pr, "enqueue_job", fails)

    with pytest.raises(HTTPException) as ei:
        pr.enqueue(12, {"plate_id": "P7"}, USER)

    assert ei.value.status_code == 503
    assert ei.value.detail == pr.NOT_QUEUED
    assert "bloom_workflows" in caplog.text


def test_a_sign_in_failure_is_the_same_503(monkeypatch, queued):
    def no_client(**kwargs):
        raise RuntimeError("auth down")

    monkeypatch.setattr(pr, "app_client", no_client)
    with pytest.raises(HTTPException) as ei:
        pr.enqueue(12, {"plate_id": "P7"}, USER)
    assert ei.value.status_code == 503


def test_every_code_the_planner_emits_has_a_status():
    """The render command reads these as exit codes, and an unmapped code falls
    to 409. Read out of the source so a new refusal added to plate_video without
    a status here fails rather than defaults."""
    import re
    from pathlib import Path

    source = Path(pv.__file__).read_text()
    # Both spellings: `_outcome(..., code="x")` and a `"code": "x"` literal.
    emitted = set(re.findall(r'"?code"?\s*[=:]\s*"([a-z_]+)"', source))

    assert emitted, "no codes found — the pattern this reads has changed"
    missing = emitted - set(pr.REFUSAL_STATUS)
    assert not missing, f"{sorted(missing)} would answer 409 Conflict"
