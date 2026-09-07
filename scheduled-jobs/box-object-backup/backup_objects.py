#!/usr/bin/env python3
"""Mirror Supabase Storage objects to Box under their Storage API paths.

MinIO holds every object at
`bloom-storage/storage-single-tenant/<bucket>/<name>/<version-uuid>`,
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
  0 = every planned object copied (or dry run completed), or another run held
      the lock and this one stood down
  1 = one or more objects failed after retries
  2 = configuration or preflight error
  3 = interrupted; progress is in the ledger and the next run resumes
  4 = copying reported success but verification found objects missing from Box
  5 = one or more objects were refused because two names collide on one Box
      path; rename one of each pair in Supabase
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
from copier import (  # noqa: E402
    MAX_ATTEMPTS,
    MAX_TRACKED_FAILURES,
    VerifyReservoir,
    copy_all,
    verify_sample,
)
from ledger import Ledger  # noqa: E402
from rclone_rc import MinioSource, RcloneError, RcloneRC  # noqa: E402
import stopping  # noqa: E402
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

# Printed when the ledger changed and its Box copy did not, because the upload
# could not be made. The upload is best-effort by design — the objects are
# already on Box — so the run still exits 0 and the summary would otherwise read
# "succeeded" while the only copy of the resume record sits on the host this job
# exists to survive losing.
LEDGER_STALE_MARKER = "the Box copy of the ledger is STALE"

# The opposite situation, and it needs the opposite remedy, so it cannot share
# the marker above. Here the upload was REFUSED because Box holds the larger
# ledger: Box is the good copy and this host's is a stub — a rebuilt host, or a
# wiped state dir. Told to "fix" a stale Box copy, an operator would overwrite
# eight million rows with twenty and buy a three-week re-seed, which is the
# exact disaster the size guard exists to prevent.
LEDGER_AHEAD_MARKER = "the ledger on Box is AHEAD of this host"

# Printed when Box did not answer for every object the pass sampled. A failed
# stat is not evidence against the backup and must not fail the run — but a
# check that silently covered less than it reported, on a night reading
# "succeeded", is the no-op this pass exists to rule out. Any shortfall counts,
# not only a total blackout: 2 answers out of 50 is still a night whose
# "2 verified" headline claims far more than was established.
VERIFY_INCOMPLETE_MARKER = "verification did NOT cover its sample"

# Objects refused before any copy because Box cannot store the name. Same
# outcome as a refused collision — not backed up, and only a rename in
# Supabase fixes it — so it gets the same treatment: the run is `partial`, the
# object stays enumerated, and the summary says so. Deliberately NOT sharing
# the collision phrase ("were NOT backed up"), which the summary greps for and
# which carries collision-specific advice.
SKIPPED_NAME_MARKER = "object(s) were SKIPPED for their names"

# The summary used to decide what to print by searching the whole job log for
# English phrases. Object names are in that log — `report_skips` prints every
# refused one — and names are partly chosen by whoever uploads the file. So an
# image called `box-object-backup: SKIPPED.png` (the colon guarantees it is
# refused, hence logged) made a night with thousands of failed copies render
# as "skipped — this is expected until the seed ends". Every marker forged the
# same way, and it happens by accident too: a colon in a filename is ordinary.
#
# These two lines carry the verdict instead. Both are emitted only by
# `emit_status`, and the workflow matches them ANCHORED to the start of a log
# line — timestamp, level, then the key. An object name can only ever appear
# after a message has already begun, so no name can produce a matching line.
STATUS_KEY = "BOX_BACKUP_STATUS"
FLAGS_KEY = "BOX_BACKUP_FLAGS"

# The whole vocabulary. Anything else is a bug, not a new condition.
STATUS_VALUES = ("ok", "skipped", "stopped", "partial", "failed")
FLAG_VALUES = (
    "collisions",
    "skipped_names",
    "verify_mismatch",
    "verify_incomplete",
    "ledger_stale",
    "ledger_ahead",
)


def emit_status(status: str, flags=()) -> None:
    """Print the run's verdict in a form no object name can imitate.

    One line for the headline verdict and one for the independent conditions,
    both from a closed vocabulary, both anchored by the workflow. Flags are
    separate from the status because they are not alternatives: a night can
    succeed AND have left the Box ledger stale AND have refused a name.
    """
    if status not in STATUS_VALUES:
        raise ValueError(f"unknown run status: {status!r}")
    unknown = [f for f in flags if f not in FLAG_VALUES]
    if unknown:
        raise ValueError(f"unknown run flags: {unknown!r}")
    logger.info("%s=%s", STATUS_KEY, status)
    logger.info("%s=%s", FLAGS_KEY, ",".join(flags))


def _status_for(code: int, outcome: str) -> str:
    """The headline verdict, from the exit code the run is about to return.

    `stopped` is its own value rather than folding into failed. A run stopped
    on purpose — the Actions job hitting its time limit, which is every night
    of the seed — has copied and recorded thousands of objects and kept its
    progress. Reported as FAILED, with "the mirror was not updated this run"
    underneath, the one outcome meaning "this is fine, re-run" was
    indistinguishable from a real failure.
    """
    if code == 3:
        return "stopped"
    if code == 0:
        return "partial" if outcome == "partial" else "ok"
    return "failed"


def _flags_for(totals: "Totals") -> tuple:
    """The conditions that are independent of the headline verdict.

    Each can occur on a night that otherwise succeeded, so none of them can be
    a branch of the result — that is what made the ledger notice invisible
    before, and it applies to all of these.
    """
    flags = []
    if totals.collisions:
        flags.append("collisions")
    if totals.skipped - totals.collisions > 0:
        flags.append("skipped_names")
    if totals.verify_mismatched:
        flags.append("verify_mismatch")
    if totals.verify_unverified:
        flags.append("verify_incomplete")
    if totals.ledger_flag:
        flags.append(totals.ledger_flag)
    return tuple(flags)


# How many objects the preflight probes, and how far into the manifest it looks
# for them. Several rather than one, because a single orphaned row must not be
# able to reject a correct configuration; bounded, so the check stays instant
# against an 8M-row manifest.
PREFLIGHT_SAMPLE = 5
PREFLIGHT_SCAN = 10_000


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    # Before any work starts, so a stop arriving during the manifest read is
    # honoured rather than killing the process partway through it.
    stopping.install_handlers()
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
    # prod only. Both environments live on one host and this path has no
    # environment in it, so a staging run would open the same ledger.db and
    # write the same `runs` table — which is the watermark. The two would then
    # advance each other's timestamp, each skipping what the other's run
    # already covered. Nothing needs staging objects on Box, so the option is
    # gone rather than the state being split per environment.
    parser.add_argument("--env", required=True, choices=("prod",))
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
    parser.add_argument(
        "--minio-bucket",
        default=os.environ.get("BACKUP_MINIO_BUCKET", ""),
        help="the single MinIO bucket storage-api writes into (STORAGE_S3_BUCKET)",
    )
    parser.add_argument(
        "--minio-prefix",
        default=os.environ.get("BACKUP_MINIO_PREFIX", ""),
        help="tenant prefix storage-api files objects under, e.g. storage-single-tenant",
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
        # 0 here, so a manual run checks nothing unless asked. The scheduled
        # value lives in the workflow, which always passes it explicitly —
        # that is the only place the sample size is set, and the only lever
        # for changing it. There is deliberately no env default: a second
        # place to set it would silently lose to the explicit argument.
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
        emit_status("skipped")
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

    # Taken from the database, BEFORE the manifest snapshot — not from the
    # host afterwards. Anchoring on a moment the snapshot cannot precede means
    # an object written while enumeration runs is re-checked next week rather
    # than falling into a gap nothing ever revisits.
    watermark = dock.database_now(
        db_container,
        user=os.environ.get("POSTGRES_USER", "supabase_admin"),
        database=os.environ.get("POSTGRES_DB", "postgres"),
    )
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
        totals = report_dry_run(manifest, ledger, args.limit)
        ledger.close()
        # `partial` when it found objects a real run would refuse: nothing was
        # copied either way, but "succeeded" on a dry run that turned things
        # away is the same false clean bill the whole verdict exists to stop.
        emit_status(
            "partial" if totals.skipped else "ok", _flags_for(totals)
        )
        return 0

    check_no_stale_daemon()
    check_box_root(args)
    minio = minio_source_from_env(args)
    require_rclone_config(args.rclone_config, args.box_remote)
    run_id = ledger.start_run(now=watermark)
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
    if args.verify:
        totals.verify_pool = VerifyReservoir(VERIFY_POOL_CAP)
    started_at = datetime.now(timezone.utc)
    crashed = False
    try:
        client = wait_for_daemon(daemon)
        preflight_source(client, minio, sample_planned_objects(manifest))
        copy_manifest(client, manifest, ledger, minio, box_fs, args, totals)
        if args.verify and totals.verify_pool and len(totals.verify_pool):
            result = verify_sample(
                client,
                lib.CopyPlan(tuple(totals.verify_pool.items), (), 0),
                box_fs,
                args.box_root,
                args.verify,
            )
            # From the pass itself, not the sample size asked for: a stat that
            # failed checked nothing, and reporting it as checked overstates
            # what the run proved.
            totals.verify_checked = result.checked
            totals.verify_mismatched = result.mismatched
            totals.verify_unverified = result.unverified
            totals.verify_failures = [
                lib.box_path(obj, args.box_root) for obj in result.failures
            ]
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
            "collisions": totals.collisions,
            "already_current": totals.already_current,
            "verify_checked": totals.verify_checked,
            "verify_mismatched": totals.verify_mismatched,
            "verify_unverified": totals.verify_unverified,
        }
        outcome = run_outcome(
            crashed=crashed,
            failed=totals.failed,
            copied=totals.copied,
            limit=args.limit,
            verify_mismatched=totals.verify_mismatched,
            bucket_scoped=bool(args.buckets.strip()),
            stopped=stopping.stopping(),
            collisions=totals.collisions,
            skipped=totals.skipped,
        )
        # The verdict is computed HERE, before the report is written, rather
        # than at the end of the function — so the report carries it.
        #
        # The status line the summary normally reads is the last thing this
        # process prints, and it travels back over the ssh pipe the workflow
        # holds open. GitHub cancelling or timing out the job kills that pipe,
        # so on exactly the runs worth explaining — a deliberate stop that had
        # already copied thousands of objects — the verdict never arrived and
        # the summary fell back to FAILED. Putting it in the report gives it a
        # second route home that does not depend on the connection surviving.
        verdict = _status_for(
            exit_code(
                failed=totals.failed,
                verify_mismatched=totals.verify_mismatched,
                stopped=stopping.stopping(),
                collisions=totals.collisions,
            ),
            outcome,
        )
        # Only the verdict, deliberately. The flags cannot be computed here:
        # publish_ledger sets `ledger_flag` and runs AFTER the report is
        # written, so a set captured now would always miss it. The flags stay
        # on the log route, where they are complete; the report carries the
        # headline, which is what a cancelled run loses.
        # Nested so the teardown below cannot be skipped. Everything in this
        # block can raise — publish_report catches only OSError and
        # RcloneError, and the ledger writes can raise sqlite3.Error on a full
        # disk — and a container left holding the RC port makes every later
        # night fail at check_no_stale_daemon until someone removes it by hand.
        #
        # The stats dict and run_outcome are inside too. Neither can raise
        # today, so this is not a fix; it keeps "nothing between `finally` and
        # `try` can raise" true by construction rather than by inspection.
        try:
            publish_report(
                daemon, state_dir, box_fs, args,
                run_id=run_id, started_at=started_at, outcome=outcome,
                stats=stats, failures=totals.failures,
                skips=totals.skips, verify_failures=totals.verify_failures,
                status=verdict,
            )
            ledger.commit()
        # Inside the finally, not after it. A run that raised is the one whose
        # record matters most, and outside it every crash left a row with no
        # finished_at, no outcome and no stats — while the report published to
        # Box three lines above named the outcome correctly. The local audit
        # trail this job's own error messages tell operators to read was the
        # only place the failure did not appear.
            ledger.finish_run(run_id, outcome, stats)
            # Closed BEFORE it is uploaded. SQLite runs in WAL mode here, so
            # committed rows can still be sitting in ledger.db-wal; a copy of
            # ledger.db on its own would be missing them. close() checkpoints
            # the WAL into the file, which makes the uploaded copy complete.
            ledger.close()
            totals.ledger_flag = publish_ledger(
                daemon, state_dir, box_fs, args,
                copied=totals.copied, crashed=crashed,
            )
        finally:
            # Last, so the daemon is still alive for both uploads above, and
            # unconditional, so nothing above can strand the container.
            daemon.stop()
    logger.info(
        "done — copied %d, failed %d, already current %d, skipped %d",
        totals.copied, totals.failed, totals.already_current, totals.skipped,
    )
    if totals.collisions:
        report_collisions(totals.collisions)
    # Collisions are counted in `skipped` too, so subtract them: a run that
    # only refused a collision must not also claim a name Box cannot store.
    name_skips = totals.skipped - totals.collisions
    if name_skips > 0:
        logger.error(
            "%d %s Box cannot store. They are NOT backed up and nothing here "
            "can change that — only renaming them in Supabase can. Each is "
            "named with its reason in the run report under _runs/ on Box, and "
            "in the `skipping` lines above. The run is recorded partial, so "
            "they stay in view every night until they are renamed rather than "
            "dropping out of sight after tonight.",
            name_skips, SKIPPED_NAME_MARKER,
        )
    if totals.verify_mismatched:
        logger.error(
            "%d of %d verified object(s) were missing or the wrong size on "
            "Box. Every one is named in the run report under _runs/ on Box, "
            "and in the ERROR lines above.\n"
            "This run changed NOTHING to compensate: the ledger still records "
            "them as mirrored, so later runs will skip them and this warning "
            "will not repeat. Putting them back needs a person — see "
            "'What verification does, and does not, prove' in the wiki. The "
            "run is recorded partial, so the watermark is held meanwhile.",
            totals.verify_mismatched, totals.verify_checked,
        )
    elif totals.verify_checked and not totals.verify_unverified:
        logger.info("verified %d object(s) on Box, all present and correct",
                    totals.verify_checked)
    if totals.verify_unverified:
        # One marker for any shortfall, not only for a total blackout. Gating
        # this on `checked == 0` made it a cliff at exactly zero: 2 answered
        # out of 50 printed "verified 2 object(s), all present and correct"
        # and a headline of "succeeded — 200,000 copied, 2 verified", which
        # states the night was checked when 96% of the sample went unanswered.
        #
        # Still not a failure. The copies were each confirmed as they were
        # made, so an unanswered stat is evidence of nothing and must not fail
        # the run or move the watermark. It has to be said out loud instead,
        # because the count above otherwise reads as a clean bill of health.
        sampled = totals.verify_checked + totals.verify_unverified
        logger.error(
            "%s: Box answered for %d of the %d object(s) sampled, so the "
            "other %d were neither confirmed present nor found missing. The "
            "copies were confirmed as they were made, so this is not a reason "
            "to re-copy anything — but the night is less checked than the "
            "count says, and if it repeats the check is not doing its job. "
            "The sample size is the workflow's `verify` input; lowering it, "
            "or moving the schedule off Box's busy hours, is the lever. "
            "These stat calls fire straight after a run that may have pushed "
            "hundreds of thousands of objects, which is when Box throttles.",
            VERIFY_INCOMPLETE_MARKER, totals.verify_checked, sampled,
            totals.verify_unverified,
        )
    if totals.failed:
        logger.error(
            "%d object(s) failed after %d attempts each — re-run to retry them",
            totals.failed, MAX_ATTEMPTS,
        )
    code = exit_code(
        failed=totals.failed,
        verify_mismatched=totals.verify_mismatched,
        stopped=stopping.stopping(),
        collisions=totals.collisions,
    )
    # Same verdict the report already carries; printed here because the log is
    # the faster route when the connection does survive.
    emit_status(verdict, _flags_for(totals))
    return code


@dataclass
class Totals:
    """Run-wide counters, accumulated across manifest batches."""

    copied: int = 0
    failed: int = 0
    skipped: int = 0
    collisions: int = 0
    already_current: int = 0
    verify_checked: int = 0
    verify_mismatched: int = 0
    # Sampled, but Box did not answer. Not a finding against the backup, so it
    # changes no exit code — it only stops `verify_checked` claiming them.
    verify_unverified: int = 0
    verify_pool: object = None
    # Which way the ledger upload went, if it did not go cleanly. Set by
    # publish_ledger, read by _flags_for — the two conditions are
    # opposites and the summary must tell them apart.
    ledger_flag: str | None = None
    failures: list = field(default_factory=list)
    # Objects refused before any copy was attempted, and objects the check
    # found missing from Box. Both end up in the run report on Box, because
    # both name an object that is NOT backed up and the job log is a GitHub
    # Actions log under retention — the one place that answers "which one?"
    # must outlive it.
    skips: list = field(default_factory=list)
    verify_failures: list = field(default_factory=list)


def sample_planned_objects(manifest: Path, count: int = PREFLIGHT_SAMPLE) -> list:
    """A spread of objects the run would copy, for the preflight to probe.

    Deliberately not the first one. The manifest is ordered
    `bucket_id, updated_at`, so the first safe row is always the OLDEST object
    in the alphabetically-first bucket — the row most likely to be a
    `storage.objects` entry whose bytes left MinIO years ago. Probing only that
    lets a single dead row reject a correct configuration, in the same way
    every week, with an error blaming the bucket and prefix settings.

    Reading is bounded to PREFLIGHT_SCAN lines and then strided, so the check
    stays instant against an 8M-row manifest.
    """
    candidates = []
    with manifest.open(encoding="utf-8") as handle:
        for lineno, obj in enumerate(lib.iter_manifest(handle)):
            if lineno >= PREFLIGHT_SCAN:
                break
            if lib.unsafe_reason(obj) is None:
                candidates.append(obj)
    if not candidates:
        return []
    stride = max(1, len(candidates) // count)
    return candidates[::stride][:count]


def exit_code(
    *,
    failed: int,
    verify_mismatched: int,
    stopped: bool = False,
    collisions: int = 0,
) -> int:
    """What the run tells its caller, which for a scheduled run is everything.

    A verification mismatch must NOT be 0. The workflow's only route to a human
    is the run failing — a green tick notifies nobody, and the mismatch would
    then live solely in a JSON file on Box that someone has to think to open.

    It is also not 1. Every copy reported success and the check disagreed, so
    the mirror is misreporting itself rather than some copies having errored,
    and those want telling apart in a job log.

    It fires once, not until someone acts. The object is already recorded as
    copied, so the next run plans it `already_current`, never re-copies it and
    never re-checks it — the count returns to 0 and the run reports clean. The
    run deliberately does not touch the ledger to compensate: this is a backup,
    and nothing here deletes a record of what is on Box. Restoring the object
    needs a person, and the run report names it.
    """
    if failed:
        return 1
    if verify_mismatched:
        return 4
    # Its own code rather than 4, because the remedies are opposite and one
    # is dangerous here. Exit 4 says delete the ledger row so the object is
    # copied again. Do that for a collision and the row you delete belongs to
    # the object that WON the path — the refused one has no row. The winner is
    # then behind the watermark and not re-enumerated, so the refused object
    # takes the path and overwrites the winner's file on Box, permanently.
    # This needs a rename in Supabase instead.
    if collisions:
        return 5
    # 3 is already the documented "interrupted; progress is in the ledger and
    # the next run resumes". Installing a signal handler means SIGINT no longer
    # raises KeyboardInterrupt, so without this a stopped run would report the
    # clean 0 of a run that finished everything.
    if stopped:
        return 3
    return 0


def run_outcome(
    *,
    crashed: bool,
    failed: int,
    copied: int,
    limit: int | None,
    verify_mismatched: int = 0,
    bucket_scoped: bool = False,
    stopped: bool = False,
    collisions: int = 0,
    skipped: int = 0,
) -> str:
    """Classify a finished run — and decide whether it can be a watermark.

    `Ledger.last_successful_run()` only considers runs recorded `ok`, so this
    is what stops a run from losing objects. A run may only be `ok` if it saw
    the whole table and everything it did was sound. Three cases must not be:

    A run cut short by `--limit` has not seen the whole table; recording it
    clean would make the next run filter on its start time and skip everything
    the limit left behind, permanently and without saying so.

    A run that was asked to stop has not reached the end of the table either,
    for the same reason and with the same consequence.

    A run scoped by `--buckets` has not seen the other buckets. Recording it
    clean advances the watermark for ALL of them, so every object in the
    buckets it never looked at, older than this run, is never enumerated
    again. The wiki's own smoke test is bucket-scoped, one edit away from
    dropping the `--limit` that currently saves it.

    A run that refused a collision has not mirrored one of the two objects, and
    nothing will until a person renames one of them — so it must not become the
    watermark either, or the object stops being enumerated and the one log line
    naming it is the last anyone hears of it. The cost is real: until that
    rename, every night re-reads everything changed since the last clean run
    rather than since last night — a window that grows until someone acts, and
    the whole table if no run has ever been clean. That is the intended trade,
    because an object nobody knows is missing is worse than a slow night.

    A run whose verification found objects missing from Box has copied things
    that are not there. Recording it clean would advance the watermark past
    them, so nothing would ever look at them again — the check would have
    found the fault and then buried it.

    A run that skipped an object because Box cannot store its name is the same
    situation as a refused collision, and was treated as its opposite. The
    object is not backed up, nothing on this side can make it so, and only a
    rename in Supabase fixes it — but `skipped` was not a parameter here at
    all, so the run recorded `ok`, the watermark moved past it, and the
    object's `updated_at` never changes, so no later incremental run ever
    enumerates it again. Permanently absent from the mirror, with a WARNING
    the summary did not surface as the only trace. It holds the watermark now,
    on the same reasoning and at the same cost as a collision.
    """
    if crashed:
        return "error"
    truncated = limit is not None and copied >= limit
    if (
        failed or truncated or verify_mismatched
        or bucket_scoped or stopped or collisions or skipped
    ):
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
    skips: list | None = None,
    verify_failures: list | None = None,
    status: str = "",
) -> None:
    """Write the run report locally, then copy it to Box beside the mirror.

    Best-effort by design: the objects are already on Box, and losing the
    report must not turn a good run into a failed one. It is logged loudly
    instead, and the local copy under the state dir survives either way.

    `skips` and `verify_failures` name the objects that are NOT on Box for a
    reason other than a copy failure. They belong here rather than only in the
    job log because this file is the durable record — the log is a GitHub
    Actions log under retention, and the verification alarm is documented as
    never repeating.
    """
    entry = report.RunReport(
        env=args.env,
        run_id=run_id,
        started_at=started_at,
        finished_at=datetime.now(timezone.utc),
        outcome=outcome,
        box_root=args.box_root,
        stats=stats,
        skips=list(skips or []),
        verify_failures=list(verify_failures or []),
        status=status,
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


def publish_ledger(
    daemon: dock.RcDaemon,
    state_dir: Path,
    box_fs: str,
    args: argparse.Namespace,
    *,
    copied: int,
    crashed: bool,
) -> str | None:
    """Copy the ledger to Box, so losing the host does not mean re-seeding.

    The ledger records which version of every object is on Box, and it is what
    lets a multi-week seed stop and carry on. It lives on the deploy host —
    the machine this job exists to survive losing. Without a copy, a rebuilt
    host starts from an empty ledger, concludes nothing has ever been copied,
    and re-transfers all eight million objects. Listing Box cannot rebuild it:
    the ledger is keyed on each object's version, and a listing shows only
    that a path exists.

    Skipped in three cases: after a run that copied nothing — the file is a
    gigabyte or two once seeded and a quiet night has not meaningfully changed
    it; after one that crashed, so a half-written ledger cannot replace a good
    copy; and when the copy on Box is larger than this one, which means this
    host did not build the mirror that copy describes.

    Best-effort, like the run report: the objects are already safely on Box
    and a failed upload must not turn a good run into a failed one.
    """
    if crashed:
        logger.info("ledger not uploaded: the run did not finish cleanly")
        return None
    if not copied:
        logger.info("ledger not uploaded: nothing was copied this run")
        return None
    local = state_dir / report.LEDGER_FILENAME
    try:
        local_size = local.stat().st_size
    except OSError as exc:
        logger.error(
            "cannot read %s: %s — %s", local, exc, LEDGER_STALE_MARKER
        )
        return "ledger_stale"
    destination = report.box_ledger_path(args.box_root)
    try:
        client = RcloneRC(daemon.url, daemon.user, daemon.password)
        # Never replace a bigger copy with a smaller one. A ledger only grows,
        # so a smaller one means this host did not build the mirror the copy on
        # Box describes — a rebuilt host, or a wiped state dir. The smoke test
        # in the wiki copies twenty objects, which is enough to trigger this
        # upload, so without the check the first command an operator runs after
        # losing the host would replace the record of eight million objects
        # with a record of twenty.
        existing = client.stat(box_fs, destination)
        remote_size = existing.get("Size") if existing else None
        if isinstance(remote_size, int) and local_size < remote_size:
            logger.error(
                "ledger NOT uploaded: %s. The copy at %s is %s and this run's "
                "is only %s, so this host is not the one that built that "
                "mirror. The Box copy is the good one — RESTORE it onto this "
                "host before running again, and do not upload over it. See "
                "'If the deploy host itself is gone' in the wiki. Uploading "
                "now would lose the record of what is already backed up.",
                LEDGER_AHEAD_MARKER, destination,
                lib.format_bytes(remote_size), lib.format_bytes(local_size),
            )
            return "ledger_ahead"
        client.copy_file(
            dock.STATE_MOUNT, report.LEDGER_FILENAME, box_fs, destination
        )
        logger.info("ledger on Box: %s (%s)", destination, lib.format_bytes(local_size))
    except Exception as exc:
        # Deliberately broad: this runs in the cleanup path, and anything
        # raised here would skip the container teardown below it.
        logger.error(
            "ledger stayed on the host only — upload failed: %s — %s",
            exc, LEDGER_STALE_MARKER,
        )
        return "ledger_stale"


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


def report_dry_run(manifest: Path, ledger: Ledger, limit: int | None) -> Totals:
    """Plan everything, copy nothing, and report what a real run would refuse.

    Returns its totals so the caller can emit a verdict. A dry run used to
    return none at all, which left the summary with nothing to read and
    falling back to the step's own outcome — so a dry run that refused a
    collision rendered "succeeded". It is step one of the pre-seed checklist
    and the first thing run on a rebuilt host, which makes it the worst place
    for a silent pass.
    """
    totals = Totals()
    for plan in plan_batches(manifest, ledger, limit):
        totals.copied += len(plan.copies)
        totals.skipped += len(plan.skipped)
        totals.collisions += plan.collisions
        totals.already_current += plan.already_current
        report_skips(plan)
    logger.info(
        "dry run — would copy %d, %d already current, %d skipped; nothing was copied",
        totals.copied, totals.already_current, totals.skipped,
    )
    if totals.collisions:
        report_collisions(totals.collisions)
    name_skips = totals.skipped - totals.collisions
    if name_skips > 0:
        logger.error(
            "%d %s Box cannot store. A real run would refuse them too.",
            name_skips, SKIPPED_NAME_MARKER,
        )
    return totals


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
        # Between batches as well as between objects: a batch is 20,000
        # objects, and a stop should not have to wait for the rest of one.
        if stopping.stopping():
            logger.warning(
                "stopping before a further batch — %d object(s) copied so far",
                totals.copied,
            )
            return
        report_skips(plan)
        totals.skipped += len(plan.skipped)
        for refused in plan.skipped:
            # Capped like the copy failures, and for the same reason: a seed
            # run must not grow an unbounded list on a host running the whole
            # stack. The count in `stats` stays exact.
            if len(totals.skips) < MAX_TRACKED_FAILURES:
                path = refused.obj.storage_path
                totals.skips.append(
                    f"{path if path.isascii() else ascii(path)}: {refused.reason}"
                )
        totals.collisions += plan.collisions
        totals.already_current += plan.already_current
        if not plan.copies:
            continue
        logger.info(
            "batch: %d object(s), %s", len(plan.copies), lib.format_bytes(plan.total_bytes)
        )
        copied, failed = copy_all(
            client, plan, minio, box_fs, args.box_root, ledger, args.workers,
            failures=totals.failures,
            succeeded=totals.verify_pool,
        )
        totals.copied += copied
        totals.failed += failed
        # The reservoir is offered every successful copy inside copy_all.


def minio_source_from_env(args: argparse.Namespace) -> MinioSource:
    access = os.environ.get("MINIO_ROOT_USER", "")
    secret = os.environ.get("MINIO_ROOT_PASSWORD", "")
    if not access or not secret:
        raise lib.BackupError(
            "MINIO_ROOT_USER / MINIO_ROOT_PASSWORD missing — the service reads "
            "them from the deploy's .env file; export them for a manual run"
        )
    if not args.minio_bucket.strip():
        raise lib.BackupError(
            "BACKUP_MINIO_BUCKET is empty. It must name the single MinIO "
            "bucket storage-api writes into (STORAGE_S3_BUCKET in the compose "
            "file). Left empty, rclone reads each object's own bucket_id as a "
            "bucket name and every copy 404s."
        )
    return MinioSource(
        endpoint=os.environ.get("BACKUP_MINIO_ENDPOINT", "http://supabase-minio:9000"),
        access_key=access,
        secret_key=secret,
        bucket=args.minio_bucket,
        prefix=args.minio_prefix,
    )


def check_no_stale_daemon() -> None:
    """Refuse to start while a previous run's container is still around.

    Deliberately a refusal rather than a cleanup. Removing a container is
    destructive and this job should not do destructive things on its own
    initiative — a person can look, confirm it is a leftover, and remove it.

    The alternative is what happens today: `docker run` fails with `port is
    already allocated`, which says nothing about a run three nights ago being
    the cause, and gives no hint that a `docker rm` is all that is needed.
    """
    stale = dock.find_stale_daemons()
    if not stale:
        return
    listed = "\n".join(f"    {line}" for line in stale)
    raise lib.BackupError(
        "an rclone container from an earlier run is still present:\n"
        f"{listed}\n"
        "It holds the RC port and a live Box session, so this run cannot start "
        "its own. A run stopped with SIGTERM — a reboot, a `kill`, a cancelled "
        "workflow — skips the cleanup that would normally remove it.\n"
        "Nothing else is using it: this run already holds the lock, so there is "
        "no other backup in progress. Remove it and re-run:\n"
        f"    docker rm --force $(docker ps -aq --filter name={dock.RC_CONTAINER_PREFIX})"
    )


def check_box_root(args: argparse.Namespace) -> None:
    """Refuse a destination that would scatter the mirror across Box.

    `--box-root` defaults to empty, and an empty root means `box_path` returns
    a bare `<bucket>/<name>` — so a manual seed launched without
    BACKUP_BOX_ROOT exported writes eight million objects, plus `_runs/`,
    straight into the top level of the Box drive. Nothing about that looks
    wrong while it happens, and undoing it is a manual cleanup of the whole
    account.

    There is no cross-environment check because there is no other environment:
    production is the only thing mirrored, so a root naming anything else is
    just a wrong root, which the pinned value in the env-defaults tests catches
    before it reaches a deploy.
    """
    root = args.box_root.strip().strip("/")
    if not root:
        raise lib.BackupError(
            "BACKUP_BOX_ROOT is empty. Set it to the folder on Box this "
            "environment mirrors into, e.g.\n"
            f"    export BACKUP_BOX_ROOT=Bloom-Backups/BloomV2-Data-Backup/{args.env}/storage\n"
            "Left empty, the objects would be written to the top level of the "
            "Box drive."
        )


def preflight_source(client: RcloneRC, minio: MinioSource, samples: list) -> None:
    """Prove the configured bucket and prefix actually address real bytes.

    Config cannot fix a layout bug on its own — a wrong value in an env file
    fails exactly as a wrong constant did, just in two files instead of one.
    What makes it safe is failing here, in seconds, naming the paths that were
    tried, rather than after days of a seed that 404s all eight million
    objects and leaves an empty mirror.

    ONE object resolving is enough. It proves the bucket and the prefix, which
    is the whole of what this check is for. Refusing only when every sample
    misses is what stops a single orphaned row — a row whose bytes left MinIO
    while the row survived — from rejecting a correct configuration every week.
    """
    if not samples:
        return
    tried: list[str] = []
    errors: list[str] = []
    for sample in samples:
        remote = lib.source_remote(sample, minio.prefix)
        tried.append(f"{minio.bucket.strip('/')}/{remote}")
        try:
            if client.stat(minio.fs(), remote) is not None:
                logger.info(
                    "preflight ok — source root %s resolves (%d of %d sampled)",
                    minio.root(), len(tried), len(samples),
                )
                return
        except RcloneError as exc:
            errors.append(str(exc))
    listed = "\n".join(f"    {path}" for path in tried)
    detail = "\nErrors: " + "; ".join(errors) if errors else ""
    raise lib.BackupError(
        f"preflight failed: none of {len(tried)} sampled object(s) is in MinIO "
        "where this job expects it:\n"
        f"{listed}\n"
        "Postgres lists them but MinIO does not hold them there. Check "
        "BACKUP_MINIO_BUCKET and BACKUP_MINIO_PREFIX against one real key:\n"
        f"    rclone lsf :s3:{minio.bucket.strip('/')} --max-depth 3"
        f"{detail}"
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


def report_collisions(count: int) -> None:
    """The line a run prints when it refused objects, real or dry.

    The workflow summary greps this phrase, so it is the difference between a
    night that says OBJECTS NOT BACKED UP and one that says succeeded. Shared
    with the dry run deliberately: a dry run is what an operator runs first,
    and it was the one path where a refused collision stayed invisible.
    """
    logger.error(
        "%d object(s) were NOT backed up: their names normalize onto a path "
        "another object already holds, so copying them would have deleted "
        "what is there. Box cannot hold both. Rename the object named at the "
        "START of each `skipping` line above — renaming its twin instead "
        "leaves a ledger row still claiming the path, and this object is then "
        "refused for ever.",
        count,
    )


def report_skips(plan: lib.CopyPlan) -> None:
    """Name every object the plan refused to mirror, and why.

    A path that is not plain ASCII is escaped, because the reason a collision
    happens is that the two names look the same. Unescaped, the line names an
    object the operator cannot pick out from its twin.
    """
    for skipped in plan.skipped:
        path = skipped.obj.storage_path
        logger.warning(
            "skipping %s: %s",
            path if path.isascii() else ascii(path),
            skipped.reason,
        )



if __name__ == "__main__":
    sys.exit(main())
