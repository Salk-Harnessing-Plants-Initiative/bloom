"""Unit tests for scheduled-jobs/weekly-backup/backup.py.

The parts pinned here are the ones whose failure mode is a *silent* bad backup:
a truncated dump that still gzips cleanly, an empty container lookup treated as
success, an artifact uploaded without being verified.
The docker/rclone calls themselves need a live host and are exercised by the
dry-run rehearsal documented in _WIKI/SCHEDULEDJOBS/weekly-backup.md.
"""
from __future__ import annotations

import gzip
import importlib.util
import subprocess
import sys
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


def _deploy_dir(tmp_path, env_name="prod", **extra):
    """A deploy directory with the env file the script reads its config from."""
    lines = ["BACKUP_RCLONE_REMOTE=box", f"BACKUP_RCLONE_DEST_DIR=bloom-backups/{env_name}"]
    lines += [f"{k}={v}" for k, v in extra.items()]
    (tmp_path / f".env.{env_name}").write_text("\n".join(lines) + "\n")
    return tmp_path


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
    args = backup.compose_args(tmp_path, "prod")
    assert str(tmp_path / "docker-compose.prod.yml") in args
    assert str(tmp_path / ".env.prod") in args


def test_the_compose_project_is_pinned_by_the_file_not_the_directory():
    # Why this job is production-only. compose_args passes no -p, and
    # docker-compose.prod.yml pins `name: bloom_v2_prod`, which outranks the
    # directory basename. So `ps -q db-prod` resolves the PRODUCTION container
    # from any deploy directory — a staging target would read staging's env
    # file (and so its Box folder) while dumping production. Adding a staging
    # path again requires -p matching deploy.yml's `-p bloom_v2_staging`.
    assert "-p" not in backup.compose_args(Path("/srv/bloom"), "prod")
    compose = (REPO_ROOT / "docker-compose.prod.yml").read_text()
    assert "\nname: bloom_v2_prod\n" in compose


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
# Production only
# --------------------------------------------------------------------------


def test_the_only_environment_this_job_backs_up_is_production():
    assert backup.BACKUP_ENV == "prod"


