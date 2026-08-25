"""Unit tests for scheduled-jobs/weekly-backup/backup.py.

The parts pinned here are the ones whose failure mode is a *silent* bad backup:
a truncated dump that still gzips cleanly, an empty container lookup treated as
success, an artifact uploaded without being verified.
The docker/rclone calls themselves need a live host and are exercised by the
staging rehearsal documented in _WIKI/SCHEDULEDJOBS/weekly-backup.md.
"""
from __future__ import annotations

import gzip
import importlib.util
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = REPO_ROOT / "scheduled-jobs" / "weekly-backup" / "backup.py"


def _load():
    spec = importlib.util.spec_from_file_location("weekly_backup", _SCRIPT)
    assert spec and spec.loader, f"cannot load {_SCRIPT}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


backup = _load()


# --------------------------------------------------------------------------
# Container resolution
# --------------------------------------------------------------------------


def test_container_id_is_parsed_from_ps_output():
    assert backup.parse_container_id("abc123def456\n") == "abc123def456"


def test_empty_ps_output_is_an_error_not_an_empty_backup():
    # A stopped stack must fail the run; backing up nothing must never look
    # like a successful backup.
    with pytest.raises(backup.ConfigError, match="no running"):
        backup.parse_container_id("\n  \n")


def test_multiple_containers_are_an_error():
    with pytest.raises(backup.ConfigError, match="found 2"):
        backup.parse_container_id("abc123\ndef456\n")


def test_compose_args_point_at_this_environments_files(tmp_path):
    args = backup.compose_args(tmp_path, "staging")
    assert str(tmp_path / "docker-compose.prod.yml") in args
    assert str(tmp_path / ".env.staging") in args


def test_compose_args_never_hardcode_a_container_name():
    # The bug this replaces: a hardcoded `bloom_v2_{env}-db-prod-1` is wrong on
    # any host whose deploy directory is named differently.
    source = _SCRIPT.read_text()
    assert "bloom_v2_" not in source, "container name must not be reconstructed by hand"
    # Resolution goes through `compose ps -q <service>`, so it is correct on any
    # host regardless of what the deploy directory is called.
    assert 'DB_SERVICE = "db-prod"' in source
    assert '"ps", "-q", DB_SERVICE' in source


# --------------------------------------------------------------------------
# Artifact verification
# --------------------------------------------------------------------------


def _write_gz(path: Path, payload: bytes) -> Path:
    with gzip.open(path, "wb") as handle:
        handle.write(payload)
    return path


def test_a_good_artifact_verifies_and_returns_its_size(tmp_path):
    artifact = _write_gz(tmp_path / "dump.sql.gz", b"CREATE TABLE x();" * 500)
    assert backup.verify_artifact(artifact, min_bytes=16) == artifact.stat().st_size


def test_an_undersized_artifact_is_rejected(tmp_path):
    artifact = _write_gz(tmp_path / "dump.sql.gz", b"")
    with pytest.raises(backup.ConfigError, match="below the"):
        backup.verify_artifact(artifact, min_bytes=backup.MIN_DATABASE_BYTES)


def test_a_corrupt_artifact_is_rejected(tmp_path):
    # Passes the size floor, fails integrity — the truncated-dump case.
    artifact = tmp_path / "dump.sql.gz"
    artifact.write_bytes(b"\x1f\x8b\x08" + b"\x00" * 5000)
    with pytest.raises(subprocess.CalledProcessError):
        backup.verify_artifact(artifact, min_bytes=64)


def test_a_missing_artifact_is_rejected(tmp_path):
    with pytest.raises(backup.ConfigError, match="never written"):
        backup.verify_artifact(tmp_path / "absent.sql.gz", min_bytes=1)


def test_database_floor_is_above_the_globals_floor():
    assert backup.MIN_DATABASE_BYTES > backup.MIN_GLOBALS_BYTES > 0


# --------------------------------------------------------------------------
# Pipeline exit handling
# --------------------------------------------------------------------------


def test_a_failing_source_fails_the_run_even_though_gzip_succeeds(tmp_path):
    # gzip happily compresses an empty stream and exits 0. Checking only the
    # last process in the pipeline is what lets a failed dump ship.
    out = tmp_path / "out.gz"
    with pytest.raises(subprocess.CalledProcessError):
        backup._stream_to_gzip(["false"], out)


def test_a_succeeding_source_writes_a_readable_artifact(tmp_path):
    out = tmp_path / "out.gz"
    backup._stream_to_gzip(["printf", "hello dump"], out)
    with gzip.open(out, "rb") as handle:
        assert handle.read() == b"hello dump"


# --------------------------------------------------------------------------
# Destination
# --------------------------------------------------------------------------


def test_destination_requires_a_configured_remote(monkeypatch):
    monkeypatch.delenv("BACKUP_RCLONE_REMOTE", raising=False)
    with pytest.raises(backup.ConfigError, match="BACKUP_RCLONE_REMOTE"):
        backup.backup_destination("prod")


