"""The copy engine — turning a CopyPlan into objects on Box.

Split out from backup_objects.py so the retry, progress, and verification
behaviour can be tested against a fake daemon without going near argument
parsing, Docker, or the workflow that schedules it.
"""

from __future__ import annotations

import hashlib
import heapq
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import backup_lib as lib
import stopping
from ledger import Ledger
from rclone_rc import MinioSource, RcloneError, RcloneRC

logger = logging.getLogger("bloom_box_object_backup")

# Box throttles hard under a seed run, so a failed object is worth several
# tries before it counts against the run.
MAX_ATTEMPTS = 4
RETRY_BASE_SECONDS = 5

LEDGER_COMMIT_EVERY = 200
PROGRESS_EVERY = 500

# Ceiling on failure paths held in memory for the run report. Comfortably
# above what a report lists, so the cap is never the reason a report is short.
MAX_TRACKED_FAILURES = 5_000


def copy_all(
    client: RcloneRC,
    plan: lib.CopyPlan,
    minio: MinioSource,
    box_fs: str,
    box_root: str,
    ledger: Ledger,
    workers: int,
    failures: list[str] | None = None,
    succeeded: "VerifyReservoir | None" = None,
) -> tuple[int, int]:
    """Copy every planned object, N at a time, recording each success.

    `failures`, when given, collects the storage path of every object that
    failed, so the run report can name them rather than only count them.

    `succeeded`, when given, is offered every object that copied cleanly, so
    verification samples objects that actually landed. Sampling the *plan*
    instead re-reports failed objects as "missing on Box", double-counting
    errors already logged.
    """
    src_fs = minio.fs()
    lock = threading.Lock()
    state = {"copied": 0, "failed": 0, "bytes": 0}
    started = time.monotonic()

    def worker(obj: lib.StorageObject) -> None:
        # Checked before anything starts, never during a transfer. A stop skips
        # the objects still queued while those already in flight finish and are
        # recorded — so nothing is left half-copied, and a restart neither
        # repeats them nor misses them.
        if stopping.stopping():
            return
        dst = lib.box_path(obj, box_root)
        try:
            copy_one(client, src_fs, lib.source_remote(obj, minio.prefix), box_fs, dst, obj)
        except RcloneError as exc:
            logger.error("failed %s: %s", obj.storage_path, exc)
            with lock:
                state["failed"] += 1
                # Bounded: a bad night can fail millions of objects, and the
                # count is what matters once there are more than a report can
                # usefully list. Every failure is in the log regardless.
                if failures is not None and len(failures) < MAX_TRACKED_FAILURES:
                    failures.append(obj.storage_path)
            return
        # Recorded only after the copy returns, so an interrupted run never
        # claims an object it did not finish.
        ledger.mark_copied(obj)
        with lock:
            if succeeded is not None:
                succeeded.offer(obj)
            state["copied"] += 1
            state["bytes"] += obj.size or 0
            done = state["copied"]
            byte_count = state["bytes"]
        if done % LEDGER_COMMIT_EVERY == 0:
            ledger.commit()
        if done % PROGRESS_EVERY == 0:
            log_progress(done, len(plan.copies), byte_count, started)

    # One worker per object at most — no point spinning idle threads.
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(plan.copies)))) as pool:
        list(pool.map(worker, plan.copies))
    ledger.commit()
    return state["copied"], state["failed"]


