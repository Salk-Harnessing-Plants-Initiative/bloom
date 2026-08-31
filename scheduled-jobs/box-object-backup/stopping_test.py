"""Tests for the stop-on-request path.

Deliberately stronger than the equivalent tests in
`services/workflows/tests/test_dispatch_worker.py`, which call the handler
directly (`worker._stop(15, None)`). That proves the flag works but nothing
about whether the handler was ever installed — deleting the `signal.signal`
calls leaves those tests green. The cases below send real signals to real
processes, so the wiring is covered as well as the logic.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import stopping  # noqa: E402

HERE = str(Path(__file__).parent)


@pytest.fixture(autouse=True)
def clean_flag():
    """The flag is module state and the suite shares one process."""
    stopping.reset()
    yield
    stopping.reset()


def run_child(body: str, send: int, timeout: int = 15) -> subprocess.CompletedProcess:
    """Run a child that installs the handlers, then signal it for real."""
    # The body is dedented on its own rather than as part of the template:
    # callers indent it to suit their own nesting, and mixing those two levels
    # produces a script that will not parse.
    preamble = textwrap.dedent(
        f"""
        import os, signal, sys, time
        sys.path.insert(0, {HERE!r})
        import stopping
        stopping.install_handlers()
        print("ready", flush=True)
        """
    )
    script = preamble + textwrap.dedent(body)
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    assert proc.stdout is not None
    assert proc.stdout.readline().strip() == "ready"
    os.kill(proc.pid, send)
    out, err = proc.communicate(timeout=timeout)
    return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)


class TestTheFlag:
    def test_nothing_is_stopping_to_begin_with(self):
        assert stopping.stopping() is False

    def test_the_handler_sets_it(self):
        stopping._request_stop(signal.SIGTERM, None)
        assert stopping.stopping() is True

    def test_reset_clears_it(self):
        stopping._request_stop(signal.SIGTERM, None)
        stopping.reset()
        assert stopping.stopping() is False

    def test_a_second_signal_is_harmless(self):
        stopping._request_stop(signal.SIGTERM, None)
        stopping._request_stop(signal.SIGINT, None)
        assert stopping.stopping() is True


class TestRealSignals:
    """Signals actually delivered by the OS, to a process that installed the
    handlers the way the job does."""

    BODY = """
        for _ in range(200):
            if stopping.stopping():
                print("stopped", flush=True)
                raise SystemExit(3)
            time.sleep(0.02)
        print("never-noticed", flush=True)
        raise SystemExit(99)
        """

    @pytest.mark.parametrize(
        "signame", ["SIGTERM", "SIGINT", "SIGHUP"],
    )
    def test_the_process_stops_and_exits_three(self, signame):
        result = run_child(self.BODY, getattr(signal, signame))
        assert "stopped" in result.stdout, f"{signame} was not noticed"
        assert result.returncode == 3, f"{signame} gave exit {result.returncode}"

    def test_the_signal_does_not_kill_it_outright(self):
        """The point of the whole exercise.

        Without a handler, SIGTERM and SIGHUP terminate Python immediately —
        no `finally`, so the rclone container is left behind. The child here
        must reach its own exit, not be killed.
        """
        result = run_child(self.BODY, signal.SIGHUP)
        # A killed process reports a negative return code (-signum).
        assert result.returncode >= 0, "the process was killed, not asked to stop"

    def test_work_in_flight_finishes_before_it_exits(self):
        body = """
            import stopping
            done = []
            for i in range(200):
                if stopping.stopping():
                    break
                time.sleep(0.02)
                done.append(i)          # "finishing the object in flight"
            print("completed:%d" % len(done), flush=True)
            raise SystemExit(3)
            """
        result = run_child(body, signal.SIGTERM)
        completed = int(result.stdout.split("completed:")[1].split()[0])
        assert completed >= 1, "stopped without finishing anything"
        assert completed < 200, "ignored the stop and ran to the end"


class TestHandlersAreActuallyInstalled:
    """Guards the wiring, which the pattern this copies does not cover."""

    def test_every_stop_signal_has_a_handler_after_install(self):
        previous = {s: signal.getsignal(s) for s in stopping.STOP_SIGNALS}
        try:
            stopping.install_handlers()
            for signum in stopping.STOP_SIGNALS:
                assert signal.getsignal(signum) is stopping._request_stop, (
                    f"{signum!r} left on its default action"
                )
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)

    def test_sighup_is_handled(self):
        # Called out because it is the one that catches people: a dropped SSH
        # connection sends SIGHUP, and Python's default action for it is to die
        # without unwinding — exactly the leak this exists to prevent.
        assert signal.SIGHUP in stopping.STOP_SIGNALS

    def test_the_usual_two_are_handled_as_well(self):
        assert signal.SIGTERM in stopping.STOP_SIGNALS
        assert signal.SIGINT in stopping.STOP_SIGNALS
