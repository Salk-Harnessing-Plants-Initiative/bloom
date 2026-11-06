"""Fakes shared by the video queue, loop and watchdog tests (a recording RPC stub — no DB)."""

import video_queue as vq
import video_worker_loop as loop


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


def queued(*jobs):
    """A claim that hands out `jobs` in order, then nothing."""
    pending = list(jobs)
    return lambda: [pending.pop(0)] if pending else []


def settled(**extra):
    return {Q.complete: True, Q.fail: True, Q.progress: True, **extra}


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def make_reporter(clock, sent, answer=True):
    def send(stage, done, total):
        sent.append((stage, done, total))
        if isinstance(answer, Exception):
            raise answer
        return answer

    return loop.ProgressReporter(send, clock=clock, interval=1.0)


def wait_for(event, seconds=2.0):
    assert event.wait(seconds), "the watchdog never answered"