class VerifyReservoir:
    """Bounded, uniform, order-independent sample of the objects a run copied.

    The pool cannot simply be the first N — a seed night copies half a million
    objects and the head of the run is not representative of it. Nor can it be
    everything: that is the memory the batching exists to avoid.

    Which objects are kept is decided by a hash of each object's path, keeping
    the `cap` smallest. That is a uniform sample, and — the part that matters —
    it does not depend on the order they were offered in. Reservoir sampling
    with a seeded RNG was uniform but not that: `offer` is called by whichever
    of the copy workers finishes first, so the order varies with Box's network
    timing and every run sampled different objects. It was only reproducible
    single-threaded, which is to say only in its tests.

    What this buys is a stable sample, not a reproducible finding. A mismatched
    object is recorded as copied before verification runs, so the next run
    skips it as `already_current`, never copies it, and never offers it here
    again — re-running does not re-check it. Forcing that means deleting its
    ledger row by hand, which the run's own error message explains.
    """

    def __init__(self, cap: int) -> None:
        self.cap = cap
        self.seen = 0
        # Entries are (-rank, path, arrival, obj), a max-heap by rank: the
        # worst-ranked entry sits on top and is the one evicted, so what
        # remains is the `cap` best. `arrival` only breaks ties, so the
        # comparison never reaches `obj`, which need not be orderable.
        self._heap: list[tuple[int, str, int, object]] = []

    @staticmethod
    def _rank(obj) -> tuple[int, str]:
        """Where this object falls in the sample, from its path alone.

        blake2b rather than hash(): the built-in is salted per process, so it
        would sample differently on every run — the exact property being fixed.
        """
        path = str(getattr(obj, "storage_path", obj))
        digest = hashlib.blake2b(path.encode("utf-8"), digest_size=8).digest()
        return int.from_bytes(digest, "big"), path

    def offer(self, obj) -> None:
        self.seen += 1
        rank, path = self._rank(obj)
        entry = (-rank, path, self.seen, obj)
        if len(self._heap) < self.cap:
            heapq.heappush(self._heap, entry)
        elif entry[0] > self._heap[0][0]:
            heapq.heapreplace(self._heap, entry)

    @property
    def items(self) -> list:
        """The sample, in a fixed order so what is checked is fixed too."""
        return [entry[3] for entry in sorted(self._heap, key=lambda e: (-e[0], e[1]))]

    def __len__(self) -> int:
        return len(self._heap)


def copy_one(
    client: RcloneRC,
    src_fs: str,
    src_remote: str,
    box_fs: str,
    dst_remote: str,
    obj: lib.StorageObject,
) -> None:
    """Copy one object, backing off on the throttling Box does under load."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            client.copy_file(src_fs, src_remote, box_fs, dst_remote)
            return
        except RcloneError as exc:
            if not exc.retryable or attempt == MAX_ATTEMPTS:
                raise
            delay = RETRY_BASE_SECONDS * (2 ** (attempt - 1))
            logger.warning(
                "retry %d/%d for %s in %ds: %s",
                attempt, MAX_ATTEMPTS - 1, obj.storage_path, delay, exc,
            )
            time.sleep(delay)


def log_progress(done: int, total: int, byte_count: int, started: float) -> None:
    elapsed = max(1e-6, time.monotonic() - started)
    rate = done / elapsed
    remaining = (total - done) / rate if rate else 0
    logger.info(
        "progress %d/%d (%s, %.1f obj/s, ~%.1fh left)",
        done, total, lib.format_bytes(byte_count), rate, remaining / 3600,
    )


def verify_sample(
    client: RcloneRC,
    plan: lib.CopyPlan,
    box_fs: str,
    box_root: str,
    sample_size: int,
) -> int:
    """Stat a spread of destination paths and compare sizes against Postgres.

    Strided rather than random, so the same pool is always checked in the same
    order. That does not make a mismatch reproducible across runs: the pool
    holds only what THIS run copied, and a mismatched object is already in the
    ledger, so later runs skip it. Returns the number of mismatches.
    """
    copies = plan.copies
    stride = max(1, len(copies) // max(1, sample_size))
    checked = mismatched = 0
    for obj in copies[::stride][:sample_size]:
        dst = lib.box_path(obj, box_root)
        try:
            item = client.stat(box_fs, dst)
        except RcloneError as exc:
            logger.error("verify: cannot stat %s: %s", dst, exc)
            mismatched += 1
            continue
        checked += 1
        if item is None:
            logger.error("verify: missing on Box: %s", dst)
            mismatched += 1
        elif obj.size is not None and item.get("Size") != obj.size:
            logger.error(
                "verify: size mismatch %s — Box %s, Postgres %s",
                dst, item.get("Size"), obj.size,
            )
            mismatched += 1
    logger.info("verify: %d checked, %d mismatched", checked, mismatched)
    return mismatched
