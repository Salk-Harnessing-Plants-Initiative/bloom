"""How many images bloommcp downloads and converts at once, and how long a call may wait."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Literal

# Images in progress across all calls; each holds an original and its pixels in memory.
MAX_IMAGE_SLOTS = 3
# The longest one image waits for a free slot.
SLOT_WAIT_SECONDS = 15
# No storage call or image starts this long after a call began; one already running
# finishes, so a call can run past this by one file's work.
CALL_SECONDS = 60

_slots = threading.BoundedSemaphore(MAX_IMAGE_SLOTS)


class NoSlot(Exception):
    def __init__(self, reason: Literal["busy", "time"]) -> None:
        super().__init__(reason)
        self.reason = reason


def deadline() -> float:
    return time.monotonic() + CALL_SECONDS


@contextmanager
def image_slot(call_deadline: float) -> Iterator[None]:
    """Holds one slot for one image's download and conversion; raises NoSlot."""
    remaining = call_deadline - time.monotonic()
    if remaining <= 0:
        raise NoSlot("time")
    if not _slots.acquire(timeout=min(SLOT_WAIT_SECONDS, remaining)):
        raise NoSlot("time" if time.monotonic() >= call_deadline else "busy")
    try:
        yield
    finally:
        _slots.release()
