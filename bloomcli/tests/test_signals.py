"""Stop signals as Ctrl-C while a command writes, and what is left as it was."""

import os
import signal

import pytest

from bloomctl._signals import stop_signals_interrupt


def test_a_stop_signal_inside_is_ctrl_c_and_the_handler_is_put_back():
    before = signal.getsignal(signal.SIGTERM)
    with pytest.raises(KeyboardInterrupt), stop_signals_interrupt():
        os.kill(os.getpid(), signal.SIGTERM)
    assert signal.getsignal(signal.SIGTERM) is before


def test_an_ignored_signal_stays_ignored_as_under_nohup():
    previous = signal.signal(signal.SIGHUP, signal.SIG_IGN)
    try:
        with stop_signals_interrupt():
            assert signal.getsignal(signal.SIGHUP) is signal.SIG_IGN
            os.kill(os.getpid(), signal.SIGHUP)
        assert signal.getsignal(signal.SIGHUP) is signal.SIG_IGN
    finally:
        signal.signal(signal.SIGHUP, previous)
