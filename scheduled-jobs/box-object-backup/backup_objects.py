#!/usr/bin/env python3
"""Mirror Supabase Storage objects to Box under their Storage API paths.

MinIO holds every object at `bloom-storage/<bucket>/<name>/<version-uuid>`,
so a straight bucket-to-Box copy produces files whose last path segment is a
UUID with no extension — Box cannot preview them and a human cannot find
anything. This job reads `storage.objects`, which knows the logical name and
its current version, and copies each object to `<bucket>/<name>` on Box. The
result is browsable: `images/<experiment>/<plate>/<frame>.png` previews in
the Box web UI.

Companion to the Postgres dump, not a replacement: the dump carries the
`storage.objects` rows (including `version`), and this job carries the
bytes. Restoring means writing each file back to MinIO under the key the
restored row names — see the wiki page for the procedure.

Exit codes:
  0 = every planned object copied (or dry run completed)
  1 = one or more objects failed after retries
  2 = configuration or preflight error
  3 = interrupted; progress is in the ledger and the next run resumes
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

sys.path.insert(0, str(Path(__file__).parent))

import backup_lib as lib  # noqa: E402
import docker_env as dock  # noqa: E402
import report  # noqa: E402
from copier import MAX_ATTEMPTS, copy_all, verify_sample  # noqa: E402
from ledger import Ledger  # noqa: E402
from rclone_rc import MinioSource, RcloneError, RcloneRC  # noqa: E402
from runlock import SKIP_MARKER, LockHeld, RunLock  # noqa: E402

logger = logging.getLogger("bloom_box_object_backup")

DEFAULT_STATE_DIR = "/var/lib/bloom-box-object-backup"
DEFAULT_WORKERS = 8

# Objects planned per pass. Big enough that the per-batch ledger lookup is
# amortized, small enough that a seed run's memory stays flat.
BATCH_SIZE = 20_000

# Verification samples from the objects this run copied; cap what we retain
# so a multi-million-object seed doesn't hold them all to check 50.
VERIFY_POOL_CAP = 5_000


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        return run_backup(args)
    except (dock.DockerError, lib.BackupError) as exc:
        logger.error("%s", exc)
        return 2
    except KeyboardInterrupt:
        logger.warning("interrupted — ledger holds progress; re-run to resume")
        return 3


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--env", required=True, choices=("staging", "prod"))
    parser.add_argument(
        "--buckets",
        default="",
        help="comma-separated bucket allow-list (default: every bucket except tus-files)",
    )
    parser.add_argument(
        "--box-root",
        default=os.environ.get("BACKUP_BOX_ROOT", ""),
        help="path under the Box remote to mirror into, e.g. Bloom-Backups/prod",
    )
    parser.add_argument(
        "--box-remote",
        default=os.environ.get("BACKUP_BOX_REMOTE", "box"),
        help="name of the configured rclone Box remote (default: box)",
    )
    parser.add_argument(
        "--rclone-config",
        default=os.environ.get(
            "BACKUP_RCLONE_CONFIG", str(Path.home() / ".config/rclone/rclone.conf")
        ),
    )
    parser.add_argument("--state-dir", default=os.environ.get("BACKUP_STATE_DIR", DEFAULT_STATE_DIR))
    parser.add_argument("--workers", type=int, default=int(os.environ.get("BACKUP_WORKERS", DEFAULT_WORKERS)))
    parser.add_argument("--rc-port", type=int, default=int(os.environ.get("BACKUP_RC_PORT", 5572)))
    parser.add_argument("--bwlimit", default=os.environ.get("BACKUP_BWLIMIT", ""))
    parser.add_argument(
        "--limit", type=int, default=None, help="copy at most N objects, then stop (for smoke tests)"
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="enumerate every object instead of only those changed since the last clean run",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="plan and report, copy nothing; still contacts Postgres",
    )
    parser.add_argument(
        "--verify",
        type=int,
        default=0,
        metavar="N",
        help="after copying, stat N destination paths (evenly spread) and compare sizes",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def run_backup(args: argparse.Namespace) -> int:
    state_dir = Path(args.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)

    # Taken before anything reads the ledger. A multi-day seed and the weekly
    # run share one SQLite file, and two writers corrupt each other's progress.
    try:
        lock = RunLock(state_dir).acquire()
    except LockHeld as held:
        logger.warning("%s", SKIP_MARKER)
        logger.warning("held by %s", held.holder.describe())
        return 0
    try:
        return run_locked(args, state_dir)
    finally:
        lock.release()


def run_locked(args: argparse.Namespace, state_dir: Path) -> int:
    project = dock.project_name(args.env)
    box_fs = f"{args.box_remote}:"

    ledger = Ledger.open(str(state_dir / "ledger.db"))

    db_container = dock.find_container(project, dock.DB_SERVICE)
    since = None if args.full else ledger.last_successful_run()
    logger.info(
        "enumerating storage.objects for %s (%s)",
        args.env,
        f"changed since {since}" if since else "full listing",
    )
    manifest = state_dir / "manifest.tsv"
    listed = dock.psql_query_to_file(
        db_container,
        lib.objects_query(
            buckets=[b.strip() for b in args.buckets.split(",") if b.strip()] or None,
            since=since,
        ),
        user=os.environ.get("POSTGRES_USER", "supabase_admin"),
        database=os.environ.get("POSTGRES_DB", "postgres"),
        destination=manifest,
    )
    logger.info("listed %d object(s)", listed)

    if args.dry_run:
        report_dry_run(manifest, ledger, args.limit)
        ledger.close()
        return 0

    minio = minio_source_from_env()
    require_rclone_config(args.rclone_config, args.box_remote)
    run_id = ledger.start_run()
    network = dock.find_network(project)
    daemon = dock.start_rc_daemon(
        network=network,
        rclone_config=str(Path(args.rclone_config).resolve()),
        port=args.rc_port,
        transfers=args.workers,
        bwlimit=args.bwlimit,
        state_dir=str(state_dir.resolve()),
    )
    totals = Totals()
    started_at = datetime.now(timezone.utc)
    crashed = False
    try:
        client = wait_for_daemon(daemon)
        copy_manifest(client, manifest, ledger, minio, box_fs, args, totals)
        if args.verify and totals.verify_pool:
            verify_sample(
                client,
                lib.CopyPlan(tuple(totals.verify_pool), (), 0),
                box_fs,
                args.box_root,
                args.verify,
            )
    except BaseException:
        # Recorded before re-raising so the Box report still names the run
        # that died — a failed run is the one most worth a record.
        crashed = True
        raise
    finally:
        stats = {
            "listed": listed,
            "copied": totals.copied,
            "failed": totals.failed,
            "skipped": totals.skipped,
            "already_current": totals.already_current,
        }
        outcome = run_outcome(
            crashed=crashed,
            failed=totals.failed,
            copied=totals.copied,
            limit=args.limit,
        )
        publish_report(
            daemon, state_dir, box_fs, args,
            run_id=run_id, started_at=started_at, outcome=outcome,
            stats=stats, failures=totals.failures,
        )
        daemon.stop()
        ledger.commit()

    ledger.finish_run(run_id, outcome, stats)
    ledger.close()
    logger.info(
        "done — copied %d, failed %d, already current %d, skipped %d",
        totals.copied, totals.failed, totals.already_current, totals.skipped,
    )
    if totals.failed:
        logger.error(
            "%d object(s) failed after %d attempts each — re-run to retry them",
            totals.failed, MAX_ATTEMPTS,
        )
        return 1
    return 0


@dataclass
class Totals:
    """Run-wide counters, accumulated across manifest batches."""

    copied: int = 0
    failed: int = 0
    skipped: int = 0
    already_current: int = 0
    verify_pool: list = field(default_factory=list)
    failures: list = field(default_factory=list)


def run_outcome(*, crashed: bool, failed: int, copied: int, limit: int | None) -> str:
    """Classify a finished run — and decide whether it can be a watermark.

    `Ledger.last_successful_run()` only considers runs recorded `ok`, so this
    is what stops a chunked seed from losing objects. A run cut short by
    `--limit` has not seen the whole table; recording it clean would make the
    next run filter on its start time and skip everything the limit left
    behind, permanently and without saying so.
    """
    if crashed:
        return "error"
    truncated = limit is not None and copied >= limit
    if failed or truncated:
        return "partial"
    return "ok"


def publish_report(
    daemon: dock.RcDaemon,
    state_dir: Path,
    box_fs: str,
    args: argparse.Namespace,
    *,
    run_id: int,
    started_at: "datetime",
    outcome: str,
    stats: dict,
    failures: list,
) -> None:
    """Write the run report locally, then copy it to Box beside the mirror.

    Best-effort by design: the objects are already on Box, and losing the
    report must not turn a good run into a failed one. It is logged loudly
    instead, and the local copy under the state dir survives either way.
    """
    entry = report.RunReport(
        env=args.env,
        run_id=run_id,
        started_at=started_at,
        finished_at=datetime.now(timezone.utc),
        outcome=outcome,
        box_root=args.box_root,
        stats=stats,
        failures=failures,
    )
    try:
        local = report.write_local(entry, state_dir)
    except OSError as exc:
        logger.error("could not write the run report locally: %s", exc)
        return
    try:
        client = RcloneRC(daemon.url, daemon.user, daemon.password)
        client.copy_file(
            dock.STATE_MOUNT + "/" + report.REPORTS_DIRNAME,
            entry.filename(),
            box_fs,
            report.box_remote_path(entry),
        )
        logger.info("run report on Box: %s", report.box_remote_path(entry))
    except RcloneError as exc:
        logger.error(
            "run report stayed local at %s — upload failed: %s", local, exc
        )


def plan_batches(
    manifest: Path, ledger: Ledger, limit: int | None
) -> "Iterator[lib.CopyPlan]":
    """Walk the manifest in batches, planning each against the ledger.

    Batching is what keeps memory flat on a seed run: one batch of objects
    and one indexed ledger lookup at a time, never the whole table.
    """
    remaining = limit
    with manifest.open(encoding="utf-8") as handle:
        for batch in lib.batches(lib.iter_manifest(handle), BATCH_SIZE):
            if remaining is not None and remaining <= 0:
                return
            copied = ledger.versions_for([obj.ledger_key for obj in batch])
            plan = lib.build_plan(batch, copied, limit=remaining)
            if remaining is not None:
                remaining -= len(plan.copies)
            yield plan


def report_dry_run(manifest: Path, ledger: Ledger, limit: int | None) -> None:
    totals = Totals()
    for plan in plan_batches(manifest, ledger, limit):
        totals.copied += len(plan.copies)
        totals.skipped += len(plan.skipped)
        totals.already_current += plan.already_current
        report_skips(plan)
    logger.info(
        "dry run — would copy %d, %d already current, %d skipped; nothing was copied",
        totals.copied, totals.already_current, totals.skipped,
    )


def copy_manifest(
    client: RcloneRC,
    manifest: Path,
    ledger: Ledger,
    minio: MinioSource,
    box_fs: str,
    args: argparse.Namespace,
    totals: Totals,
) -> None:
    for plan in plan_batches(manifest, ledger, args.limit):
        report_skips(plan)
        totals.skipped += len(plan.skipped)
        totals.already_current += plan.already_current
        if not plan.copies:
            continue
        logger.info(
            "batch: %d object(s), %s", len(plan.copies), lib.format_bytes(plan.total_bytes)
        )
        copied, failed = copy_all(
            client, plan, minio, box_fs, args.box_root, ledger, args.workers,
            failures=totals.failures,
        )
        totals.copied += copied
        totals.failed += failed
        if args.verify and len(totals.verify_pool) < VERIFY_POOL_CAP:
            totals.verify_pool.extend(plan.copies[: args.verify])


def minio_source_from_env() -> MinioSource:
    access = os.environ.get("MINIO_ROOT_USER", "")
    secret = os.environ.get("MINIO_ROOT_PASSWORD", "")
    if not access or not secret:
        raise lib.BackupError(
            "MINIO_ROOT_USER / MINIO_ROOT_PASSWORD missing — the service reads "
            "them from the deploy's .env file; export them for a manual run"
        )
    return MinioSource(
        endpoint=os.environ.get("BACKUP_MINIO_ENDPOINT", "http://supabase-minio:9000"),
        access_key=access,
        secret_key=secret,
    )


def require_rclone_config(path: str, remote: str) -> None:
    config = Path(path)
    if not config.is_file():
        raise lib.BackupError(
            f"rclone config not found at {config} — run `rclone config` to add "
            "the Box remote first (see the wiki page)"
        )
    text = config.read_text(errors="replace")
    if f"[{remote}]" not in text:
        raise lib.BackupError(
            f"rclone config {config} has no '[{remote}]' remote — "
            f"run `rclone config` and create it, or pass --box-remote"
        )


def wait_for_daemon(daemon: dock.RcDaemon, attempts: int = 30) -> RcloneRC:
    """Poll rc/noop until the daemon answers, so the first copy isn't a race."""
    client = RcloneRC(daemon.url, daemon.user, daemon.password)
    for attempt in range(attempts):
        try:
            client.noop()
            logger.info("rclone daemon ready (%s)", client.version())
            return client
        except RcloneError:
            time.sleep(0.5)
    raise lib.BackupError(
        "rclone daemon never became ready. Container logs:\n"
        + dock.daemon_logs(daemon.container)
    )


def report_skips(plan: lib.CopyPlan) -> None:
    """Name every object the plan refused to mirror, and why."""
    for skipped in plan.skipped:
        logger.warning("skipping %s: %s", skipped.obj.storage_path, skipped.reason)



if __name__ == "__main__":
    sys.exit(main())
