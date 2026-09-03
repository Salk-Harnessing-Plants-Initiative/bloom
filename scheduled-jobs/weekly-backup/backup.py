#!/usr/bin/env python3
"""Weekly backup of Bloom's Supabase Postgres database to Box.

Runs from the weekly-backup GitHub Actions workflow, which SSHes to the deploy
host and invokes this script there. Dumps the
database and the global role definitions out of the running db-prod container,
verifies both artifacts, and uploads them to Box via a pre-configured rclone
remote.

This job only ever writes to Box. It never deletes there — old backups are kept
until someone removes them by hand.

Exit codes:
  0 = verified backup uploaded
  1 = subprocess failure (docker / pg_dump / gzip / rclone)
  2 = configuration error (missing env, no remote, stack not running)
  3 = an artifact failed verification (missing, undersized, corrupt, or empty)
  4 = the run was terminated by a signal (cancelled from the Actions tab, or
      timed out) — not a failure of anything it ran

See `.env.{staging,prod}.defaults` for the BACKUP_* config surface, and
_WIKI/SCHEDULEDJOBS/weekly-backup.md for setup.
"""

from __future__ import annotations

import argparse
import gzip
import logging
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("bloom_weekly_backup")

COMPOSE_FILE = "docker-compose.prod.yml"
# The only environment this job backs up. COMPOSE_FILE pins the production
# compose project name, so every other value resolves the production container
# while reading some other environment's env file.
BACKUP_ENV = "prod"
# Under the invoking user's own home, so no root-created directory is needed.
DEFAULT_STATE_DIR = "~/.local/state/bloom-weekly-backup"
DB_SERVICE = "db-prod"

# Size floors. An empty or truncated dump is the failure this job exists to
# avoid reporting as success; both floors sit far below any real dump.
MIN_DATABASE_BYTES = 4096
MIN_GLOBALS_BYTES = 256

# Content floors. A dump can be well-formed, correctly sized and cleanly
# gzipped while holding no rows at all — the shape a dump takes if the role
# taking it ever loses its RLS-bypass privilege. Size alone never catches that.
DB_DUMP_COMPLETE_MARKER = "-- PostgreSQL database dump complete"
GLOBALS_DUMP_COMPLETE_MARKER = "-- PostgreSQL database cluster dump complete"
MIN_DATA_ROWS = 100
MIN_ROLE_STATEMENTS = 5

EXIT_OK = 0
EXIT_SUBPROCESS = 1
EXIT_CONFIG = 2
EXIT_VERIFY = 3
EXIT_SIGNAL = 4


class ConfigError(RuntimeError):
    """Environment or host is not set up for this job to run."""


class VerificationError(RuntimeError):
    """An artifact was produced but cannot be a usable dump."""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def load_env_file(path: Path) -> dict[str, str]:
    """Read a deploy .env file into a plain dict.

    A systemd unit would have read this file for us; over SSH nothing does,
    and sourcing it in the
    shell would let a value containing spaces or quotes rewrite the command.

    Deliberately not a shell: `export ` prefixes, inline `#` comments and
    values spanning several lines are all read literally. The deploy env files
    use none of them, and guessing at them would corrupt a value containing a
    `#` far more quietly than refusing to.
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


def apply_env_file(path: Path) -> int:
    """Load the env file as defaults. A real environment variable still wins."""
    if not path.is_file():
        raise ConfigError(f"env file not found: {path}")
    values = load_env_file(path)
    for key, value in values.items():
        os.environ.setdefault(key, value)
    logger.info("loaded %d values from %s", len(values), path.name)
    return len(values)


def _which(name: str) -> str:
    """Resolve a binary, raising a config error rather than a traceback."""
    found = shutil.which(name)
    if not found:
        raise ConfigError(f"required binary not on PATH: {name}")
    return found

def _run(cmd: list[str], cwd: Path | None = None) -> str:
    """Run a command, log its output to the journal, raise on non-zero exit."""
    logger.info("running: %s", " ".join(cmd))
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    for line in (result.stderr or "").rstrip().splitlines():
        logger.warning("  stderr: %s", line)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, cmd, output=result.stdout, stderr=result.stderr
        )
    return result.stdout

def compose_args(deploy_dir: Path, env_name: str) -> list[str]:
    """The compose invocation this environment's stack was brought up with."""
    return [
        "-f", str(deploy_dir / COMPOSE_FILE),
        "--env-file", str(deploy_dir / f".env.{env_name}"),
    ]

