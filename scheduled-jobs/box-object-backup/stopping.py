"""Stop a run on request, after the object in flight, leaving it resumable.

The seed moves eight million objects over several nights, and during those
nights the backup and a deploy want the same machine. Stopping had exactly one
safe route — Ctrl-C at an attached terminal — because `daemon.stop()` sits in a
`finally` and `finally` does not run when a process is killed. Any other way of
stopping left the rclone container behind, holding the RC port, and the next
run refused to start until someone removed it by hand.

So a signal now asks the run to stop rather than killing it. The object already
being copied finishes and is recorded, nothing new is started, and the run
returns through its normal path — which removes the container, commits the
ledger and writes the report. Restarting then carries on from where it stopped,
because the ledger already knows what was copied.

This is the shape `services/workflows/dispatch_worker.py` and `status_poller.py`
use: a flag the handler flips, read at a loop boundary, never an interrupt of
work in progress. SIGHUP is handled as well as SIGTERM and SIGINT because a
dropped SSH connection sends it, and Python's default action for SIGHUP is to
die without unwinding — which is the leak this exists to prevent.
"""

from __future__ import annotations

import logging
import signal
import threading

logger = logging.getLogger("bloom_box_object_backup")

# The signals that mean "stop", and where each comes from:
#   SIGINT   Ctrl-C at a terminal, and GitHub's first cancellation signal
#   SIGTERM  `kill`, a reboot, a systemd stop, an Actions timeout
#   SIGHUP   the SSH connection dropping, which is how a cancelled workflow
#            reaches a run started over SSH
STOP_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)

_stop_requested = threading.Event()


def install_handlers() -> None:
    """Ask for a tidy stop on the signals that mean stop.

    Call once, early, from the process entry point. Only the main thread can
    install handlers, so this must not be called from a worker.
    """
    for signum in STOP_SIGNALS:
        signal.signal(signum, _request_stop)


def _request_stop(signum: int, _frame: object) -> None:
    """Flip the flag. Deliberately does nothing else.

    A handler runs between bytecodes and can interrupt anything, so it must not
    touch the ledger, the rclone client or the daemon. Everything real happens
    on the normal path once a loop notices.
    """
    if _stop_requested.is_set():
        # A second signal is someone with less patience. Say what is happening
        # rather than appearing hung; the copy in flight still finishes.
        logger.warning(
            "stop already requested — finishing the object in flight (signal %s)",
            signum,
        )
        return
    _stop_requested.set()
    logger.warning(
        "signal %s received — stopping after the object in flight. Progress is "
        "in the ledger; run again to carry on from here.",
        signum,
    )


def stopping() -> bool:
    """True once a stop has been asked for. Safe to call from any thread."""
    return _stop_requested.is_set()


def reset() -> None:
    """Clear the flag. For tests, which share a process across cases."""
    _stop_requested.clear()
