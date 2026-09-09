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
  6 = every object copied, but the ledger's Box copy is stale or ahead of this
      host — the record that makes a re-seed unnecessary is not safe
"""

from __future__ import annotations

import argparse
import json
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
from rclone_rc import DEFAULT_RC_PORT, MinioSource, RcloneError, RcloneRC  # noqa: E402
import stopping  # noqa: E402
from runlock import ACTIONS_RUN_ENV, SKIP_MARKER, LockHeld, RunLock  # noqa: E402

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

# Objects refused before any copy because Box cannot store the name. A
# collision is the other kind of permanent non-backup, and the two carry
# opposite advice — rename either of the colliding pair, versus rename this
# one and nothing else will do — so they stay separate lines with separate
# flags. Unlike a collision this one does NOT hold the watermark: nothing on
# this side can ever fix such a name, so holding it would freeze the mirror's
# progress for good.
SKIPPED_NAME_MARKER = "object(s) were SKIPPED for their names"

# The run's verdict. Emitted only by `emit_status`, and read anchored to the
# start of a log line — timestamp, level, then the key. Object names reach this
# log and are partly chosen by whoever uploads the file, so a name can only
# appear once a message has begun and can never forge one of these.
# The only keys this job reads out of a deploy env file. An allow-list rather
# than a prefix match: the file beside these holds the JWT signing keys and the
# service-role secret, and rclone takes its whole option surface from `RCLONE_*`
# — one `RCLONE_CONFIG=` line would point the mirror at somebody else's remote.
ENV_KEYS = (
    "POSTGRES_USER",
    "POSTGRES_DB",
    "MINIO_ROOT_USER",
    "MINIO_ROOT_PASSWORD",
    "OBJECT_BACKUP_MINIO_BUCKET",
    "OBJECT_BACKUP_MINIO_PREFIX",
    "OBJECT_BACKUP_MINIO_ENDPOINT",
    "OBJECT_BACKUP_BOX_REMOTE",
    "OBJECT_BACKUP_BOX_ROOT",
    "OBJECT_BACKUP_STATE_DIR",
    "OBJECT_BACKUP_WORKERS",
    "OBJECT_BACKUP_BWLIMIT",
    "OBJECT_BACKUP_RC_PORT",
    "OBJECT_BACKUP_RCLONE_CONFIG",
)

# Returned to the caller instead of exported. Every `docker` child this job
# starts inherits our environment, so a secret left in it travels further than
# the one function that needs it.
# `.env.prod.defaults` classifies both of these as credentials rather than
# config: they pair with an admin password.
SECRET_ENV_KEYS = ("MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD")

# The renderer anchors on this shape — timestamp, level, then the key — so the
# two are named in one place. `asctime` contains a space.
LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"

STATUS_KEY = "BOX_BACKUP_STATUS"
FLAGS_KEY = "BOX_BACKUP_FLAGS"
# The counts, as JSON on one anchored line. The summary reads them from here
# rather than from the prose lines above, which are for people.
STATS_KEY = "BOX_BACKUP_STATS"

# The whole vocabulary. Anything else is a bug, not a new condition.
STATUS_VALUES = ("ok", "skipped", "stopped", "partial", "failed")
FLAG_VALUES = (
    "collisions",
    "skipped_names",
    "verify_mismatch",
    "verify_incomplete",
    "ledger_stale",
    "ledger_ahead",
    "source_gone",
)


def load_env_file(path: Path) -> dict[str, str]:
    """Read a deploy .env file into a plain dict.

    A systemd unit would have read this file for us; an `ssh host cmd` shell
    reads no profile and no env file, and sourcing it would let a value
    containing a quote, a backtick or a `$` either fail to parse or run.

    Deliberately not a shell: `export ` prefixes, inline `#` comments and
    values spanning several lines are all read literally. The deploy env files
    use none of them, and guessing would corrupt a value containing a `#` far
    more quietly than refusing to.
    """
    values: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or not key[0].isalpha():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def apply_env_file(path: Path) -> dict[str, str]:
    """Load this job's keys as defaults, and return the ones it found.

    Only ENV_KEYS are taken; the rest of the file is left where it is, and a
    real environment variable still wins over it — which is what makes a
    manual run with a couple of exports work.

    A blank value counts as absent so the defaults still apply: blanking a
    line is how someone asks for the default, and an empty
    OBJECT_BACKUP_MINIO_BUCKET would otherwise make rclone read each object's
    own bucket_id as a bucket name and 404 every copy.
    """
    if not path.is_file():
        raise lib.BackupError(f"env file not found: {path}")
    try:
        values = load_env_file(path)
    except (OSError, ValueError) as exc:
        # OSError: the runner writes .env.<env> at mode 600 and the deploy user
        # reads it. ValueError: a byte in it that is not UTF-8.
        raise lib.BackupError(f"cannot read env file {path}: {exc}") from exc
    found: dict[str, str] = {}
    for key in ENV_KEYS:
        value = values.get(key, "")
        if not value:
            continue
        found[key] = value
        if key not in SECRET_ENV_KEYS:
            os.environ.setdefault(key, value)
    logger.info("loaded %d of %d values from %s", len(found), len(values), path.name)
    return found


def emit_status(status: str, flags=(), stats=None) -> None:
    """Print the run's verdict in a form no object name can imitate.

    One line for the headline verdict, one for the independent conditions, and
    one for the counts. The first two come from a closed vocabulary; all three
    are anchored by the workflow. Flags are separate from the status because
    they are not alternatives: a night can succeed AND have left the Box ledger
    stale AND have refused a name.
    """
    if status not in STATUS_VALUES:
        raise ValueError(f"unknown run status: {status!r}")
    unknown = [f for f in flags if f not in FLAG_VALUES]
    if unknown:
        raise ValueError(f"unknown run flags: {unknown!r}")
    logger.info("%s=%s", STATUS_KEY, status)
    logger.info("%s=%s", FLAGS_KEY, ",".join(flags))
    if stats is not None:
        # One line, no newlines in it: the workflow anchors on the line start.
        logger.info("%s=%s", STATS_KEY, json.dumps(stats, sort_keys=True))


def _stats_for(totals: "Totals", listed: int) -> dict:
    """The run's counters, in the shape the report and the summary both read."""
    return {
        "listed": listed,
        "copied": totals.copied,
        "failed": totals.failed,
        "skipped": totals.skipped,
        "collisions": totals.collisions,
        "source_gone": totals.source_gone,
        "already_current": totals.already_current,
        "verify_checked": totals.verify_checked,
        "verify_mismatched": totals.verify_mismatched,
        "verify_unverified": totals.verify_unverified,
    }


