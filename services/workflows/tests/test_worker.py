"""Unit tests for the queue worker loop (mocks the claim/generate/complete/fail
seam, so no ffmpeg, DB, or Supabase client is needed)."""

import pytest
from fastapi import HTTPException

import worker
import video_queue


def test_process_one_returns_false_on_empty_queue(monkeypatch):
    monkeypatch.setattr(worker, "claim_job", lambda c: None)
    assert worker.process_one(object()) is False


def test_process_one_generates_and_completes(monkeypatch):
    calls = {}
    monkeypatch.setattr(worker, "claim_job", lambda c: {"job_id": "j1", "scan_id": 5, "msg_id": 9})
    monkeypatch.setattr(worker, "generate_scan_video", lambda c, s: {"path": "cyl-videos/5.mp4", "frames": 10})
    monkeypatch.setattr(worker, "complete_job", lambda c, j, m, p: calls.update(complete=(j, m, p)))
    monkeypatch.setattr(worker, "fail_job", lambda *a: calls.update(fail=a))

    assert worker.process_one(object()) is True
    assert calls["complete"] == ("j1", 9, "cyl-videos/5.mp4")
    assert "fail" not in calls


def test_process_one_fails_on_encode_error(monkeypatch):
    calls = {}
    monkeypatch.setattr(worker, "claim_job", lambda c: {"job_id": "j1", "scan_id": 5, "msg_id": 9})

    def boom(_c, _s):
        raise HTTPException(status_code=500, detail="encode boom")

    monkeypatch.setattr(worker, "generate_scan_video", boom)
    monkeypatch.setattr(worker, "complete_job", lambda *a: calls.update(complete=a))
    monkeypatch.setattr(worker, "fail_job", lambda c, j, m, e: calls.update(fail=(j, m, e)))

    assert worker.process_one(object()) is True
    assert calls["fail"][0] == "j1" and calls["fail"][1] == 9  # job_id, msg_id
    assert "complete" not in calls  # the bad video was never recorded


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
