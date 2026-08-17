"""The copy engine — turning a CopyPlan into objects on Box.

Split out from backup_objects.py so the retry, progress, and verification
behaviour can be tested against a fake daemon without going near argument
parsing, Docker, or the systemd wrapper.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import backup_lib as lib
from ledger import Ledger
from rclone_rc import MinioSource, RcloneError, RcloneRC

logger = logging.getLogger("bloom_box_object_backup")

# Box throttles hard under a seed run, so a failed object is worth several
# tries before it counts against the run.
MAX_ATTEMPTS = 4
RETRY_BASE_SECONDS = 5

LEDGER_COMMIT_EVERY = 200
PROGRESS_EVERY = 500


def copy_all(
    client: RcloneRC,
    plan: lib.CopyPlan,
    minio: MinioSource,
    box_fs: str,
    box_root: str,
    ledger: Ledger,
    workers: int,
) -> tuple[int, int]:
    """Copy every planned object, N at a time, recording each success."""
    src_fs = minio.fs()
    lock = threading.Lock()
    state = {"copied": 0, "failed": 0, "bytes": 0}
    started = time.monotonic()

    def worker(obj: lib.StorageObject) -> None:
        dst = lib.box_path(obj, box_root)
        try:
            copy_one(client, src_fs, obj, box_fs, dst)
        except RcloneError as exc:
            logger.error("failed %s: %s", obj.storage_path, exc)
            with lock:
                state["failed"] += 1
            return
        # Recorded only after the copy returns, so an interrupted run never
        # claims an object it did not finish.
        ledger.mark_copied(obj)
        with lock:
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


def copy_one(
    client: RcloneRC,
    src_fs: str,
    obj: lib.StorageObject,
    box_fs: str,
    dst_remote: str,
) -> None:
    """Copy one object, backing off on the throttling Box does under load."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            client.copy_file(src_fs, obj.minio_key, box_fs, dst_remote)
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

    Sampled by stride rather than at random so a re-run checks the same
    objects — a mismatch stays reproducible instead of vanishing. Returns the
    number of mismatches.
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