def parse_container_id(ps_output: str) -> str:
    """Pick the container id out of `compose ps -q` output.

    Empty output means the stack is not running, which must be a hard error —
    a backup of nothing is the one result worse than no backup at all.
    """
    ids = [line.strip() for line in ps_output.splitlines() if line.strip()]
    if not ids:
        raise ConfigError(
            f"no running '{DB_SERVICE}' container — is the stack up in this deploy directory?"
        )
    if len(ids) > 1:
        raise ConfigError(f"expected one '{DB_SERVICE}' container, found {len(ids)}")
    return ids[0]


def resolve_container(deploy_dir: Path, env_name: str) -> str:
    """Locate the database container through compose.

    Never by name: compose derives its project from the deploy directory, which
    differs per host, so a hardcoded container name is wrong somewhere.
    """
    out = _run(
        [_which("docker"), "compose", *compose_args(deploy_dir, env_name), "ps", "-q", DB_SERVICE],
        cwd=deploy_dir,
    )
    container = parse_container_id(out)
    logger.info("resolved %s container: %s", DB_SERVICE, container[:12])
    return container

# ---------------------------------------------------------------------------
# Dump + verify
# ---------------------------------------------------------------------------

def _terminate(signum: int, _frame: object) -> None:
    """Turn a kill signal into an exception so the working dir unwinds.

    Its own exit code: a SystemExit carrying a string exits 1, which would be
    indistinguishable from a pg_dump or rclone failure. Being cancelled is not
    a failure of anything this job ran.
    """
    logger.error("terminated by signal %d", signum)
    raise SystemExit(EXIT_SIGNAL)


def _state_dir() -> Path:
    """Where working copies live. BACKUP_STATE_DIR is read from the env file."""
    return Path(_env("BACKUP_STATE_DIR", DEFAULT_STATE_DIR)).expanduser()


def sweep_best_effort() -> int:
    """Sweep on a path that is already failing, without adding a new failure.

    A run that dies in config loading returns before the sweep below, so a
    persistent config error (renamed deploy dir, unreadable env file) would let
    an orphaned plaintext dump — `auth.users` and all — outlive the one-week
    bound this job promises.
    """
    try:
        state_dir = _state_dir()
        return sweep_stale_work_dirs(state_dir) if state_dir.is_dir() else 0
    except OSError as exc:
        logger.warning("could not sweep stale working directories: %s", exc)
        return 0


def sweep_stale_work_dirs(state_dir: Path) -> int:
    """Remove dumps left by a run that was killed before its cleanup ran."""
    stale = list(state_dir.glob("bloom-backup-*"))
    for leftover in stale:
        shutil.rmtree(leftover, ignore_errors=True)
    if stale:
        logger.warning("removed %d working dir(s) left by an interrupted run",
                       len(stale))
    return len(stale)


def _stream_to_gzip(cmd: list[str], out: Path) -> None:
    """Run cmd, pipe it through gzip into out, and check BOTH exit statuses.

    A shell pipeline reports only the last process, which is how a truncated
    dump wrapped in valid gzip passes for a good backup.
    """
    with out.open("wb") as handle:
        gzip_proc = subprocess.Popen(
            [_which("gzip"), "-c"], stdin=subprocess.PIPE, stdout=handle
        )
        src_proc = subprocess.Popen(cmd, stdout=gzip_proc.stdin, stderr=subprocess.PIPE)
        gzip_proc.stdin.close()  # type: ignore[union-attr]
        _, src_err = src_proc.communicate()
        gzip_proc.wait()
    for line in (src_err or b"").decode(errors="replace").rstrip().splitlines():
        logger.warning("  stderr: %s", line)
    if src_proc.returncode != 0:
        raise subprocess.CalledProcessError(src_proc.returncode, cmd, stderr=src_err)
    if gzip_proc.returncode != 0:
        raise subprocess.CalledProcessError(gzip_proc.returncode, ["gzip"])


