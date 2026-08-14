#!/usr/bin/env python3
"""Weekly backup of Bloom's Postgres database and MinIO buckets to Box.

Runs from a per-env systemd timer (see bloom-weekly-backup.timer). Reads the
deploy's `.env.<env>` for credentials, dumps Postgres with `pg_dump`, mirrors
the MinIO buckets with `mc`, tarballs the result with a timestamped name,
and pushes the bundle to Box via a pre-configured rclone remote. Local
working copy is removed once the upload succeeds; the rclone remote
applies retention (configurable, default keep last 8 weeks).

Exit codes:
  0 = clean backup uploaded
  1 = subprocess failure (pg_dump / mc / rclone)
  2 = configuration error (missing env, no rclone remote)
  3 = upload succeeded but retention prune failed (non-fatal but flagged)

See `.env.{staging,prod}.defaults` for the BACKUP_* config surface.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("bloom_weekly_backup")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _run(cmd: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    """Run a subprocess, stream output to the journal, raise on non-zero exit."""
    logger.info("running: %s", " ".join(cmd))
    result = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    if result.stdout:
        for line in result.stdout.rstrip().splitlines():
            logger.info("  stdout: %s", line)
    if result.stderr:
        for line in result.stderr.rstrip().splitlines():
            logger.warning("  stderr: %s", line)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, cmd, output=result.stdout, stderr=result.stderr
        )


def _which(name: str) -> str:
    """Resolve a binary, raise if missing — surfaces missing tooling early."""
    found = shutil.which(name)
    if not found:
        raise FileNotFoundError(f"required binary not on PATH: {name}")
    return found


# ---------------------------------------------------------------------------
# Backup steps
# ---------------------------------------------------------------------------

def dump_postgres(work_dir: Path, env_name: str) -> Path:
    """pg_dump the bloom Postgres database via the local container exec path.

    We exec into the running db-prod container rather than connecting from the
    host so we don't need to expose Postgres or duplicate credentials. The
    container always has its own pg client at the matching server version.
    """
    container = f"bloom_v2_{env_name}-db-prod-1"
    pg_user = _env("POSTGRES_USER", "supabase_admin")
    pg_db = _env("POSTGRES_DB", "postgres")
    out = work_dir / f"postgres-{pg_db}.sql.gz"
    logger.info("dumping Postgres from container %s -> %s", container, out.name)
    cmd = [
        _which("docker"), "exec", "-i", container,
        "pg_dump",
        "-U", pg_user,
        "-d", pg_db,
        "--format=plain",
        "--no-owner",
        "--no-privileges",
    ]
    # Stream pg_dump | gzip into the output file so we never materialize the
    # full uncompressed dump on disk.
    with out.open("wb") as f:
        gzip_proc = subprocess.Popen([_which("gzip"), "-c"], stdin=subprocess.PIPE, stdout=f)
        dump_proc = subprocess.Popen(cmd, stdout=gzip_proc.stdin, stderr=subprocess.PIPE)
        gzip_proc.stdin.close()  # type: ignore[union-attr]
        _, dump_err = dump_proc.communicate()
        gzip_proc.wait()
        if dump_proc.returncode != 0:
            raise subprocess.CalledProcessError(
                dump_proc.returncode, cmd, stderr=dump_err.decode()
            )
        if gzip_proc.returncode != 0:
            raise subprocess.CalledProcessError(gzip_proc.returncode, ["gzip"])
    logger.info("postgres dump: %s bytes", out.stat().st_size)
    return out


def mirror_minio(work_dir: Path, env_name: str) -> Path:
    """Snapshot the MinIO buckets using mc mirror.

    Iterates the buckets advertised in BACKUP_BUCKETS (comma-separated). Each
    bucket lands in its own subdirectory under <work_dir>/minio/<bucket>/.
    `mc mirror` is content-aware — re-runs are fast on unchanged data — but
    we always run with --overwrite so the snapshot is a true point-in-time
    copy, not an incremental.
    """
    buckets = [b.strip() for b in _env("BACKUP_BUCKETS", "images,videos,scrna").split(",") if b.strip()]
    minio_alias = _env("BACKUP_MC_ALIAS", "local")
    out = work_dir / "minio"
    out.mkdir(parents=True, exist_ok=True)
    logger.info("mirroring %d MinIO bucket(s): %s", len(buckets), buckets)
    for bucket in buckets:
        dest = out / bucket
        dest.mkdir(parents=True, exist_ok=True)
        _run([_which("mc"), "mirror", "--overwrite", f"{minio_alias}/{bucket}", str(dest)])
    return out


def make_tarball(work_dir: Path, env_name: str, timestamp: str) -> Path:
    """Wrap the postgres dump + minio mirror in one timestamped tarball."""
    tarball = work_dir.parent / f"bloom-{env_name}-backup-{timestamp}.tar.gz"
    logger.info("packing tarball: %s", tarball.name)
    with tarfile.open(tarball, "w:gz") as tf:
        # Add the entire work_dir, preserving the postgres-*.sql.gz and minio/<bucket>/ structure.
        for item in work_dir.iterdir():
            tf.add(item, arcname=item.name)
    logger.info("tarball: %s bytes", tarball.stat().st_size)
    return tarball


def upload_to_box(tarball: Path, env_name: str) -> None:
    """Push the tarball to the configured rclone Box remote."""
    remote = _env("BACKUP_RCLONE_REMOTE", "")
    if not remote:
        raise RuntimeError("BACKUP_RCLONE_REMOTE not set")
    dest_dir = _env("BACKUP_RCLONE_DEST_DIR", f"bloom-backups/{env_name}")
    logger.info("uploading to %s:%s/", remote, dest_dir)
    _run([
        _which("rclone"), "copy",
        str(tarball),
        f"{remote}:{dest_dir}/",
        "--progress",
    ])


def prune_old_backups(env_name: str, keep_weeks: int) -> None:
    """Delete remote tarballs older than keep_weeks weeks.

    Uses rclone's --min-age to skip the deletion check on recent files, then
    delete-older-than for the actual prune. Failure is non-fatal — we don't
    want a retention hiccup to mask the success of the actual backup.
    """
    remote = _env("BACKUP_RCLONE_REMOTE", "")
    dest_dir = _env("BACKUP_RCLONE_DEST_DIR", f"bloom-backups/{env_name}")
    min_age = f"{keep_weeks * 7}d"
    logger.info("pruning remote backups older than %s under %s:%s", min_age, remote, dest_dir)
    try:
        _run([
            _which("rclone"), "delete",
            f"{remote}:{dest_dir}/",
            "--min-age", min_age,
        ])
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        logger.error("retention prune failed: %s", exc)
        raise


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", required=True, choices=["staging", "prod"],
                        help="Which deploy environment to back up.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Build the tarball but skip the rclone upload.")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    keep_weeks = int(_env("BACKUP_RETENTION_WEEKS", "8"))
    if keep_weeks < 1:
        logger.error("BACKUP_RETENTION_WEEKS=%d is invalid (must be >= 1)", keep_weeks)
        return 2

    # State dir created by install.sh, mode 0700, owned by bloom-deploy.
    state_dir = Path(_env("BACKUP_STATE_DIR", "/var/lib/bloom-weekly-backup"))
    state_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    logger.info("starting bloom-weekly-backup env=%s timestamp=%s", args.env, timestamp)

    with tempfile.TemporaryDirectory(prefix="bloom-backup-", dir=str(state_dir)) as tmp:
        work_dir = Path(tmp) / "payload"
        work_dir.mkdir()
        try:
            dump_postgres(work_dir, args.env)
            mirror_minio(work_dir, args.env)
            tarball = make_tarball(work_dir, args.env, timestamp)
        except subprocess.CalledProcessError as exc:
            logger.error("backup step failed: %s", exc)
            return 1
        except (RuntimeError, FileNotFoundError) as exc:
            logger.error("configuration error: %s", exc)
            return 2

        if args.dry_run:
            logger.info("DRY RUN — tarball at %s, skipping upload + retention", tarball)
            return 0

        try:
            upload_to_box(tarball, args.env)
        except subprocess.CalledProcessError as exc:
            logger.error("upload failed: %s", exc)
            return 1
        except RuntimeError as exc:
            logger.error("configuration error: %s", exc)
            return 2

    # Retention runs against the remote, not the local working tree.
    try:
        prune_old_backups(args.env, keep_weeks)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return 3

    logger.info("bloom-weekly-backup env=%s timestamp=%s complete", args.env, timestamp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
