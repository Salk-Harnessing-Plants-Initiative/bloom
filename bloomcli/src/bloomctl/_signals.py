"""Turning a stop signal into Ctrl-C while a command writes, so it can tidy up before exiting."""

from __future__ import annotations

import signal
from contextlib import contextmanager
from typing import Iterator

# What closing the terminal or terminating the process sends; a hard kill cannot be caught.
STOP_SIGNALS = tuple(
    getattr(signal, name) for name in ("SIGTERM", "SIGHUP") if hasattr(signal, name)
)


@contextmanager
def stop_signals_interrupt() -> Iterator[None]:
    """While inside, a stop signal is handled as Ctrl-C (KeyboardInterrupt). A signal already
    ignored stays ignored, so a run under `nohup` outlives the terminal."""
    def interrupt(_signum, _frame):
        raise KeyboardInterrupt

    previous = {sig: signal.signal(sig, interrupt) for sig in STOP_SIGNALS
                if signal.getsignal(sig) is not signal.SIG_IGN}
    try:
        yield
    finally:
        for sig, handler in previous.items():
            # None: a handler set outside Python, which can't be put back; the default is.
            signal.signal(sig, signal.SIG_DFL if handler is None else handler)