def verify_artifact(path: Path, min_bytes: int) -> int:
    """Reject an artifact that cannot be a real dump. Returns its size."""
    if not path.exists():
        raise VerificationError(f"expected artifact was never written: {path.name}")
    size = path.stat().st_size
    if size < min_bytes:
        raise VerificationError(
            f"{path.name} is {size} bytes, below the {min_bytes}-byte floor — "
            "treating as a failed dump rather than uploading it"
        )
    try:
        _run([_which("gzip"), "-t", str(path)])
    except subprocess.CalledProcessError as exc:
        # Same symptom as the checks above; reporting it as a subprocess
        # failure would send the operator looking at docker instead.
        raise VerificationError(f"{path.name} failed its gzip integrity check") from exc
    logger.info("verified %s: %d bytes", path.name, size)
    return size


def scan_plain_dump(path: Path, marker: str) -> tuple[int, int, bool]:
    """Read a gzipped plain dump once: data rows, CREATE ROLEs, did it finish.

    pg_dump writes its completion line last, so seeing that line — outside any
    COPY block, where a data row could otherwise forge it — is what proves the
    dump ran to the end rather than stopping partway.
    """
    rows = 0
    roles = 0
    completed = False
    in_copy = False
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if in_copy:
                if line.startswith("\\."):
                    in_copy = False
                else:
                    rows += 1
                continue
            if line.startswith("COPY ") and line.rstrip().endswith("FROM stdin;"):
                in_copy = True
            elif line.startswith("CREATE ROLE "):
                roles += 1
            elif line.startswith(marker):
                completed = True
    return rows, roles, completed


def verify_database_content(path: Path) -> int:
    """Reject a database dump that cannot hold this database's contents.

    The size floor and `gzip -t` both pass on a dump whose every table came out
    empty. Returns the number of data rows seen.
    """
    rows, _, completed = scan_plain_dump(path, DB_DUMP_COMPLETE_MARKER)
    if not completed:
        raise VerificationError(
            f"{path.name} never reached its '{DB_DUMP_COMPLETE_MARKER}' line — "
            "the dump stopped partway"
        )
    if rows < MIN_DATA_ROWS:
        raise VerificationError(
            f"{path.name} holds {rows} data row(s), below the {MIN_DATA_ROWS}-row "
            "floor — a dump this empty means the role taking it could not read "
            "the tables, not that the database is empty"
        )
    logger.info("verified %s content: %d data row(s)", path.name, rows)
    return rows


def verify_globals_content(path: Path) -> int:
    """Reject a globals dump with no roles in it. Returns the role count.

    The database dump's OWNER and GRANT statements name these roles; a globals
    artifact without them restores to a cluster that cannot own its own data.
    """
    _, roles, completed = scan_plain_dump(path, GLOBALS_DUMP_COMPLETE_MARKER)
    if not completed:
        raise VerificationError(
            f"{path.name} never reached its '{GLOBALS_DUMP_COMPLETE_MARKER}' line — "
            "the dump stopped partway"
        )
    if roles < MIN_ROLE_STATEMENTS:
        raise VerificationError(
            f"{path.name} defines {roles} role(s), below the {MIN_ROLE_STATEMENTS} "
            "this cluster always has — the database dump's OWNER and GRANT "
            "statements would have nothing to bind to"
        )
    logger.info("verified %s content: %d role(s)", path.name, roles)
    return roles