def test_destination_defaults_per_environment(monkeypatch):
    monkeypatch.setenv("BACKUP_RCLONE_REMOTE", "box")
    monkeypatch.delenv("BACKUP_RCLONE_DEST_DIR", raising=False)
    assert backup.backup_destination("staging") == ("box", "bloom-backups/staging")
    assert backup.backup_destination("prod") == ("box", "bloom-backups/prod")


# --------------------------------------------------------------------------
# Exit-code contract
# --------------------------------------------------------------------------


def test_exit_codes_are_distinct():
    codes = {backup.EXIT_OK, backup.EXIT_SUBPROCESS, backup.EXIT_CONFIG}
    assert len(codes) == 3, "each failure class needs its own exit code"


def test_the_job_never_deletes_anything_on_the_remote():
    # This job uploads only. Nothing on Box is ever removed by it, by design.
    source = _SCRIPT.read_text()
    assert '"delete"' not in source
    assert "--min-age" not in source
    assert not hasattr(backup, "prune_old_backups")


def test_a_missing_deploy_dir_is_a_config_error(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKUP_STATE_DIR", str(tmp_path / "state"))
    rc = backup.main(["--env", "prod", "--deploy-dir", str(tmp_path / "absent")])
    assert rc == backup.EXIT_CONFIG


def test_dry_run_verifies_but_never_uploads(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKUP_STATE_DIR", str(tmp_path / "state"))
    touched = []
    monkeypatch.setattr(backup, "resolve_container", lambda *a: "container123")
    monkeypatch.setattr(backup, "dump_database", lambda *a: tmp_path / "db.sql.gz")
    monkeypatch.setattr(backup, "dump_globals", lambda *a: tmp_path / "globals.sql.gz")
    monkeypatch.setattr(backup, "upload", lambda *a: touched.append("upload"))
    rc = backup.main(["--env", "prod", "--deploy-dir", str(tmp_path), "--dry-run"])
    assert rc == backup.EXIT_OK
    assert not touched, "a dry run must not upload"


def test_the_working_directory_is_removed_on_failure(tmp_path, monkeypatch):
    state = tmp_path / "state"
    monkeypatch.setenv("BACKUP_STATE_DIR", str(state))
    monkeypatch.setattr(
        backup, "resolve_container",
        lambda *a: (_ for _ in ()).throw(backup.ConfigError("stack down")),
    )
    backup.main(["--env", "prod", "--deploy-dir", str(tmp_path)])
    leftovers = list(state.glob("bloom-backup-*"))
    assert not leftovers, f"a dump directory outlived a failed run: {leftovers}"


def test_the_working_directory_is_removed_on_success(tmp_path, monkeypatch):
    state = tmp_path / "state"
    monkeypatch.setenv("BACKUP_STATE_DIR", str(state))
    monkeypatch.setattr(backup, "resolve_container", lambda *a: "container123")
    monkeypatch.setattr(backup, "dump_database", lambda *a: tmp_path / "db.sql.gz")
    monkeypatch.setattr(backup, "dump_globals", lambda *a: tmp_path / "globals.sql.gz")
    monkeypatch.setattr(backup, "upload", lambda *a: None)
    rc = backup.main(["--env", "prod", "--deploy-dir", str(tmp_path)])
    assert rc == backup.EXIT_OK
    assert not list(state.glob("bloom-backup-*"))


# --------------------------------------------------------------------------
# The privileges contract
# --------------------------------------------------------------------------


def test_the_database_dump_keeps_owners_and_privileges(tmp_path, monkeypatch):
    # The whole point of this change over PR #340: --no-owner/--no-privileges
    # produce a dump that restores into a database with no grants.
    seen: list[list[str]] = []
    monkeypatch.setattr(backup, "_stream_to_gzip", lambda cmd, out: seen.append(cmd))
    monkeypatch.setattr(backup, "verify_artifact", lambda *a, **k: 999999)
    monkeypatch.setattr(backup, "_which", lambda name: name)
    backup.dump_database("container123", tmp_path, "20260824T000000Z")
    cmd = seen[0]
    assert "pg_dump" in cmd
    assert "--no-owner" not in cmd
    assert "--no-privileges" not in cmd
    assert "--no-acl" not in cmd


def test_globals_are_dumped_alongside_the_database(tmp_path, monkeypatch):
    seen: list[list[str]] = []
    monkeypatch.setattr(backup, "_stream_to_gzip", lambda cmd, out: seen.append(cmd))
    monkeypatch.setattr(backup, "verify_artifact", lambda *a, **k: 999)
    monkeypatch.setattr(backup, "_which", lambda name: name)
    backup.dump_globals("container123", tmp_path, "20260824T000000Z")
    assert "pg_dumpall" in seen[0]
    assert "--globals-only" in seen[0]


def test_both_artifacts_share_one_run_timestamp(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "_stream_to_gzip", lambda cmd, out: None)
    monkeypatch.setattr(backup, "verify_artifact", lambda *a, **k: 999)
    monkeypatch.setattr(backup, "_which", lambda name: name)
    stamp = "20260824T010203Z"
    db = backup.dump_database("c", tmp_path, stamp)
    globals_ = backup.dump_globals("c", tmp_path, stamp)
    assert stamp in db.name and stamp in globals_.name
