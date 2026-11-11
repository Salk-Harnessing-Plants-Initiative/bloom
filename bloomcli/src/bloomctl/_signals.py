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
    """While inside, a stop signal is handled as Ctrl-C (KeyboardInterrupt)."""
    def interrupt(_signum, _frame):
        raise KeyboardInterrupt

    previous = {sig: signal.signal(sig, interrupt) for sig in STOP_SIGNALS}
    try:
        yield
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