def test_the_script_itself_refuses_any_other_environment(tmp_path, monkeypatch):
    # The workflow no longer offers staging, but the workflow is not what a
    # person rehearsing by hand runs. COMPOSE_FILE pins the production compose
    # project, so a staging run reads staging's env file and still resolves the
    # PRODUCTION container: a full prod dump, auth.users included, uploaded to
    # staging's Box folder with every log line saying "staging".
    _deploy_dir(tmp_path, env_name="staging")
    monkeypatch.setenv("BACKUP_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(backup, "_which", lambda name: name)
    resolved: list[str] = []
    monkeypatch.setattr(backup, "resolve_container",
                        lambda *a: resolved.append("resolved") or "container123")
    dumped: list[str] = []
    monkeypatch.setattr(backup, "dump_database", lambda *a: dumped.append("database"))

    rc = backup.main(["--env", "staging", "--deploy-dir", str(tmp_path)])
    assert rc == backup.EXIT_CONFIG
    assert not resolved, "no container may be resolved for a non-production run"
    assert not dumped, "no dump may be taken for a non-production run"


def test_the_refusal_names_the_rehearsal_that_is_allowed(tmp_path, caplog):
    # A refusal an operator cannot act on gets worked around instead.
    _deploy_dir(tmp_path, env_name="staging")
    with caplog.at_level("ERROR"):
        backup.main(["--env", "staging", "--deploy-dir", str(tmp_path)])
    assert "--env prod --dry-run" in caplog.text


def test_a_non_production_run_cannot_even_print_a_destination(tmp_path, capsys):
    # --print-destination is what the workflow summary calls. It must not become
    # the way a staging path gets named anywhere.
    _deploy_dir(tmp_path, env_name="staging")
    rc = backup.main(["--env", "staging", "--deploy-dir", str(tmp_path),
                      "--print-destination"])
    assert rc == backup.EXIT_CONFIG
    assert "staging" not in capsys.readouterr().out


def test_the_compose_file_is_what_makes_this_production_only():
    # If the compose file ever stops pinning the project name, the guard above
    # is protecting against something that no longer applies — revisit both.
    compose = (REPO_ROOT / backup.COMPOSE_FILE).read_text()
    assert "name: bloom_v2_prod" in compose


# --------------------------------------------------------------------------
# Artifact verification
# --------------------------------------------------------------------------


def _write_gz(path: Path, payload: bytes) -> Path:
    with gzip.open(path, "wb") as handle:
        handle.write(payload)
    return path


def _database_dump(rows: int = backup.MIN_DATA_ROWS, complete: bool = True) -> bytes:
    """A plain pg_dump, in the shape the content check reads."""
    body = ["--", "-- PostgreSQL database dump", "--", "",
            "COPY public.plants (id, barcode) FROM stdin;"]
    body += [f"{n}\tBRC{n:05d}" for n in range(rows)]
    body += ["\\.", ""]
    if complete:
        body.append(backup.DB_DUMP_COMPLETE_MARKER)
    return ("\n".join(body) + "\n").encode()


def _globals_dump(roles: int = backup.MIN_ROLE_STATEMENTS, complete: bool = True) -> bytes:
    """A pg_dumpall --globals-only dump, in the shape the content check reads."""
    body = ["--", "-- PostgreSQL database cluster dump", "--", ""]
    for n in range(roles):
        body += [f"CREATE ROLE bloom_role_{n};",
                 f"ALTER ROLE bloom_role_{n} WITH NOSUPERUSER LOGIN;"]
    if complete:
        body.append(backup.GLOBALS_DUMP_COMPLETE_MARKER)
    return ("\n".join(body) + "\n").encode()


def _dump_writer(seen: list, payload: bytes):
    """Stand in for the real pipeline: record the command, write a real dump."""
    def _write(cmd, out):
        seen.append(cmd)
        _write_gz(out, payload)
    return _write


def test_a_good_artifact_verifies_and_returns_its_size(tmp_path):
    artifact = _write_gz(tmp_path / "dump.sql.gz", b"CREATE TABLE x();" * 500)
    assert backup.verify_artifact(artifact, min_bytes=16) == artifact.stat().st_size


def test_an_undersized_artifact_is_rejected(tmp_path):
    artifact = _write_gz(tmp_path / "dump.sql.gz", b"")
    with pytest.raises(backup.VerificationError, match="below the"):
        backup.verify_artifact(artifact, min_bytes=backup.MIN_DATABASE_BYTES)


def test_a_corrupt_artifact_is_rejected(tmp_path):
    # Passes the size floor, fails integrity — the truncated-dump case.
    artifact = tmp_path / "dump.sql.gz"
    artifact.write_bytes(b"\x1f\x8b\x08" + b"\x00" * 5000)
    with pytest.raises(backup.VerificationError, match="gzip integrity"):
        backup.verify_artifact(artifact, min_bytes=64)


def test_a_missing_artifact_is_rejected(tmp_path):
    with pytest.raises(backup.VerificationError, match="never written"):
        backup.verify_artifact(tmp_path / "absent.sql.gz", min_bytes=1)


def test_database_floor_is_above_the_globals_floor():
    assert backup.MIN_DATABASE_BYTES > backup.MIN_GLOBALS_BYTES > 0


# --------------------------------------------------------------------------
# Content verification
# --------------------------------------------------------------------------


def test_a_dump_full_of_rows_passes_its_content_check(tmp_path):
    artifact = _write_gz(tmp_path / "db.sql.gz", _database_dump(rows=500))
    assert backup.verify_database_content(artifact) == 500


def test_a_dump_whose_tables_all_came_out_empty_is_rejected(tmp_path):
    # The RLS case: if the dumping role loses its bypass privilege the dump is
    # valid SQL with every COPY block empty, clears the size floor, and gzips
    # cleanly. Size and integrity checks cannot tell it from a good backup.
    artifact = _write_gz(tmp_path / "db.sql.gz", _database_dump(rows=0))
    with pytest.raises(backup.VerificationError, match="could not read"):
        backup.verify_database_content(artifact)


def test_a_dump_that_stopped_partway_is_rejected(tmp_path):
    # gzip closes its stream cleanly around a pg_dump that died mid-table, so
    # the completion line pg_dump writes last is the only evidence it finished.
    artifact = _write_gz(tmp_path / "db.sql.gz", _database_dump(rows=500, complete=False))
    with pytest.raises(backup.VerificationError, match="stopped partway"):
        backup.verify_database_content(artifact)


def test_a_data_row_cannot_forge_the_completion_line(tmp_path):
    # A row holding the marker text would otherwise let a truncated dump pass.
    payload = ("COPY public.notes (body) FROM stdin;\n"
               + backup.DB_DUMP_COMPLETE_MARKER + "\n") * 200
    artifact = _write_gz(tmp_path / "db.sql.gz", payload.encode())
    with pytest.raises(backup.VerificationError, match="stopped partway"):
        backup.verify_database_content(artifact)


def test_a_globals_dump_defining_roles_passes(tmp_path):
    artifact = _write_gz(tmp_path / "globals.sql.gz", _globals_dump(roles=9))
    assert backup.verify_globals_content(artifact) == 9


def test_a_globals_dump_with_no_roles_is_rejected(tmp_path):
    # The database dump's OWNER and GRANT statements name these roles.
    artifact = _write_gz(tmp_path / "globals.sql.gz", _globals_dump(roles=0))
    with pytest.raises(backup.VerificationError, match="nothing to bind to"):
        backup.verify_globals_content(artifact)


def test_a_truncated_globals_dump_is_rejected(tmp_path):
    artifact = _write_gz(tmp_path / "globals.sql.gz",
                         _globals_dump(roles=9, complete=False))
    with pytest.raises(backup.VerificationError, match="stopped partway"):
        backup.verify_globals_content(artifact)


def test_the_content_floors_sit_below_any_real_cluster():
    assert backup.MIN_DATA_ROWS > 0
    # Supabase alone ships anon, authenticated, service_role, supabase_admin
    # and authenticator, before any role this project adds.
    assert 0 < backup.MIN_ROLE_STATEMENTS <= 5


def test_an_empty_dump_fails_the_run_on_the_verification_code(tmp_path, monkeypatch):
    # End to end: a content failure must land on 3, the same code a short or
    # corrupt artifact does, so the wiki's table stays true.
    _deploy_dir(tmp_path)
    monkeypatch.setenv("BACKUP_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(backup, "_which", lambda name: name)
    monkeypatch.setattr(backup, "resolve_container", lambda *a: "container123")
    monkeypatch.setattr(backup, "verify_artifact", lambda *a, **k: 999999)
    monkeypatch.setattr(backup, "_stream_to_gzip", _dump_writer([], _database_dump(rows=0)))
    uploaded: list = []
    monkeypatch.setattr(backup, "upload", lambda *a: uploaded.append(a))

    assert backup.main(["--env", "prod", "--deploy-dir", str(tmp_path)]) == backup.EXIT_VERIFY
    assert not uploaded, "an empty dump must never reach Box"


def test_a_bad_dump_exits_on_its_own_code_not_the_config_one(tmp_path, monkeypatch):
    # Exit 2 tells the operator to go and look at .env and rclone. A short or
    # corrupt dump is the one failure this job exists to catch, so it gets its
    # own code and the wiki's table can stay true.
    _deploy_dir(tmp_path)
    monkeypatch.setenv("BACKUP_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(backup, "_which", lambda name: name)
    monkeypatch.setattr(backup, "resolve_container", lambda *a: "container123")
    monkeypatch.setattr(backup, "_stream_to_gzip", lambda cmd, out: out.write_bytes(b""))

    rc = backup.main(["--env", "prod", "--deploy-dir", str(tmp_path)])
    assert rc == backup.EXIT_VERIFY
    assert backup.EXIT_VERIFY not in (backup.EXIT_OK, backup.EXIT_SUBPROCESS,
                                      backup.EXIT_CONFIG)


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
    codes = {backup.EXIT_OK, backup.EXIT_SUBPROCESS, backup.EXIT_CONFIG,
             backup.EXIT_VERIFY, backup.EXIT_SIGNAL}
    assert len(codes) == 5, "each failure class needs its own exit code"


def test_a_real_subprocess_failure_exits_on_the_subprocess_code(tmp_path, monkeypatch):
    # The mirror of the EXIT_VERIFY test above, driven by a process that really
    # exits non-zero rather than a raised CalledProcessError: without it, a
    # change routing this path to EXIT_CONFIG would pass the whole suite.
    _deploy_dir(tmp_path)
    monkeypatch.setenv("BACKUP_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(backup, "_which", lambda name: name)
    monkeypatch.setattr(backup, "resolve_container", lambda *a: "container123")

    def _failing_dump(container, work_dir, timestamp):
        backup._stream_to_gzip(
            [sys.executable, "-c", "import sys; sys.stderr.write('boom\\n'); sys.exit(3)"],
            work_dir / "database.sql.gz",
        )

    monkeypatch.setattr(backup, "dump_database", _failing_dump)
    rc = backup.main(["--env", "prod", "--deploy-dir", str(tmp_path)])
    assert rc == backup.EXIT_SUBPROCESS
    assert rc != backup.EXIT_CONFIG


def test_a_cancelled_run_does_not_look_like_a_failed_dump(tmp_path):
    # SystemExit carrying a string exits 1, which is the subprocess code. Being
    # cancelled from the Actions tab, or hitting timeout-minutes, is not a
    # failure of docker or pg_dump and must not read as one.
    driver = f"""
import importlib.util, os, signal
spec = importlib.util.spec_from_file_location("wb", {str(_SCRIPT)!r})
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)
signal.signal(signal.SIGTERM, backup._terminate)
os.kill(os.getpid(), signal.SIGTERM)
"""
    done = subprocess.run([sys.executable, "-c", driver], capture_output=True, text=True)
    assert done.returncode == backup.EXIT_SIGNAL
    assert done.returncode != backup.EXIT_SUBPROCESS


# Every rclone subcommand that can remove something at the destination.
DESTRUCTIVE_RCLONE_VERBS = (
    "sync", "move", "moveto", "purge", "delete", "deletefile",
    "rmdir", "rmdirs", "cleanup",
)


def test_a_missing_remote_fails_before_the_dump_runs(tmp_path, monkeypatch):
    # Discovering the destination is unset after a multi-GB production dump
    # costs the whole dump window and then throws the artifact away.
    (tmp_path / ".env.prod").write_text("POSTGRES_DB=postgres\n")
    monkeypatch.delenv("BACKUP_RCLONE_REMOTE", raising=False)
    monkeypatch.setenv("BACKUP_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(backup, "_which", lambda name: name)
    dumped: list[str] = []
    monkeypatch.setattr(backup, "resolve_container", lambda *a: "container123")
    monkeypatch.setattr(backup, "dump_database",
                        lambda *a: dumped.append("database"))

    rc = backup.main(["--env", "prod", "--deploy-dir", str(tmp_path)])
    assert rc == backup.EXIT_CONFIG
    assert not dumped, "no dump may run before the destination is known"


def test_a_dry_run_still_needs_no_rclone(tmp_path, monkeypatch):
    # Proving the dump path before Box is set up is the point of --dry-run, so
    # the preflight above must not start demanding rclone.
    _deploy_dir(tmp_path)
    monkeypatch.setenv("BACKUP_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(backup, "resolve_container", lambda *a: "container123")
    monkeypatch.setattr(backup, "dump_database", lambda *a: tmp_path / "db.sql.gz")
    monkeypatch.setattr(backup, "dump_globals", lambda *a: tmp_path / "globals.sql.gz")

    def _no_rclone(name: str) -> str:
        if name == "rclone":
            raise backup.ConfigError("rclone is not installed")
        return name

    monkeypatch.setattr(backup, "_which", _no_rclone)
    rc = backup.main(["--env", "prod", "--deploy-dir", str(tmp_path), "--dry-run"])
    assert rc == backup.EXIT_OK


def test_the_job_only_ever_copies_to_the_remote(tmp_path, monkeypatch):
    # Assert the verb positively. Blacklisting the single spelling "delete" let
    # `sync` through — which removes everything at the destination that is not
    # in the source, i.e. every previous week's backup.
    seen: list[list[str]] = []
    monkeypatch.setattr(backup, "_run", lambda cmd, cwd=None: seen.append(cmd))
    monkeypatch.setattr(backup, "_which", lambda name: name)
    monkeypatch.setenv("BACKUP_RCLONE_REMOTE", "box")
    monkeypatch.setenv("BACKUP_RCLONE_DEST_DIR", "bloom-backups/prod")

    work = tmp_path / "bloom-backup-run"
    work.mkdir()
    destination = backup.upload([work / "database.sql.gz", work / "globals.sql.gz"],
                                "prod", "20260824T000000Z")

    assert len(seen) == 1, "the pair goes up as one copy, not one call per file"
    cmd = seen[0]
    assert cmd[0] == "rclone"
    assert cmd[1] == "copy", f"rclone must only ever copy, not {cmd[1]!r}"
    assert cmd[2] == str(work), "copy the working directory, not a single file"
    assert cmd[3] == destination == "box:bloom-backups/prod/20260824T000000Z/"


def test_the_upload_retries_rather_than_lose_a_verified_dump(tmp_path, monkeypatch):
    # By the time the upload runs the dump is taken and verified; the next
    # attempt is a week away. A transient blip must not be the end of the run.
    seen: list[list[str]] = []
    monkeypatch.setattr(backup, "_run", lambda cmd, cwd=None: seen.append(cmd))
    monkeypatch.setattr(backup, "_which", lambda name: name)
    monkeypatch.setenv("BACKUP_RCLONE_REMOTE", "box")
    work = tmp_path / "bloom-backup-run"
    work.mkdir()

    backup.upload([work / "database.sql.gz"], "prod", "20260824T000000Z")
    cmd = seen[0]
    assert "--retries" in cmd, "a blip must not discard the week's backup"
    retries = int(cmd[cmd.index("--retries") + 1])
    assert retries > 1
    assert "--retries-sleep" in cmd, "retries with no backoff hammer a flaky link"


def test_the_job_never_deletes_anything_on_the_remote():
    # This job uploads only. Nothing on Box is ever removed by it, by design.
    source = _SCRIPT.read_text()
    for verb in DESTRUCTIVE_RCLONE_VERBS:
        assert f'"{verb}"' not in source, f"destructive rclone verb in source: {verb}"
    assert "--min-age" not in source
    assert not hasattr(backup, "prune_old_backups")


def test_a_missing_deploy_dir_is_a_config_error(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKUP_STATE_DIR", str(tmp_path / "state"))
    rc = backup.main(["--env", "prod", "--deploy-dir", str(tmp_path / "absent")])
    assert rc == backup.EXIT_CONFIG


def test_dry_run_verifies_but_never_uploads(tmp_path, monkeypatch):
    _deploy_dir(tmp_path)
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
    # _deploy_dir matters: without the env file main() returns before the state
    # dir is created, and the glob below passes without reaching the cleanup.
    _deploy_dir(tmp_path)
    state = tmp_path / "state"
    monkeypatch.setenv("BACKUP_STATE_DIR", str(state))
    monkeypatch.setattr(
        backup, "resolve_container",
        lambda *a: (_ for _ in ()).throw(backup.ConfigError("stack down")),
    )
    rc = backup.main(["--env", "prod", "--deploy-dir", str(tmp_path)])
    assert rc == backup.EXIT_CONFIG
    assert state.is_dir(), "the run must have got far enough to create the state dir"
    leftovers = list(state.glob("bloom-backup-*"))
    assert not leftovers, f"a dump directory outlived a failed run: {leftovers}"


def test_the_working_directory_is_removed_on_success(tmp_path, monkeypatch):
    _deploy_dir(tmp_path)
    state = tmp_path / "state"
    monkeypatch.setenv("BACKUP_STATE_DIR", str(state))
    # The preflight resolves rclone on PATH; without this the test passes only
    # on a machine that happens to have rclone installed.
    monkeypatch.setattr(backup, "_which", lambda name: name)
    monkeypatch.setattr(backup, "resolve_container", lambda *a: "container123")
    monkeypatch.setattr(backup, "dump_database", lambda *a: tmp_path / "db.sql.gz")
    monkeypatch.setattr(backup, "dump_globals", lambda *a: tmp_path / "globals.sql.gz")
    monkeypatch.setattr(backup, "upload", lambda *a: None)
    rc = backup.main(["--env", "prod", "--deploy-dir", str(tmp_path)])
    assert rc == backup.EXIT_OK
    assert not list(state.glob("bloom-backup-*"))


def test_a_dump_left_by_a_killed_run_is_swept_at_startup(tmp_path, monkeypatch):
    # SIGKILL and power loss cannot be caught, so the next run must clear what
    # they left. Each orphan is a full plaintext dump including auth.users.
    _deploy_dir(tmp_path)
    state = tmp_path / "state"
    orphan = state / "bloom-backup-oldrun"
    orphan.mkdir(parents=True)
    (orphan / "postgres-postgres-20260824T021700Z.sql.gz").write_bytes(b"stale dump")

    monkeypatch.setenv("BACKUP_STATE_DIR", str(state))
    monkeypatch.setattr(backup, "_which", lambda name: name)
    monkeypatch.setattr(backup, "resolve_container", lambda *a: "container123")
    monkeypatch.setattr(backup, "dump_database", lambda *a: tmp_path / "db.sql.gz")
    monkeypatch.setattr(backup, "dump_globals", lambda *a: tmp_path / "globals.sql.gz")
    monkeypatch.setattr(backup, "upload", lambda *a: None)

    assert backup.main(["--env", "prod", "--deploy-dir", str(tmp_path)]) == backup.EXIT_OK
    assert not orphan.exists(), "an orphaned dump survived the next run"


def test_an_orphan_is_swept_even_when_this_run_cannot_start(tmp_path, monkeypatch):
    # The sweep used to sit after config loading, so a persistent config error
    # — renamed deploy directory, unreadable env file — meant every run
    # returned before reaching it and the orphaned plaintext dump stayed put
    # indefinitely, well past the one week the wiki promises.
    state = tmp_path / "state"
    orphan = state / "bloom-backup-oldrun"
    orphan.mkdir(parents=True)
    (orphan / "postgres-postgres-20260824T021700Z.sql.gz").write_bytes(b"stale dump")
    monkeypatch.setenv("BACKUP_STATE_DIR", str(state))

    rc = backup.main(["--env", "prod", "--deploy-dir", str(tmp_path / "gone")])
    assert rc == backup.EXIT_CONFIG
    assert not orphan.exists(), "a failing run stranded a full plaintext dump"


def test_a_broken_sweep_does_not_mask_the_error_that_caused_it(tmp_path, monkeypatch):
    # The sweep runs on an already-failing path; it must report the config
    # error, not an OSError raised while tidying up.
    monkeypatch.setenv("BACKUP_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "state").mkdir()

    def _explode(_state_dir):
        raise OSError("state directory went away")

    monkeypatch.setattr(backup, "sweep_stale_work_dirs", _explode)
    rc = backup.main(["--env", "prod", "--deploy-dir", str(tmp_path / "gone")])
    assert rc == backup.EXIT_CONFIG


def test_the_working_copy_is_not_readable_by_other_users(tmp_path, monkeypatch):
    # It holds a full plaintext dump, auth.users included, on a host whose
    # runner runs other jobs.
    state = tmp_path / "state"
    _deploy_dir(tmp_path)
    monkeypatch.setenv("BACKUP_STATE_DIR", str(state))
    monkeypatch.setattr(backup, "_which", lambda name: name)
    monkeypatch.setattr(backup, "resolve_container", lambda *a: "container123")
    monkeypatch.setattr(backup, "dump_database", lambda *a: tmp_path / "db.sql.gz")
    monkeypatch.setattr(backup, "dump_globals", lambda *a: tmp_path / "globals.sql.gz")
    monkeypatch.setattr(backup, "upload", lambda *a: None)

    assert backup.main(["--env", "prod", "--deploy-dir", str(tmp_path)]) == backup.EXIT_OK
    assert state.stat().st_mode & 0o077 == 0, "group or other can read the dump"


def test_a_state_directory_left_loose_by_an_earlier_run_is_tightened(tmp_path, monkeypatch):
    # mode= on the create only applies when the create happens. This directory
    # persists between runs, so a pre-existing loose one has to be fixed too.
    state = tmp_path / "state"
    state.mkdir(mode=0o755)
    _deploy_dir(tmp_path)
    monkeypatch.setenv("BACKUP_STATE_DIR", str(state))
    monkeypatch.setattr(backup, "_which", lambda name: name)
    monkeypatch.setattr(backup, "resolve_container", lambda *a: "container123")
    monkeypatch.setattr(backup, "dump_database", lambda *a: tmp_path / "db.sql.gz")
    monkeypatch.setattr(backup, "dump_globals", lambda *a: tmp_path / "globals.sql.gz")
    monkeypatch.setattr(backup, "upload", lambda *a: None)

    assert backup.main(["--env", "prod", "--deploy-dir", str(tmp_path)]) == backup.EXIT_OK
    assert state.stat().st_mode & 0o077 == 0


def _run_until_signalled(state: Path, install_handler: bool) -> list[str]:
    """Enter the real working dir, take a signal, report what was left behind."""
    driver = f"""
import importlib.util, os, signal, tempfile
spec = importlib.util.spec_from_file_location("wb", {str(_SCRIPT)!r})
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)
if {install_handler!r}:
    signal.signal(signal.SIGTERM, backup._terminate)
try:
    with tempfile.TemporaryDirectory(prefix="bloom-backup-", dir={str(state)!r}):
        os.kill(os.getpid(), signal.SIGTERM)
except SystemExit:
    pass
"""
    subprocess.run([sys.executable, "-c", driver], capture_output=True, text=True)
    return [p.name for p in state.glob("bloom-backup-*")]


def test_a_terminating_signal_still_removes_the_working_directory(tmp_path):
    # Cancelling the run from the Actions tab, and timeout-minutes, both kill
    # this process from outside. No exception is raised, so `with` alone does
    # not unwind — the handler is what makes it.
    state = tmp_path / "state"
    state.mkdir()
    assert _run_until_signalled(state, install_handler=True) == []

    # Control: without the handler the dump is stranded. If this ever comes
    # back empty the test above has stopped proving anything.
    for stale in state.glob("bloom-backup-*"):
        stale.rmdir()
    assert _run_until_signalled(state, install_handler=False) != []


def test_main_installs_the_termination_handlers(tmp_path, monkeypatch):
    _deploy_dir(tmp_path)
    monkeypatch.setenv("BACKUP_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(backup, "resolve_container", lambda *a: "container123")
    monkeypatch.setattr(backup, "dump_database", lambda *a: tmp_path / "db.sql.gz")
    monkeypatch.setattr(backup, "dump_globals", lambda *a: tmp_path / "globals.sql.gz")
    monkeypatch.setattr(backup, "upload", lambda *a: None)

    installed = {}
    monkeypatch.setattr(backup.signal, "signal",
                        lambda sig, handler: installed.setdefault(sig, handler))
    backup.main(["--env", "prod", "--deploy-dir", str(tmp_path)])
    assert set(installed) == {backup.signal.SIGTERM, backup.signal.SIGHUP}
    assert set(installed.values()) == {backup._terminate}


# --------------------------------------------------------------------------
# The privileges contract
# --------------------------------------------------------------------------


def test_the_database_dump_keeps_owners_and_privileges(tmp_path, monkeypatch):
    # The whole point of this change over PR #340: --no-owner/--no-privileges
    # produce a dump that restores into a database with no grants.
    seen: list[list[str]] = []
    monkeypatch.setattr(backup, "_stream_to_gzip", _dump_writer(seen, _database_dump()))
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
    monkeypatch.setattr(backup, "_stream_to_gzip", _dump_writer(seen, _globals_dump()))
    monkeypatch.setattr(backup, "verify_artifact", lambda *a, **k: 999)
    monkeypatch.setattr(backup, "_which", lambda name: name)
    backup.dump_globals("container123", tmp_path, "20260824T000000Z")
    assert "pg_dumpall" in seen[0]
    assert "--globals-only" in seen[0]


def test_a_run_dumps_and_uploads_both_artifacts(tmp_path, monkeypatch):
    # The sibling test proves dump_globals works when called. This proves a run
    # calls it: the database dump's GRANT statements name roles only the globals
    # file defines, so shipping one without the other is half a backup.
    _deploy_dir(tmp_path)
    monkeypatch.setenv("BACKUP_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(backup, "_which", lambda name: name)
    monkeypatch.setattr(backup, "resolve_container", lambda *a: "container123")

    database = tmp_path / "postgres-postgres-20260824T000000Z.sql.gz"
    globals_ = tmp_path / "globals-20260824T000000Z.sql.gz"
    dumped: list[str] = []
    monkeypatch.setattr(backup, "dump_database",
                        lambda *a: (dumped.append("database"), database)[1])
    monkeypatch.setattr(backup, "dump_globals",
                        lambda *a: (dumped.append("globals"), globals_)[1])
    uploaded: list[Path] = []
    monkeypatch.setattr(backup, "upload",
                        lambda artifacts, env, ts: uploaded.extend(artifacts) or "box:d/")

    rc = backup.main(["--env", "prod", "--deploy-dir", str(tmp_path)])
    assert rc == backup.EXIT_OK
    assert dumped == ["database", "globals"], "a run must take both dumps"
    assert uploaded == [database, globals_], "both artifacts must reach Box"


def test_both_artifacts_share_one_run_timestamp(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "_stream_to_gzip", _dump_writer([], _database_dump()))
    monkeypatch.setattr(backup, "verify_artifact", lambda *a, **k: 999)
    monkeypatch.setattr(backup, "verify_globals_content", lambda *a, **k: 9)
    monkeypatch.setattr(backup, "_which", lambda name: name)
    stamp = "20260824T010203Z"
    db = backup.dump_database("c", tmp_path, stamp)
    globals_ = backup.dump_globals("c", tmp_path, stamp)
    assert stamp in db.name and stamp in globals_.name


# --------------------------------------------------------------------------
# Env-file loading (an EnvironmentFile equivalent, done in-process)
# --------------------------------------------------------------------------


def test_env_file_parses_plain_pairs(tmp_path):
    f = tmp_path / ".env.prod"
    f.write_text("BACKUP_RCLONE_REMOTE=box\nPOSTGRES_DB=postgres\n")
    assert backup.load_env_file(f) == {
        "BACKUP_RCLONE_REMOTE": "box",
        "POSTGRES_DB": "postgres",
    }


def test_env_file_ignores_comments_and_blanks(tmp_path):
    f = tmp_path / ".env.prod"
    f.write_text("# a comment\n\nA=1\n   \n# B=2\n")
    assert backup.load_env_file(f) == {"A": "1"}


def test_env_file_strips_matched_quotes(tmp_path):
    f = tmp_path / ".env.prod"
    f.write_text("A=\"quoted\"\nB='single'\nC=bare\n")
    assert backup.load_env_file(f) == {"A": "quoted", "B": "single", "C": "bare"}


def test_env_file_keeps_values_containing_equals(tmp_path):
    # JWT secrets and connection strings routinely contain '='.
    f = tmp_path / ".env.prod"
    f.write_text("KEY=abc=def==\n")
    assert backup.load_env_file(f)["KEY"] == "abc=def=="


def test_env_file_values_do_not_override_the_real_environment(tmp_path, monkeypatch):
    # The env file supplies defaults; anything already exported wins.
    monkeypatch.setenv("BACKUP_RCLONE_REMOTE", "from-environment")
    f = tmp_path / ".env.prod"
    f.write_text("BACKUP_RCLONE_REMOTE=from-file\n")
    backup.apply_env_file(f)
    assert backup._env("BACKUP_RCLONE_REMOTE") == "from-environment"


def test_a_missing_env_file_is_a_config_error(tmp_path):
    with pytest.raises(backup.ConfigError, match="env file not found"):
        backup.apply_env_file(tmp_path / "absent")


# --------------------------------------------------------------------------
# The weekly summary
# --------------------------------------------------------------------------


def test_summary_reports_each_artifact_and_its_size(tmp_path):
    a = tmp_path / "postgres-postgres-20260824T000000Z.sql.gz"
    a.write_bytes(b"x" * 1234)
    out = backup.format_summary("prod", "20260824T000000Z", [a], "box:bloom-backups/prod/", True)
    assert "env: prod" in out
    assert "postgres-postgres-20260824T000000Z.sql.gz" in out
    assert "1,234 bytes" in out
    assert "box:bloom-backups/prod/" in out


def test_summary_marks_a_dry_run_as_not_uploaded(tmp_path):
    a = tmp_path / "db.sql.gz"
    a.write_bytes(b"x" * 10)
    out = backup.format_summary("staging", "20260824T000000Z", [a], "", False)
    assert "not uploaded" in out