def dump_database(container: str, work_dir: Path, timestamp: str) -> Path:
    """Dump the whole database, keeping owners and privileges."""
    pg_user = _env("POSTGRES_USER", "supabase_admin")
    pg_db = _env("POSTGRES_DB", "postgres")
    out = work_dir / f"postgres-{pg_db}-{timestamp}.sql.gz"
    logger.info("dumping database %s -> %s", pg_db, out.name)
    _stream_to_gzip(
        [_which("docker"), "exec", "-i", container,
         "pg_dump", "-U", pg_user, "-d", pg_db, "--format=plain"],
        out,
    )
    verify_artifact(out, MIN_DATABASE_BYTES)
    verify_database_content(out)
    return out


def dump_globals(container: str, work_dir: Path, timestamp: str) -> Path:
    """Dump the roles the database dump's OWNER/GRANT statements reference."""
    pg_user = _env("POSTGRES_USER", "supabase_admin")
    out = work_dir / f"globals-{timestamp}.sql.gz"
    logger.info("dumping globals -> %s", out.name)
    _stream_to_gzip(
        [_which("docker"), "exec", "-i", container,
         "pg_dumpall", "-U", pg_user, "--globals-only"],
        out,
    )
    verify_artifact(out, MIN_GLOBALS_BYTES)
    verify_globals_content(out)
    return out


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


def format_summary(env_name: str, timestamp: str, artifacts: list[Path],
                   destination: str, uploaded: bool) -> str:
    """A short human-readable record of the run, for the weekly glance."""
    lines = [
        f"env: {env_name}",
        f"run: {timestamp}",
        f"destination: {destination}" if uploaded else "destination: (dry run — not uploaded)",
        "artifacts:",
    ]
    for artifact in artifacts:
        size = artifact.stat().st_size if artifact.exists() else 0
        lines.append(f"  {artifact.name}  {size:,} bytes")
    return "\n".join(lines)


# Retry the upload rather than lose a verified dump to a blip; rclone's own
# backoff, so nothing here re-implements one.
RCLONE_RETRY_ARGS = ["--retries", "5", "--retries-sleep", "30s",
                     "--low-level-retries", "20"]


def backup_destination(env_name: str) -> tuple[str, str]:
    """The remote and directory this environment's artifacts belong in."""
    remote = _env("BACKUP_RCLONE_REMOTE", "")
    if not remote:
        raise ConfigError("BACKUP_RCLONE_REMOTE is not set")
    return remote, _env("BACKUP_RCLONE_DEST_DIR", f"bloom-backups/{env_name}")


