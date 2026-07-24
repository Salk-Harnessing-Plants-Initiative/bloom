"""Unit tests for the shared per-key mutual exclusion (`_locks.KeyedLock`).

Covers what `test_store_parity.py`'s higher-level concurrent-commit test
doesn't isolate on its own: that the registry never accumulates entries for
keys nobody is using anymore (the bounding fix), and that a still-in-flight
key's entry survives eviction pressure from other keys (the correctness
property the bounding fix must not break).
"""

from __future__ import annotations

import threading
import time

from bloom_mcp.result_store._locks import KeyedLock


def test_sequential_use_leaves_no_registry_entry():
    with KeyedLock("k1"):
        pass
    assert "k1" not in KeyedLock._entries


def test_many_distinct_keys_do_not_accumulate():
    for i in range(500):
        with KeyedLock(("bulk", i)):
            pass
    assert not any(k[0] == "bulk" for k in KeyedLock._entries if isinstance(k, tuple))


def test_concurrent_use_of_same_key_is_mutually_exclusive():
    key = "shared-exclusive"
    order: list[str] = []
    start = threading.Barrier(2)

    def _worker(label: str, hold_seconds: float) -> None:
        start.wait()
        with KeyedLock(key):
            order.append(f"{label}-enter")
            time.sleep(hold_seconds)
            order.append(f"{label}-exit")

    t1 = threading.Thread(target=_worker, args=("A", 0.05))
    t2 = threading.Thread(target=_worker, args=("B", 0.0))
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    # Whichever thread entered first must also exit before the other enters
    # -- an interleaved enter/enter/exit/exit order would prove they ran
    # concurrently, which the lock exists to prevent.
    first = order[0].split("-")[0]
    assert order[:2] == [f"{first}-enter", f"{first}-exit"]
    assert len(order) == 4
    assert key not in KeyedLock._entries


def test_entry_survives_while_a_second_caller_is_registered_on_it():
    key = "held-with-waiter"
    acquired_first = threading.Event()
    release_first = threading.Event()
    entered_second = threading.Event()

    def _holder() -> None:
        with KeyedLock(key):
            acquired_first.set()
            release_first.wait(timeout=5)

    def _waiter() -> None:
        with KeyedLock(key):
            entered_second.set()

    holder = threading.Thread(target=_holder)
    holder.start()
    assert acquired_first.wait(timeout=5)

    waiter = threading.Thread(target=_waiter)
    waiter.start()
    time.sleep(0.05)  # let the waiter register itself and block on the lock

    # The waiter has incremented the refcount and is blocked on the
    # underlying lock -- the entry must not have been evicted out from
    # under it despite the holder not being done yet.
    assert key in KeyedLock._entries

    release_first.set()
    holder.join(timeout=5)
    waiter.join(timeout=5)

    assert entered_second.is_set()
    assert key not in KeyedLock._entries


def test_exception_inside_the_block_still_releases_and_evicts():
    key = "raises"
    try:
        with KeyedLock(key):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert key not in KeyedLock._entries
    # A fresh acquire must succeed promptly -- proves the lock was released.
    with KeyedLock(key):
        pass
