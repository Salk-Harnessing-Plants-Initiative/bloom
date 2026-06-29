#!/usr/bin/env python3
"""One-time archive of the Bloom V1 AWS S3 bucket to a Box folder.

Streams every object from the legacy V1 S3 bucket straight to a folder on
Box using rclone, so nothing is staged on local disk. Objects are *copied*
(never `sync`) — the script will not delete anything on Box. Re-running is
safe and resumable: rclone skips objects already present with a matching
size, so an interrupted run can simply be started again.

This is a deliberate one-shot archive, not a recurring job. For the
recurring V2 Postgres+MinIO backup see `scheduled-jobs/weekly-backup/`.

--------------------------------------------------------------------------
Prerequisites
--------------------------------------------------------------------------
* `rclone` on PATH.
* A pre-configured rclone **Box** remote (e.g. `rclone config` -> Box).
  Its name is read from BOX_RCLONE_REMOTE (default `box`). The destination
  folder is created on Box automatically by rclone if it does not exist.
* AWS credentials for the V1 bucket, supplied via the standard
  AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY env vars (and
  AWS_SESSION_TOKEN if using temporary creds). Secrets are passed to
  rclone through env, never on the command line.

--------------------------------------------------------------------------
Configuration (env vars, all overridable by CLI flags)
--------------------------------------------------------------------------
  V1_S3_BUCKET           V1 source bucket name              (required)
  V1_S3_PREFIX           optional key prefix to limit scope (default: "")
  V1_S3_REGION           AWS region of the bucket           (default: us-west-2)
  AWS_ACCESS_KEY_ID      AWS access key                     (required)
  AWS_SECRET_ACCESS_KEY  AWS secret key                     (required)
  AWS_SESSION_TOKEN      AWS session token                  (optional)
  BOX_RCLONE_REMOTE      name of the rclone Box remote      (default: box)
  V1_ARCHIVE_DEST_DIR    folder path on Box                 (default: bloom-v1-archive)

--------------------------------------------------------------------------
Usage
--------------------------------------------------------------------------
  # See how big the transfer is and what would happen, without copying:
  V1_S3_BUCKET=salk-hpi-bloom AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... \
      python scripts/backup_v1_s3_to_box.py --dry-run

  # Run the archive, then verify source vs Box by size+count:
  V1_S3_BUCKET=salk-hpi-bloom AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... \
      python scripts/backup_v1_s3_to_box.py --verify

Exit codes:
  0 = archive copied (and verified, if --verify)
  1 = rclone subprocess failure
  2 = configuration error (missing env / rclone / Box remote)
  3 = copy succeeded but --verify found a mismatch
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys

logger = logging.getLogger("bloom_v1_s3_archive")


class ConfigError(Exception):
    """Missing env / tooling / Box remote — maps to exit code 2."""

# rclone reads an ad-hoc remote named "V1S3" from these env vars, so AWS
# secrets never appear in argv (visible via `ps`). See rclone docs:
# "Config from environment variables" (RCLONE_CONFIG_<NAME>_<KEY>).
RCLONE_REMOTE_NAME = "V1S3"

DEFAULT_REGION = "us-west-2"
DEFAULT_BOX_REMOTE = "box"
DEFAULT_DEST_DIR = "bloom-v1-archive"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _which(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise FileNotFoundError(f"required binary not on PATH: {name}")
    return found


def _run(cmd: list[str], env: dict[str, str]) -> None:
    """Run rclone, streaming its output to the log; raise on non-zero exit."""
    logger.info("running: %s", " ".join(cmd))
    result = subprocess.run(cmd, env=env, capture_output=True, text=True)
    for line in (result.stdout or "").rstrip().splitlines():
        logger.info("  %s", line)
    for line in (result.stderr or "").rstrip().splitlines():
        logger.warning("  %s", line)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, cmd)


def build_config() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Archive the V1 AWS S3 bucket to Box.")
    p.add_argument("--bucket", default=_env("V1_S3_BUCKET"), help="V1 source S3 bucket")
    p.add_argument("--prefix", default=_env("V1_S3_PREFIX"), help="optional key prefix")
    p.add_argument("--region", default=_env("V1_S3_REGION", DEFAULT_REGION))
    p.add_argument("--remote", default=_env("BOX_RCLONE_REMOTE", DEFAULT_BOX_REMOTE),
                   help="name of the configured rclone Box remote")
    p.add_argument("--dest", default=_env("V1_ARCHIVE_DEST_DIR", DEFAULT_DEST_DIR),
                   help="destination folder path on Box")
    p.add_argument("--transfers", type=int, default=8, help="parallel transfers")
    p.add_argument("--checkers", type=int, default=16, help="parallel checkers")
    p.add_argument("--dry-run", action="store_true",
                   help="report size and show what would copy, without copying")
    p.add_argument("--verify", action="store_true",
                   help="after copy, compare source and Box by size + count")
    return p.parse_args()


def rclone_env(cfg: argparse.Namespace) -> dict[str, str]:
    """Build the child env: an ad-hoc rclone S3 remote fed from AWS creds."""
    access = _env("AWS_ACCESS_KEY_ID")
    secret = _env("AWS_SECRET_ACCESS_KEY")
    if not access or not secret:
        raise ConfigError("AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY must be set")

    env = dict(os.environ)
    env[f"RCLONE_CONFIG_{RCLONE_REMOTE_NAME}_TYPE"] = "s3"
    env[f"RCLONE_CONFIG_{RCLONE_REMOTE_NAME}_PROVIDER"] = "AWS"
    env[f"RCLONE_CONFIG_{RCLONE_REMOTE_NAME}_REGION"] = cfg.region
    env[f"RCLONE_CONFIG_{RCLONE_REMOTE_NAME}_ACCESS_KEY_ID"] = access
    env[f"RCLONE_CONFIG_{RCLONE_REMOTE_NAME}_SECRET_ACCESS_KEY"] = secret
    session = _env("AWS_SESSION_TOKEN")
    if session:
        env[f"RCLONE_CONFIG_{RCLONE_REMOTE_NAME}_SESSION_TOKEN"] = session
    return env


def _src_path(cfg: argparse.Namespace) -> str:
    """`V1S3:bucket` or `V1S3:bucket/prefix` for the rclone source."""
    prefix = cfg.prefix.strip("/")
    base = f"{RCLONE_REMOTE_NAME}:{cfg.bucket}"
    return f"{base}/{prefix}" if prefix else base


def _dest_path(cfg: argparse.Namespace) -> str:
    return f"{cfg.remote}:{cfg.dest.strip('/')}"


def validate(cfg: argparse.Namespace, rclone: str, env: dict[str, str]) -> None:
    """Fail fast on missing bucket or an unconfigured Box remote."""
    if not cfg.bucket:
        raise ConfigError("V1_S3_BUCKET (or --bucket) is required")
    listremotes = subprocess.run(
        [rclone, "listremotes"], env=env, capture_output=True, text=True
    )
    remotes = (listremotes.stdout or "").splitlines()
    if f"{cfg.remote}:" not in remotes:
        raise ConfigError(
            f"rclone Box remote '{cfg.remote}:' not configured. "
            f"Run `rclone config` to add it (configured remotes: {remotes or 'none'})."
        )


def report_size(cfg: argparse.Namespace, rclone: str, env: dict[str, str]) -> None:
    logger.info("measuring source %s ...", _src_path(cfg))
    _run([rclone, "size", _src_path(cfg)], env)


def copy_to_box(cfg: argparse.Namespace, rclone: str, env: dict[str, str]) -> None:
    src, dest = _src_path(cfg), _dest_path(cfg)
    logger.info("copying %s -> %s", src, dest)
    cmd = [
        rclone, "copy", src, dest,
        "--transfers", str(cfg.transfers),
        "--checkers", str(cfg.checkers),
        "--progress",
        "--stats", "30s",
    ]
    if cfg.dry_run:
        cmd.append("--dry-run")
    _run(cmd, env)


def verify(cfg: argparse.Namespace, rclone: str, env: dict[str, str]) -> bool:
    """Size+count check between source and Box (hash types differ, so size-only)."""
    src, dest = _src_path(cfg), _dest_path(cfg)
    logger.info("verifying %s == %s (size + count) ...", src, dest)
    result = subprocess.run(
        [rclone, "check", src, dest, "--size-only", "--one-way"],
        env=env, capture_output=True, text=True,
    )
    for line in (result.stderr or "").rstrip().splitlines():
        logger.info("  %s", line)
    return result.returncode == 0


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    cfg = build_config()

    try:
        rclone = _which("rclone")
        env = rclone_env(cfg)
        validate(cfg, rclone, env)
    except (FileNotFoundError, ConfigError) as exc:
        logger.error("%s", exc)
        return 2

    try:
        report_size(cfg, rclone, env)
        copy_to_box(cfg, rclone, env)
    except subprocess.CalledProcessError as exc:
        logger.error("rclone failed (exit %s)", exc.returncode)
        return 1

    if cfg.dry_run:
        logger.info("dry run complete — nothing was copied")
        return 0

    if cfg.verify:
        if not verify(cfg, rclone, env):
            logger.error("verification FAILED — source and Box differ")
            return 3
        logger.info("verification OK — source matches Box")

    logger.info("V1 S3 archive to Box complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
