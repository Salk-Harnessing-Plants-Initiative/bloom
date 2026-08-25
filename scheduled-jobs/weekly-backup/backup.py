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

See `.env.{staging,prod}.defaults` for the BACKUP_* config surface, and
_WIKI/SCHEDULEDJOBS/weekly-backup.md for setup and restore.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("bloom_weekly_backup")

COMPOSE_FILE = "docker-compose.prod.yml"
# Under the invoking user's own home, so no root-created directory is needed.
DEFAULT_STATE_DIR = "~/.local/state/bloom-weekly-backup"
DB_SERVICE = "db-prod"

# Size floors. An empty or truncated dump is the failure this job exists to
# avoid reporting as success; both floors sit far below any real dump.
MIN_DATABASE_BYTES = 4096
MIN_GLOBALS_BYTES = 256

EXIT_OK = 0
EXIT_SUBPROCESS = 1
EXIT_CONFIG = 2


class ConfigError(RuntimeError):
    """Environment or host is not set up for this job to run."""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def load_env_file(path: Path) -> dict[str, str]:
    """Read a deploy .env file into a plain dict.

    systemd read this file for us; over SSH nothing does, and sourcing it in the
    shell would let a value containing spaces or quotes rewrite the command.
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
        raise ConfigError(f"expected artifact was never written: {path.name}")
    size = path.stat().st_size
    if size < min_bytes:
        raise ConfigError(
            f"{path.name} is {size} bytes, below the {min_bytes}-byte floor — "
            "treating as a failed dump rather than uploading it"
        )
    _run([_which("gzip"), "-t", str(path)])
    logger.info("verified %s: %d bytes", path.name, size)
    return size


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


def backup_destination(env_name: str) -> tuple[str, str]:
    """The remote and directory this environment's artifacts belong in."""
    remote = _env("BACKUP_RCLONE_REMOTE", "")
    if not remote:
        raise ConfigError("BACKUP_RCLONE_REMOTE is not set")
    return remote, _env("BACKUP_RCLONE_DEST_DIR", f"bloom-backups/{env_name}")


def upload(artifacts: list[Path], env_name: str) -> None:
    """Push verified artifacts to this environment's Box destination."""
    remote, dest_dir = backup_destination(env_name)
    logger.info("uploading %d artifact(s) to %s:%s/", len(artifacts), remote, dest_dir)
    for artifact in artifacts:
        _run([_which("rclone"), "copy", str(artifact), f"{remote}:{dest_dir}/"])


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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    logger.info("starting bloom-weekly-backup env=%s timestamp=%s", args.env, timestamp)

    try:
        deploy_dir = args.deploy_dir.resolve()
        if not deploy_dir.is_dir():
            raise ConfigError(f"deploy directory does not exist: {deploy_dir}")
        apply_env_file(args.env_file or deploy_dir / f".env.{args.env}")
        state_dir = Path(_env("BACKUP_STATE_DIR", DEFAULT_STATE_DIR)).expanduser()
        state_dir.mkdir(parents=True, exist_ok=True)
        # The working copy holds a full dump; keep it off other users.
        state_dir.chmod(0o700)
    except ConfigError as exc:
        logger.error("configuration error: %s", exc)
        return EXIT_CONFIG

    # The working copy holds a full dump; the context manager removes it on
    # every exit path, success or failure.
    with tempfile.TemporaryDirectory(prefix="bloom-backup-", dir=str(state_dir)) as tmp:
        work_dir = Path(tmp)
        try:
            container = resolve_container(deploy_dir, args.env)
            artifacts = [
                dump_database(container, work_dir, timestamp),
                dump_globals(container, work_dir, timestamp),
            ]
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
            upload(artifacts, args.env)
        except ConfigError as exc:
            logger.error("configuration error: %s", exc)
            return EXIT_CONFIG
        except subprocess.CalledProcessError as exc:
            logger.error("upload failed: %s", exc)
            return EXIT_SUBPROCESS

        remote, dest_dir = backup_destination(args.env)
        print(format_summary(args.env, timestamp, artifacts,
                             f"{remote}:{dest_dir}/", uploaded=True))

    logger.info("bloom-weekly-backup env=%s timestamp=%s complete", args.env, timestamp)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