def _status_for(code: int, outcome: str, stopped: bool = False) -> str:
    """What to tell the operator happened — NOT the exit code in words.

    The two answer different questions. The exit code ranks conditions worth a
    person's attention, so the most pointed number reaches the notification;
    this ranks what happened to the copying, which is what the summary
    headlines. Derived from the code, every non-zero one reads as `failed` —
    including three nights where every copy succeeded.

    `stopped` is its own value rather than folding into failed: a run stopped
    on purpose, which is every night of the seed, has copied and recorded
    thousands of objects and kept its progress. It is the one outcome meaning
    "this is fine".

    `error` is checked FIRST, and from the outcome rather than the code. A
    crash unwinds through `except BaseException` with every counter still
    zero, so the code is 0 — and the outcome is the only input that knows.
    """
    if outcome == "error":
        return "failed"
    # Only these two mean the copying itself did not work: objects failed
    # after their retries, or the run could not start. Everything else that
    # exits non-zero is a condition the run FOUND and reports through a flag.
    if code in (1, 2):
        return "failed"
    # Taken directly rather than read off code 3, which a higher-ranked
    # condition takes first. A seed night stopped by the job's time limit
    # whose ledger upload was throttled exits 6, and it is still a stopped
    # night — "stopped, progress kept" is exactly what its operator needs to
    # read, and it is the one headline meaning "this is fine".
    if stopped or code == 3:
        return "stopped"
    return "partial" if outcome == "partial" else "ok"


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
    if totals.source_gone:
        flags.append("source_gone")
    return tuple(flags)


# How many objects the preflight probes, and how far into the manifest it looks
# for them. Several rather than one, because a single orphaned row must not be
# able to reject a correct configuration; bounded, so the check stays instant
# against an 8M-row manifest.
PREFLIGHT_SAMPLE = 5
PREFLIGHT_SCAN = 10_000


