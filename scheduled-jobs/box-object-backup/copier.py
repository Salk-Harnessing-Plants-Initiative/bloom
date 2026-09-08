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
from dataclasses import dataclass

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

# Printed for an object Postgres lists that MinIO does not hold. Its own line
# and its own count, because it is not a failure that retrying can clear —
# nothing can copy bytes that are not there.
SOURCE_GONE_MARKER = "source object is gone:"


def copy_all(
    client: RcloneRC,
    plan: lib.CopyPlan,
    minio: MinioSource,
    box_fs: str,
    box_root: str,
    ledger: Ledger,
    workers: int,
    failures: list[str] | None = None,
    gone: list[str] | None = None,
    succeeded: "VerifyReservoir | None" = None,
) -> tuple[int, int, int]:
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
    state = {"copied": 0, "failed": 0, "bytes": 0, "gone": 0}
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
            # Ask MinIO whether the object is there at all before calling this
            # a failure. Postgres can hold a row whose bytes left MinIO long
            # ago — `preflight_source` names that case and tolerates it — and
            # such an object can never be copied by anything. Counted as a
            # failure it holds the watermark for ever: no run is ever recorded
            # clean, so every later night re-reads all eight million rows
            # inside a 240-minute job. That is the same cost this job already
            # refuses to pay for a filename Box cannot store, and it is the
            # same reason: nothing on this side can fix it.
            #
            # Established by asking, not by matching the error text. Guessing
            # from a message would eventually classify a real failure as a
            # missing object and advance the watermark past something that
            # should have been retried. One extra call, only on failure.
            if _source_is_gone(
                client, src_fs, lib.source_remote(obj, minio.prefix), exc
            ):
                logger.error(
                    "%s %s: it is in storage.objects but not in MinIO, so "
                    "nothing can copy it. NOT backed up.",
                    SOURCE_GONE_MARKER, lib.loggable(obj.storage_path),
                )
                with lock:
                    state["gone"] += 1
                    if gone is not None and len(gone) < MAX_TRACKED_FAILURES:
                        gone.append(obj.storage_path)
                return
            logger.error(
                "failed %s: %s",
                lib.loggable(obj.storage_path), lib.loggable(str(exc)),
            )
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
    return state["copied"], state["failed"], state["gone"]


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
    again — re-running does not re-check it. The run deliberately does not
    change the ledger to compensate: this is a backup, and nothing here
    removes a record of what is on Box. The report names the object; putting
    it back is a person's decision.
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


# What rclone and MinIO say when the key does not exist. Only a pre-filter —
# the stat below is what actually decides — so a phrase appearing in some
# unrelated error costs one extra call and nothing else.
MISSING_MARKERS = ("not found", "404", "no such", "nosuchkey")


def _source_is_gone(client, src_fs: str, src_remote: str, exc: Exception) -> bool:
    """Is the object genuinely absent from MinIO, or did the copy just fail?

    BOTH signals are required, because being wrong here is expensive in each
    direction. Call a real failure "gone" and the watermark advances past an
    object that should have been retried — the run says clean and the object
    is not on Box. Call a genuinely absent object a failure and the watermark
    never moves again.

    So the error has to say the key is missing AND a direct stat has to agree.
    Only a definite `None` counts: a stat that itself errors proves nothing,
    since the daemon may be the thing that is broken, and treating "I could
    not ask" as "it is not there" is the first mistake in a different form.
    """
    lowered = str(exc).lower()
    if not any(marker in lowered for marker in MISSING_MARKERS):
        return False
    try:
        return client.stat(src_fs, src_remote) is None
    except RcloneError:
        return False


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
                attempt, MAX_ATTEMPTS - 1, lib.loggable(obj.storage_path), delay,
                lib.loggable(str(exc)),
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


@dataclass(frozen=True)
class VerifyResult:
    """What a verification pass actually established.

    `unverified` is what it could not answer for: the stat call itself failed,
    so the object may be perfectly fine. Keeping that apart from `mismatched`
    is the whole point of this type. Counted together, one Box hiccup on an
    otherwise clean night produced VERIFICATION FAILED and sent an operator to
    the ledger for a healthy object — and taught the team to discount the only
    alarm a genuinely missing object has.

    `failures` names the objects behind `mismatched`, so the run report on Box
    can list them. A count alone left the identity of a missing object nowhere
    but a job log under retention, on the one alarm documented as never
    repeating, while the message claimed the paths were in the run report.

    Naming them is all it is for. The run does not act on them: it does not
    touch the ledger to force a re-copy, because this is a backup and nothing
    here removes a record of what is on Box. Restoring a missing object is a
    person's decision, and the report is what tells them which one.
    """

    checked: int
    mismatched: int
    unverified: int
    failures: tuple = ()


def verify_sample(
    client: RcloneRC,
    plan: lib.CopyPlan,
    box_fs: str,
    box_root: str,
    sample_size: int,
) -> VerifyResult:
    """Stat a spread of destination paths and compare sizes against Postgres.

    Strided rather than random, so the same pool is always checked in the same
    order. That does not make a mismatch reproducible across runs: the pool
    holds only what THIS run copied, and a mismatched object is already in the
    ledger, so later runs skip it.

    Only two findings are evidence against the backup: Box does not have the
    object, or has it at a different size. A failed stat is evidence of
    nothing.
    """
    copies = plan.copies
    stride = max(1, len(copies) // max(1, sample_size))
    checked = mismatched = unverified = 0
    failures = []
    for obj in copies[::stride][:sample_size]:
        dst = lib.box_path(obj, box_root)
        try:
            item = client.stat(box_fs, dst)
        except RcloneError as exc:
            # Not a mismatch: Box was asked and did not answer. Warning, not
            # error, so it cannot be read as a missing object.
            logger.warning(
                # The path is in the exception text too: rclone echoes the
                # remote it failed on. Escaping only the argument beside it
                # printed the same name raw on the same line.
                "verify: could not check %s: %s",
                lib.loggable(dst), lib.loggable(str(exc)),
            )
            unverified += 1
            continue
        checked += 1
        if item is None:
            logger.error("verify: missing on Box: %s", lib.loggable(dst))
            mismatched += 1
            failures.append(obj)
        elif obj.size is not None and item.get("Size") != obj.size:
            logger.error(
                "verify: size mismatch %s — Box %s, Postgres %s",
                lib.loggable(dst), item.get("Size"), obj.size,
            )
            mismatched += 1
            failures.append(obj)
    # The workflow summary extracts the leading "verify: N checked, N
    # mismatched"; anything appended here is outside that pattern.
    tail = f", {unverified} unverified" if unverified else ""
    logger.info("verify: %d checked, %d mismatched%s", checked, mismatched, tail)
    return VerifyResult(
        checked=checked, mismatched=mismatched, unverified=unverified,
        failures=tuple(failures),
    )
