"""Unit tests for the video queues' wrappers (a recording RPC stub — no DB),
matching test_dispatch_worker.py's convention."""

import pytest

import video_queue as vq
from video_writer import ENCODE_TIMEOUT_SECONDS


class _Rpc:
    def __init__(self, data):
        self._data = data

    def execute(self):
        return type("R", (), {"data": self._data})()


class FakeClient:
    """Answers each wrapper from `answers` and records every call."""

    def __init__(self, **answers):
        self.answers = answers
        self.calls = []

    def rpc(self, name, params):
        self.calls.append((name, params))
        answer = self.answers.get(name)
        if isinstance(answer, Exception):
            raise answer
        if callable(answer):
            answer = answer()
        return _Rpc(answer)

    def names(self):
        return [name for name, _ in self.calls]


JOB = {"job_id": "job-1", "msg_id": 7, "reads": 1, "scan_id": 5, "experiment_id": 1}
Q = vq.CYL_QUEUE


def _queued(*jobs):
    """A claim that hands out `jobs` in order, then nothing."""
    pending = list(jobs)
    return lambda: [pending.pop(0)] if pending else []


def _settled(**extra):
    return {Q.complete: True, Q.fail: True, Q.progress: True, **extra}


# --- constants ---------------------------------------------------------------


def test_the_visibility_timeout_outlives_the_encode_plus_the_upload():
    """Encoding reports nothing while it runs, so the timeout must outlast it."""
    assert (
        vq.VISIBILITY_TIMEOUT_SECONDS
        == int(ENCODE_TIMEOUT_SECONDS) + vq.UPLOAD_MARGIN_SECONDS
    )
    assert vq.VISIBILITY_TIMEOUT_SECONDS > ENCODE_TIMEOUT_SECONDS


def test_the_visibility_timeout_is_inside_what_the_wrappers_accept():
    """The claim and progress wrappers refuse anything past an hour."""
    assert 0 < vq.VISIBILITY_TIMEOUT_SECONDS <= 3600


@pytest.mark.parametrize("queue", [vq.PLATE_QUEUE, vq.CYL_QUEUE], ids=lambda q: q.name)
def test_each_queue_names_its_own_five_wrappers(queue):
    names = [queue.enqueue, queue.claim, queue.progress, queue.complete, queue.fail]
    assert len(set(names)) == 5
    kind = "gravi_plate_video" if queue is vq.PLATE_QUEUE else "cyl_video"
    assert all(kind in name for name in names)


# --- wrappers ----------------------------------------------------------------


def test_enqueue_answers_the_job_and_whether_it_was_new():
    client = FakeClient(**{Q.enqueue: [{"job_id": "job-1", "created": False}]})
    assert vq.enqueue(client, Q, {"p_scan_id": 5}) == {
        "job_id": "job-1",
        "created": False,
    }
    assert client.calls == [(Q.enqueue, {"p_scan_id": 5})]


def test_an_enqueue_that_returns_no_job_is_an_error():
    with pytest.raises(RuntimeError):
        vq.enqueue(FakeClient(**{Q.enqueue: []}), Q, {})


def test_claim_passes_the_visibility_timeout_and_delivery_limit():
    client = FakeClient(**{Q.claim: [JOB]})
    assert vq.claim(client, Q) == JOB
    assert client.calls == [
        (Q.claim, {"p_vt": vq.VISIBILITY_TIMEOUT_SECONDS, "p_max_reads": vq.MAX_READS})
    ]


def test_an_empty_claim_is_none():
    assert vq.claim(FakeClient(**{Q.claim: []}), Q) is None


def test_a_progress_report_renews_with_the_same_timeout_the_claim_took():
    """A shorter renewal would move visibility backwards and hand the job out again."""
    client = FakeClient(**{Q.progress: True})
    assert vq.report(client, Q, JOB, "encoding", 3, 9) is True
    assert client.calls == [
        (
            Q.progress,
            {
                "p_job_id": "job-1",
                "p_msg_id": 7,
                "p_stage": "encoding",
                "p_frames_done": 3,
                "p_frames_total": 9,
                "p_vt": vq.VISIBILITY_TIMEOUT_SECONDS,
            },
        )
    ]


def test_complete_and_fail_settle_the_claimed_message():
    client = FakeClient(**{Q.complete: True, Q.fail: True})
    vq.complete(client, Q, JOB, "kept")
    vq.fail(client, Q, JOB, "no_images", "No images found for scan 5")
    assert client.calls == [
        (Q.complete, {"p_job_id": "job-1", "p_msg_id": 7, "p_outcome": "kept"}),
        (
            Q.fail,
            {
                "p_job_id": "job-1",
                "p_msg_id": 7,
                "p_error_code": "no_images",
                "p_error": "No images found for scan 5",
            },
        ),
    ]