def env_file_for(argv: list[str] | None) -> tuple[Path, bool]:
    """The env file this invocation reads, and whether it asked for debug logs.

    Both are needed before `parse_args`: the file fills the environment its
    defaults read, and the log level has to be set to report a failure to read
    it.
    """
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--env", default="prod")
    pre.add_argument("--env-file", default="")
    pre.add_argument("-v", "--verbose", action="store_true")
    known, _ = pre.parse_known_args(argv)
    return (
        Path(known.env_file) if known.env_file else Path(f".env.{known.env}")
    ), known.verbose


def check_state_dir(args: argparse.Namespace, configured: str) -> None:
    """Refuse when the env file and the caller name different directories.

    The workflow's cancel step and its summary both read this directory over
    separate ssh connections that see no env file. Pointed elsewhere here, the
    job would work while those two silently watched an empty one — a cancel
    that stops nothing and a verdict that is never recovered.
    """
    if configured and str(args.state_dir) != configured:
        raise lib.BackupError(
            f"OBJECT_BACKUP_STATE_DIR is {configured} but this run was given "
            f"{args.state_dir}. Point them at the same directory."
        )


def main(argv: list[str] | None = None) -> int:
    # Before anything reads a file: --help is how an operator finds the
    # argument surface, and it must work from any directory.
    if {"-h", "--help"} & set(argv if argv is not None else sys.argv[1:]):
        parse_args(argv)
    env_file, verbose = env_file_for(argv)
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format=LOG_FORMAT,
    )
    # Before anything else, including reading the config, so a stop arriving
    # at any point is honoured rather than killing the process partway.
    stopping.install_handlers()
    try:
        # Before the parser is built: every default in it reads the
        # environment, and this file is what fills it.
        found = apply_env_file(env_file)
        args = parse_args(argv)
        # The environment first, then the file — the same precedence every
        # other key gets, and the same order the sibling job uses. Reversed,
        # an operator who exports a corrected password silently gets the
        # file's.
        args.minio_secret = args.minio_secret or found.get("MINIO_ROOT_PASSWORD", "")
        check_state_dir(args, found.get("OBJECT_BACKUP_STATE_DIR", ""))
        return run_backup(args)
    except (dock.DockerError, lib.BackupError) as exc:
        logger.error("%s", exc)
        return 2
    except KeyboardInterrupt:
        logger.warning("interrupted — ledger holds progress; re-run to resume")
        return 3