def upload(artifacts: list[Path], env_name: str, timestamp: str) -> str:
    """Push this run's artifacts to Box under one folder. Returns that folder.

    One copy of the working directory rather than one per file: a run that
    fails halfway then leaves an obviously incomplete folder, not a database
    dump sitting among good backups with no globals beside it.
    """
    remote, dest_dir = backup_destination(env_name)
    work_dirs = {artifact.parent for artifact in artifacts}
    if len(work_dirs) != 1:
        raise ConfigError(f"artifacts span {len(work_dirs)} directories, expected one")
    destination = f"{remote}:{dest_dir}/{timestamp}/"
    logger.info("uploading %d artifact(s) to %s", len(artifacts), destination)
    # A transient blip on the way to Box would otherwise discard a dump that is
    # already taken and verified, and the next attempt is a week away.
    _run([_which("rclone"), "copy", str(work_dirs.pop()), destination, *RCLONE_RETRY_ARGS])
    return destination


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Weekly Postgres backup to Box.")
    parser.add_argument("--env", required=True, choices=["staging", "prod"],
                        help="Which deploy environment to back up.")
    parser.add_argument("--deploy-dir", required=True, type=Path,
                        help="Deploy directory holding the compose file and env file.")
    parser.add_argument("--env-file", type=Path, default=None,
                        help="Env file to read config from (default: <deploy-dir>/.env.<env>).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Dump and verify, but skip the upload.")
    parser.add_argument("--print-destination", action="store_true",
                        help="Print this environment's Box destination and exit.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    logger.info("starting bloom-weekly-backup env=%s timestamp=%s", args.env, timestamp)

    try:
        if args.env != BACKUP_ENV:
            # The workflow stopped offering this, but the script is what someone
            # rehearsing by hand actually runs. Under any other --env it reads
            # that environment's env file and still resolves the PRODUCTION
            # container, so it would upload a full prod dump — auth.users and
            # all — into the other environment's Box folder, logging the wrong
            # name at every line.
            raise ConfigError(
                f"--env {args.env} is refused: {COMPOSE_FILE} pins the production "
                f"compose project, so a '{args.env}' run dumps production while "
                f"reading {args.env}'s configuration. Rehearse with "
                f"'--env {BACKUP_ENV} --dry-run' instead."
            )
        deploy_dir = args.deploy_dir.resolve()
        if not deploy_dir.is_dir():
            raise ConfigError(f"deploy directory does not exist: {deploy_dir}")
        apply_env_file(args.env_file or deploy_dir / f".env.{args.env}")
        if args.print_destination:
            # So the workflow never re-implements this parsing in shell.
            remote, dest_dir = backup_destination(args.env)
            print(f"{remote}:{dest_dir}")
            return EXIT_OK
        state_dir = _state_dir()
        # The working copy holds a full dump; keep it off other users. mode= on
        # the create leaves no window between the two calls; the chmod is what
        # tightens a directory an earlier run left looser.
        state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        state_dir.chmod(0o700)
        # Cancelling the job or hitting timeout-minutes kills this process from
        # outside; without a handler the working dir below is never unwound.
        signal.signal(signal.SIGTERM, _terminate)
        signal.signal(signal.SIGHUP, _terminate)
        # SIGKILL and power loss cannot be caught, so sweep what they left.
        sweep_stale_work_dirs(state_dir)
        # Resolve the destination before dumping: finding out afterwards costs
        # the whole dump window and discards the artifact.
        if not args.dry_run:
            _which("rclone")
            backup_destination(args.env)
    except ConfigError as exc:
        logger.error("configuration error: %s", exc)
        # This run is over, but a dump orphaned by an earlier SIGKILL must not
        # outlive it just because the config is broken this week too.
        sweep_best_effort()
        return EXIT_CONFIG

    # The working copy holds a full dump. The context manager covers returns
    # and exceptions; signals are covered by the handlers installed above.
    with tempfile.TemporaryDirectory(prefix="bloom-backup-", dir=str(state_dir)) as tmp:
        work_dir = Path(tmp)
        try:
            container = resolve_container(deploy_dir, args.env)
            artifacts = [
                dump_database(container, work_dir, timestamp),
                dump_globals(container, work_dir, timestamp),
            ]
        except VerificationError as exc:
            logger.error("verification failed: %s", exc)
            return EXIT_VERIFY
        except ConfigError as exc:
            logger.error("configuration error: %s", exc)
            return EXIT_CONFIG
        except subprocess.CalledProcessError as exc:
            logger.error("dump failed: %s", exc)
            return EXIT_SUBPROCESS

        if args.dry_run:
            logger.info("DRY RUN — %d artifact(s) verified, skipping upload",
                        len(artifacts))
            print(format_summary(args.env, timestamp, artifacts, "", uploaded=False))
            return EXIT_OK

        try:
            destination = upload(artifacts, args.env, timestamp)
        except ConfigError as exc:
            logger.error("configuration error: %s", exc)
            return EXIT_CONFIG
        except subprocess.CalledProcessError as exc:
            logger.error("upload failed: %s", exc)
            return EXIT_SUBPROCESS

        print(format_summary(args.env, timestamp, artifacts,
                             destination, uploaded=True))

    logger.info("bloom-weekly-backup env=%s timestamp=%s complete", args.env, timestamp)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
