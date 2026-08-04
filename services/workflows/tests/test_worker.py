"""Unit tests for the queue worker loop (mocks the claim/generate/complete/fail
seam, so no ffmpeg, DB, or Supabase client is needed)."""

import pytest
from fastapi import HTTPException

import worker
import video_queue


_JOB = {"job_id": "j1", "scan_id": 5, "msg_id": 9, "experiment_id": 2}


def test_process_one_returns_false_on_empty_queue(monkeypatch):
    monkeypatch.setattr(worker, "claim_job", lambda c: None)
    assert worker.process_one(object()) is False


def test_process_one_generates_and_completes(monkeypatch):
    calls = {}
    monkeypatch.setattr(worker, "claim_job", lambda c: dict(_JOB))
    monkeypatch.setattr(
        worker,
        "generate_experiment_scan_video",
        lambda e, s, client=None: {"path": "cyl-videos/5.mp4", "frames": 10},
    )
    monkeypatch.setattr(
        worker, "complete_job", lambda c, j, m, p: calls.update(complete=(j, m, p))
    )
    monkeypatch.setattr(worker, "fail_job", lambda *a: calls.update(fail=a))

    assert worker.process_one(object()) is True
    assert calls["complete"] == ("j1", 9, "cyl-videos/5.mp4")
    assert "fail" not in calls


def test_process_one_uses_experiment_orchestration(monkeypatch):
    # 2.3: render via generate_experiment_scan_video (which records cyl_scan_videos), passing the
    # claim's experiment_id — not the low-level encoder that skips _record_video.
    captured = {}
    monkeypatch.setattr(worker, "claim_job", lambda c: {**_JOB, "experiment_id": 7})

    def _gen(experiment_id, scan_id, client=None):
        captured["args"] = (experiment_id, scan_id)
        return {"path": "cyl-videos/5.mp4", "frames": 10}

    monkeypatch.setattr(worker, "generate_experiment_scan_video", _gen)
    monkeypatch.setattr(worker, "complete_job", lambda *a: None)
    monkeypatch.setattr(worker, "fail_job", lambda *a: None)

    worker.process_one(object())
    assert captured["args"] == (7, 5)  # (experiment_id, scan_id)


def test_process_one_does_not_fail_after_completion_error(monkeypatch):
    # 2.2 orphan-job race: the render succeeded (video written); a lost/failed completion RPC must
    # NOT run fail_job — that would set an already-done job back to 'queued' and strand it forever.
    calls = {}
    monkeypatch.setattr(worker, "claim_job", lambda c: dict(_JOB))
    monkeypatch.setattr(
        worker,
        "generate_experiment_scan_video",
        lambda e, s, client=None: {"path": "cyl-videos/5.mp4", "frames": 10},
    )

    def _complete_boom(*a):
        raise RuntimeError("connection reset after commit")

    monkeypatch.setattr(worker, "complete_job", _complete_boom)
    monkeypatch.setattr(worker, "fail_job", lambda *a: calls.update(fail=a))

    assert worker.process_one(object()) is True
    assert "fail" not in calls  # a completed job is never failed/orphaned


def test_process_one_fails_on_encode_error(monkeypatch):
    calls = {}
    monkeypatch.setattr(worker, "claim_job", lambda c: dict(_JOB))

    def boom(_e, _s, client=None):
        raise HTTPException(status_code=500, detail="encode boom")

    monkeypatch.setattr(worker, "generate_experiment_scan_video", boom)
    monkeypatch.setattr(worker, "complete_job", lambda *a: calls.update(complete=a))
    monkeypatch.setattr(
        worker, "fail_job", lambda c, j, m, e: calls.update(fail=(j, m, e))
    )

    assert worker.process_one(object()) is True
    assert calls["fail"][0] == "j1" and calls["fail"][1] == 9  # job_id, msg_id
    assert calls["fail"][2] == "encode boom"  # HTTPException.detail preserved
    assert "complete" not in calls  # the bad video was never recorded


def test_process_one_sanitizes_raw_error(monkeypatch):
    # 2.9: a non-HTTPException (raw Storage/ffmpeg error) must not leak internal text into
    # cyl_video_jobs.error (a user-readable column).
    calls = {}
    monkeypatch.setattr(worker, "claim_job", lambda c: dict(_JOB))

    def boom(_e, _s, client=None):
        raise RuntimeError("/secret/path ffmpeg exploded at 0xdeadbeef")

    monkeypatch.setattr(worker, "generate_experiment_scan_video", boom)
    monkeypatch.setattr(worker, "fail_job", lambda c, j, m, e: calls.update(err=e))

    worker.process_one(object())
    assert (
        calls["err"] == "video generation failed (internal error)"
    )  # generic, not raw text
    assert "secret" not in calls["err"]


# --- enqueue helper (route side) --------------------------------------------


class _RpcResult:
    def __init__(self, data):
        self.data = data


class _EnqueueClient:
    def __init__(self, job_id="job-1"):
        self._job_id = job_id
        self.rpc_calls = []

    def rpc(self, name, params):
        self.rpc_calls.append((name, params))
        return self  # chainable

    def execute(self):
        return _RpcResult(self._job_id)


def test_enqueue_404_when_scan_not_in_experiment(monkeypatch):
    monkeypatch.setattr(video_queue, "app_client", lambda: _EnqueueClient())
    monkeypatch.setattr(video_queue, "scan_in_experiment", lambda c, e, s: False)
    with pytest.raises(HTTPException) as ei:
        video_queue.enqueue_experiment_scan_video(1, 5)
    assert ei.value.status_code == 404


def test_enqueue_returns_job_id(monkeypatch):
    client = _EnqueueClient(job_id="job-42")
    monkeypatch.setattr(video_queue, "app_client", lambda: client)
    monkeypatch.setattr(video_queue, "scan_in_experiment", lambda c, e, s: True)

    result = video_queue.enqueue_experiment_scan_video(1, 5)

    assert result == {"job_id": "job-42", "status": "queued"}
    assert client.rpc_calls[0][0] == "enqueue_cyl_video"
    assert client.rpc_calls[0][1] == {"p_scan_id": 5, "p_experiment_id": 1}


def test_enqueue_skips_when_video_exists(monkeypatch):
    # enqueue_cyl_video returns NULL when a video already exists for the scan → status "exists".
    client = _EnqueueClient(job_id=None)  # RPC returns NULL
    monkeypatch.setattr(video_queue, "app_client", lambda: client)
    monkeypatch.setattr(video_queue, "scan_in_experiment", lambda c, e, s: True)

    result = video_queue.enqueue_experiment_scan_video(1, 5)
    assert result == {"job_id": None, "status": "exists"}