def _env_int(key: str, default: int) -> int:
    """A whole number from the environment, or a config failure naming the key.

    Read while the parser's defaults are being built, so an unhandled
    ValueError here is a traceback and exit 1 — the code documented as "objects
    failed after retries" — before the run has printed a verdict.
    """
    raw = os.environ.get(key, "")
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise lib.BackupError(f"{key} must be a whole number, not {raw!r}") from None


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--env-file",
        default="",
        help="deploy env file to read settings from (default: .env.<env>)",
    )
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
        default=os.environ.get("OBJECT_BACKUP_BOX_ROOT", ""),
        help="path under the Box remote to mirror into, e.g. Bloom-Backups/prod",
    )
    parser.add_argument(
        "--box-remote",
        default=os.environ.get("OBJECT_BACKUP_BOX_REMOTE", "box"),
        help="name of the configured rclone Box remote (default: box)",
    )
    parser.add_argument(
        "--rclone-config",
        default=os.environ.get(
            "OBJECT_BACKUP_RCLONE_CONFIG",
            str(Path.home() / ".config/rclone/rclone.conf"),
        ),
    )
    parser.add_argument(
        "--minio-bucket",
        default=os.environ.get("OBJECT_BACKUP_MINIO_BUCKET", ""),
        help="the single MinIO bucket storage-api writes into (STORAGE_S3_BUCKET)",
    )
    parser.add_argument(
        "--minio-prefix",
        default=os.environ.get("OBJECT_BACKUP_MINIO_PREFIX", ""),
        help="tenant prefix storage-api files objects under, e.g. storage-single-tenant",
    )
    parser.add_argument(
        "--state-dir",
        default=os.environ.get("OBJECT_BACKUP_STATE_DIR", DEFAULT_STATE_DIR),
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=_env_int("OBJECT_BACKUP_WORKERS", DEFAULT_WORKERS),
    )
    parser.add_argument(
        "--rc-port",
        type=int,
        default=_env_int("OBJECT_BACKUP_RC_PORT", DEFAULT_RC_PORT),
    )
    parser.add_argument(
        "--bwlimit", default=os.environ.get("OBJECT_BACKUP_BWLIMIT", "")
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="copy at most N objects, then stop (for smoke tests)",
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
    args = parser.parse_args(argv)
    # Carried on the namespace rather than read from the environment where it
    # is used, so the value need never be exported. `main` replaces it with
    # what the env file held; a plain export still works for a run by hand.
    args.minio_secret = os.environ.get("MINIO_ROOT_PASSWORD", "")
    return args


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
    # Config first, before the manifest read. Every check below is a string
    # or a file on this host — none of them can pass at 02:00 and fail at
    # 05:00 — and reading eight million rows before finding out that
    # OBJECT_BACKUP_BOX_ROOT is empty spends four hours to learn something known in
    # a millisecond. The dry run reaches them too now, which is the point:
    # it is step one of the pre-seed checklist.
    check_box_root(args)
    destination = f"{args.box_remote}:{args.box_root.strip().strip('/')}"
    check_destination(ledger, destination)
    minio = minio_source_from_env(args)
    require_rclone_config(args.rclone_config, args.box_remote)

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
            "partial" if totals.skipped else "ok",
            _flags_for(totals),
            _stats_for(totals, listed),
        )
        return 0

    # Left here rather than moved up with the config checks: it is the one
    # that reads live state, and it is answered right before the daemon it
    # is about would start.
    check_no_stale_daemon()
    # Recorded only now — a dry run must not claim a destination it never
    # wrote to.
    ledger.remember_destination(destination)
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
    except lib.Stopped as exc:
        # NOT `crashed`. A stop is the one outcome that means "this is fine",
        # and the run still has a report and a ledger to put away below — the
        # same ones it would have written had the stop arrived a minute later,
        # during the copies.
        logger.warning("%s", exc)
    except BaseException:
        # Recorded before re-raising so the Box report still names the run
        # that died — a failed run is the one most worth a record.
        crashed = True
        raise
    finally:
        stats = _stats_for(totals, listed)
        outcome = run_outcome(
            crashed=crashed,
            failed=totals.failed,
            copied=totals.copied,
            limit=args.limit,
            bucket_scoped=bool(args.buckets.strip()),
            stopped=stopping.stopping(),
            collisions=totals.collisions,
            gone=totals.source_gone,
        )
        # Computed before the report is written, so the report carries it. The
        # copy the summary normally reads is the last thing this process prints,
        # over an ssh pipe a cancel or a timeout kills — the report is the route
        # home that does not depend on the connection surviving.
        verdict = _status_for(
            exit_code(
                failed=totals.failed,
                verify_mismatched=totals.verify_mismatched,
                stopped=stopping.stopping(),
                collisions=totals.collisions,
            ),
            outcome,
            stopped=stopping.stopping(),
        )
        # The four flags already final go in too, or a cancelled night recovers
        # its headline and loses every notice — including the refused-filename
        # one, whose whole justification is that you get exactly one. The ledger
        # flags cannot: the upload has not run. Those reach a human by exit code
        # 6, which notifies whether or not the summary renders anything.
        report_flags = _flags_for(totals)
        # Nested so the teardown below cannot be skipped. Everything in this
        # block can raise — the ledger writes raise sqlite3.Error on a full disk
        # — and a container left holding the RC port makes every later night
        # fail at check_no_stale_daemon until someone clears it by hand.
        try:
            publish_report(
                daemon,
                state_dir,
                box_fs,
                args,
                run_id=run_id,
                started_at=started_at,
                outcome=outcome,
                stats=stats,
                failures=totals.failures,
                skips=totals.skips,
                name_skips=totals.name_skips,
                source_gone=totals.gone,
                verify_failures=totals.verify_failures,
                status=verdict,
                flags=report_flags,
            )
            ledger.commit()
            # Inside the finally, not after it: a run that raised is the one whose
            # record matters most, and it is the local audit trail this job's own
            # error messages tell operators to read.
            ledger.finish_run(run_id, outcome, stats)
            # Closed BEFORE it is uploaded. SQLite runs in WAL mode here, so
            # committed rows can still be sitting in ledger.db-wal; a copy of
            # ledger.db on its own would be missing them. close() checkpoints
            # the WAL into the file, which makes the uploaded copy complete.
            ledger.close()
            totals.ledger_flag = publish_ledger(
                daemon,
                state_dir,
                box_fs,
                args,
                copied=totals.copied,
                crashed=crashed,
            )
        finally:
            # Last, so the daemon is still alive for both uploads above, and
            # unconditional, so nothing above can strand the container.
            daemon.stop()
    logger.info(
        "done — copied %d, failed %d, already current %d, skipped %d",
        totals.copied,
        totals.failed,
        totals.already_current,
        totals.skipped,
    )
    if totals.source_gone:
        # An aggregate beside the per-object lines. Without a count, a night
        # where a whole class of rows is missing from MinIO — a layout fault
        # the preflight's sample can miss — headlines as "nothing new to
        # copy" while thousands were passed over.
        logger.error(
            "source gone: %d object(s) listed in Postgres have no bytes in "
            "MinIO. They are NOT backed up and nothing here can copy them. "
            "Each is named under `source_gone` in the run report under _runs/ "
            "on Box.",
            totals.source_gone,
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
            "in the `skipping` lines above.\n"
            "THIS IS THE ONLY NIGHT THAT WILL SAY SO. The run is still "
            "recorded clean, deliberately: holding the watermark for a name "
            "nothing can fix on its own would make every later night re-read "
            "the whole table. The report on Box is the durable record — read "
            "it, and rename them at the source.",
            name_skips,
            SKIPPED_NAME_MARKER,
        )
    if totals.verify_mismatched:
        logger.error(
            "%d of %d verified object(s) were missing or the wrong size on "
            "Box. Every one is named in the run report under _runs/ on Box, "
            "and in the ERROR lines above.\n"
            "This run changed NOTHING to compensate: the ledger still records "
            "them as mirrored, so later runs will skip them and this warning "
            "will not repeat. Putting them back needs a person — see "
            "'What verification does, and does not, prove' in the wiki. "
            "The run fails (exit 4) so this reaches you, but the watermark "
            "is NOT held: holding it bought exactly one night — the next run "
            "finds the object already current, never re-checks it, records "
            "clean and advances anyway.",
            totals.verify_mismatched,
            totals.verify_checked,
        )
    elif totals.verify_checked and not totals.verify_unverified:
        logger.info(
            "verified %d object(s) on Box, all present and correct",
            totals.verify_checked,
        )
    if totals.verify_unverified:
        # Any shortfall, not only a total blackout: 2 answers out of 50 is a
        # night whose "2 verified" headline claims far more than was
        # established. Still not a failure — each copy was confirmed as it was
        # made, so an unanswered stat is evidence of nothing and must not move
        # the watermark. Said out loud instead, because the count above
        # otherwise reads as a clean bill of health.
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
            VERIFY_INCOMPLETE_MARKER,
            totals.verify_checked,
            sampled,
            totals.verify_unverified,
        )
    if totals.failed:
        logger.error(
            "%d object(s) failed after %d attempts each — re-run to retry them",
            totals.failed,
            MAX_ATTEMPTS,
        )
    code = exit_code(
        failed=totals.failed,
        verify_mismatched=totals.verify_mismatched,
        stopped=stopping.stopping(),
        collisions=totals.collisions,
        ledger_flag=totals.ledger_flag,
    )
    # Same verdict the report already carries; printed here because the log is
    # the faster route when the connection does survive.
    emit_status(
        _status_for(code, outcome, stopped=stopping.stopping()),
        _flags_for(totals),
        stats,
    )
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
    # Rows Postgres lists whose bytes are not in MinIO. Counted apart from
    # `failed` on purpose: nothing can ever copy them, so counting them as
    # failures would hold the watermark for good and make every later night
    # re-read the whole table. Same treatment, and the same reason, as a
    # filename Box cannot store.
    source_gone: int = 0
    # Which way the ledger upload went, if it did not go cleanly. Set by
    # publish_ledger, read by _flags_for — the two conditions are
    # opposites and the summary must tell them apart.
    ledger_flag: str | None = None
    failures: list = field(default_factory=list)
    gone: list = field(default_factory=list)
    # Objects refused before any copy was attempted, and objects the check
    # found missing from Box. Both end up in the run report on Box, because
    # both name an object that is NOT backed up and the job log is a GitHub
    # Actions log under retention — the one place that answers "which one?"
    # must outlive it.
    skips: list = field(default_factory=list)
    name_skips: list = field(default_factory=list)
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
    ledger_flag: str | None = None,
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
    needs a person, and the run report names it under `verify_failures`.
    """
    if failed:
        return 1
    if verify_mismatched:
        return 4
    # Its own code rather than 4, because the two need opposite things done.
    # A mismatch is about an object the ledger says is on Box and is not; a
    # collision is about an object that never got there, whose path is held
    # by a different object's row. Only a rename in Supabase clears it, and
    # renaming the wrong one of the pair leaves the refused object refused
    # for ever.
    if collisions:
        return 5
    # The ledger's Box copy is not what it should be — either stale or ahead
    # of this host. Its own code, and non-zero deliberately: every other route
    # to a human is a notice inside a SUCCESSFUL run's summary, which notifies
    # nobody. This is the one condition that silently erodes the record that
    # makes a re-seed unnecessary, so it is worth a red tick and an email even
    # though every object copied fine. The run still records `ok`, so the
    # watermark is unaffected — the exit code and the watermark are separate.
    if ledger_flag:
        return 6
    # 3 is the documented "interrupted; progress is in the ledger and the
    # next run resumes". A signal handler means SIGINT no longer raises
    # KeyboardInterrupt, so without this a stopped run reports the clean 0
    # of one that finished everything. Ranked last, so a condition worth a
    # person's attention keeps its own code. The headline does NOT come from
    # here: `_status_for` takes `stopped` directly, because "which number do
    # we exit with" and "what happened" are different questions.
    if stopped:
        return 3
    return 0


def run_outcome(
    *,
    crashed: bool,
    failed: int,
    copied: int,
    limit: int | None,
    bucket_scoped: bool = False,
    stopped: bool = False,
    collisions: int = 0,
    gone: int = 0,
) -> str:
    """Classify a finished run — and decide whether it can be a watermark.

    `Ledger.last_successful_run()` only considers runs recorded `ok`, so this
    is what stops a run from losing objects: a run may only be `ok` if it saw
    the whole table and everything it did was sound. Recording one clean that
    did not makes the next run filter on its start time and skip whatever was
    left behind — permanently, and without saying so.

    Never `ok`, because the run did not see the whole table:

    - cut short by `--limit`
    - asked to stop
    - scoped by `--buckets`, which would advance the watermark for the
      buckets it never looked at (the wiki's smoke test is bucket-scoped,
      one edit away from dropping the `--limit` that saves it)
    - a refused collision: nothing mirrors that object until a person renames
      one of the pair, and an object nobody knows is missing is worse than a
      slow night

    Two conditions deliberately do NOT hold the watermark, and both fail
    loudly instead — exit 4 or a summary notice, with the object named in the
    run report on Box, which outlives the job log:

    - verification found objects missing. Holding it buys one night: the next
      run finds the object `already_current`, never re-copies or re-checks it,
      records itself clean, and the watermark advances anyway.
    - a filename Box cannot store. Nothing on this side can ever clear it, so
      holding it would freeze the watermark for good and make every night
      re-read all eight million rows inside a 240-minute job.

    `gone` is a parameter for one reason: those objects spend a `--limit` slot
    without reaching `copied`, so truncation has to count them or a chunk that
    stopped early reads as one that finished.
    """
    if crashed:
        return "error"
    # `copied + gone`, not `copied`: --limit is spent on PLANNED copies, and
    # a row whose bytes are not in MinIO spends a slot without landing in
    # `copied`. Counting only `copied`, a chunk that used its whole limit but
    # met one dead row looks unfinished, records `ok`, and moves the
    # watermark past every row it never reached.
    truncated = limit is not None and copied + gone >= limit
    if failed or truncated or bucket_scoped or stopped or collisions:
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
    name_skips: list | None = None,
    source_gone: list | None = None,
    verify_failures: list | None = None,
    status: str = "",
    flags: tuple = (),
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
        minio_bucket=args.minio_bucket,
        minio_prefix=args.minio_prefix,
        # Named in the report as well as the lock: the lock is blanked when
        # the run ends, and the summary comes looking afterwards.
        actions_run=os.environ.get(ACTIONS_RUN_ENV, ""),
        stats=stats,
        skips=list(skips or []),
        name_skips=list(name_skips or []),
        source_gone=list(source_gone or []),
        verify_failures=list(verify_failures or []),
        status=status,
        flags=list(flags),
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
        logger.error("run report stayed local at %s — upload failed: %s", local, exc)


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
        logger.error("cannot read %s: %s — %s", local, exc, LEDGER_STALE_MARKER)
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
                LEDGER_AHEAD_MARKER,
                destination,
                lib.format_bytes(remote_size),
                lib.format_bytes(local_size),
            )
            return "ledger_ahead"
        client.copy_file(dock.STATE_MOUNT, report.LEDGER_FILENAME, box_fs, destination)
        logger.info("ledger on Box: %s (%s)", destination, lib.format_bytes(local_size))
    except Exception as exc:
        # Deliberately broad: this runs in the cleanup path, and anything
        # raised here would skip the container teardown below it.
        logger.error(
            "ledger stayed on the host only — upload failed: %s — %s",
            exc,
            LEDGER_STALE_MARKER,
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
        totals.copied,
        totals.already_current,
        totals.skipped,
    )
    if totals.collisions:
        report_collisions(totals.collisions)
    name_skips = totals.skipped - totals.collisions
    if name_skips > 0:
        logger.error(
            "%d %s Box cannot store. A real run would refuse them too.",
            name_skips,
            SKIPPED_NAME_MARKER,
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
                entry = f"{lib.loggable(path)}: {refused.reason}"
                totals.skips.append(entry)
                # Named apart as well as counted. A collision is recoverable
                # by renaming either of the pair; a name Box cannot store is
                # not, and the watermark advances past it — so the report is
                # the only record that it exists, and it must not be crowded
                # out of a shared cap by collisions.
                if not refused.collision:
                    totals.name_skips.append(entry)
        totals.collisions += plan.collisions
        totals.already_current += plan.already_current
        if not plan.copies:
            continue
        logger.info(
            "batch: %d object(s), %s",
            len(plan.copies),
            lib.format_bytes(plan.total_bytes),
        )
        copied, failed, gone = copy_all(
            client,
            plan,
            minio,
            box_fs,
            args.box_root,
            ledger,
            args.workers,
            failures=totals.failures,
            gone=totals.gone,
            succeeded=totals.verify_pool,
        )
        totals.copied += copied
        totals.failed += failed
        totals.source_gone += gone
        # The reservoir is offered every successful copy inside copy_all.


def minio_source_from_env(args: argparse.Namespace) -> MinioSource:
    access = os.environ.get("MINIO_ROOT_USER", "")
    secret = args.minio_secret
    if not access or not secret:
        raise lib.BackupError(
            "MINIO_ROOT_USER / MINIO_ROOT_PASSWORD missing — the service reads "
            "them from the deploy's .env file; export them for a manual run"
        )
    if not args.minio_bucket.strip():
        raise lib.BackupError(
            "OBJECT_BACKUP_MINIO_BUCKET is empty. It must name the single MinIO "
            "bucket storage-api writes into (STORAGE_S3_BUCKET in the compose "
            "file). Left empty, rclone reads each object's own bucket_id as a "
            "bucket name and every copy 404s."
        )
    return MinioSource(
        endpoint=os.environ.get(
            "OBJECT_BACKUP_MINIO_ENDPOINT", "http://supabase-minio:9000"
        ),
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


def check_destination(ledger: Ledger, destination: str) -> None:
    """Refuse to run a ledger against a Box folder it did not fill.

    The ledger says which objects are already mirrored. It does not say
    WHERE, so pointed at a different folder it answers "already copied" about
    a folder that is empty — and the run reports "nothing new to copy
    (8,013,796 already on Box)" against nothing, every night, for ever. One
    mistyped character during a hand-run seed is enough.

    Refusing rather than flagging, because the flagged version still writes:
    the night would go on filling the wrong folder while recording it in the
    same ledger, and after that there is no run that can tell which of the two
    folders any given row means.
    """
    recorded = ledger.destination()
    if recorded is None or recorded == destination:
        return
    raise lib.BackupError(
        "this ledger records objects mirrored to a different place on Box:\n"
        f"    recorded:  {lib.loggable(recorded)}\n"
        f"    requested: {lib.loggable(destination)}\n"
        "It tracks WHICH objects are already copied, not where, so running it "
        "against another folder would report millions of objects as already "
        "backed up while that folder stays empty.\n"
        "If OBJECT_BACKUP_BOX_ROOT or OBJECT_BACKUP_BOX_REMOTE was mistyped, correct it. If "
        "the mirror is genuinely moving, move the folder on Box and keep the "
        "recorded value, or point OBJECT_BACKUP_STATE_DIR at a new directory and "
        "seed the new location from scratch."
    )


def check_box_root(args: argparse.Namespace) -> None:
    """Refuse a destination that would scatter the mirror across Box.

    `--box-root` defaults to empty, and an empty root means `box_path` returns
    a bare `<bucket>/<name>` — so a manual seed launched without
    OBJECT_BACKUP_BOX_ROOT exported writes eight million objects, plus `_runs/`,
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
            "OBJECT_BACKUP_BOX_ROOT is empty. Set it to the folder on Box this "
            "environment mirrors into, e.g.\n"
            f"    export OBJECT_BACKUP_BOX_ROOT=Bloom-Backups/BloomV2-Data-Backup/{args.env}/storage\n"
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
                    minio.root(),
                    len(tried),
                    len(samples),
                )
                return
        except RcloneError as exc:
            # rclone names the remote it failed on, so this is a path.
            errors.append(lib.loggable(str(exc)))
    listed = "\n".join(f"    {lib.loggable(path)}" for path in tried)
    detail = "\nErrors: " + "; ".join(errors) if errors else ""
    raise lib.BackupError(
        f"preflight failed: none of {len(tried)} sampled object(s) is in MinIO "
        "where this job expects it:\n"
        f"{listed}\n"
        "Postgres lists them but MinIO does not hold them there. Check "
        "OBJECT_BACKUP_MINIO_BUCKET and OBJECT_BACKUP_MINIO_PREFIX against one real key:\n"
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


# The readiness poll gets its own timeout. RcloneRC defaults to 900 seconds,
# which is right for a single large object over a slow Box link and absurd for
# "are you listening yet" — thirty attempts of it is up to seven and a half
# hours, spent holding the run lock, in a job with a four-hour limit.
DAEMON_READY_TIMEOUT_SECONDS = 10


def wait_for_daemon(daemon: dock.RcDaemon, attempts: int = 30) -> RcloneRC:
    """Poll rc/noop until the daemon answers, so the first copy isn't a race."""
    poll = RcloneRC(
        daemon.url,
        daemon.user,
        daemon.password,
        timeout=DAEMON_READY_TIMEOUT_SECONDS,
    )
    for attempt in range(attempts):
        # Checked between attempts, like every other loop in the run: a
        # stop arriving while the daemon starts must not wait out the poll.
        if stopping.stopping():
            raise lib.Stopped("stopped while waiting for the rclone daemon")
        try:
            poll.noop()
            logger.info("rclone daemon ready (%s)", poll.version())
            # The long timeout is what the copies want; only the poll was
            # ever meant to be impatient.
            return RcloneRC(daemon.url, daemon.user, daemon.password)
        except RcloneError:
            time.sleep(0.5)
    raise lib.BackupError(
        "rclone daemon never became ready. Container logs:\n"
        + dock.daemon_logs(daemon.container)
    )


def report_collisions(count: int) -> None:
    """The line a run prints when it refused objects, real or dry.

    What the summary branches on is the `collisions` flag, not this text —
    the phrase greps are gone, because an object name can contain any phrase.
    This is the line that says WHICH objects and what to do about them, which
    the flag cannot carry. Shared with the dry run deliberately: a dry run is
    what an operator runs first, and it was the one path where a refused
    collision stayed invisible.
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
            lib.loggable(path),
            skipped.reason,
        )


if __name__ == "__main__":
    sys.exit(main())
