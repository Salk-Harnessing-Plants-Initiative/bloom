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
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

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

# Distinctive enough that a test can search a command line for it.
DEPLOY_PASSWORD = "s3cret-prod-pw"


def _host_state_dir(tmp_path) -> Path:
    """The working directory the host provides. The job never creates one.

    Made loose on purpose: it persists between runs, so tightening it is the
    job's business every time, not just when it is new.
    """
    path = tmp_path / "state"
    path.mkdir(mode=0o755, exist_ok=True)
    return path


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Keep one test's env file out of the next test's environment.

    apply_env_file writes straight to os.environ and nothing undoes that, so
    without this a floor or a password loaded by one test silently sets the
    starting conditions of the next one — and the order decides the result.
    """
    for key in backup.ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def _deploy_dir(tmp_path, env_name="prod", **extra):
    """A deploy directory with the env file the script reads its config from."""
    lines = ["BACKUP_RCLONE_REMOTE=box", f"BACKUP_RCLONE_DEST_DIR=bloom-backups/{env_name}",
             f"POSTGRES_PASSWORD={DEPLOY_PASSWORD}",
             # A floor of one byte: these tests are about everything except the
             # host's own free space, which its own tests pin explicitly.
             "BACKUP_MIN_FREE_BYTES=1"]
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


def test_the_compose_project_comes_from_the_environment_not_the_directory():
    # Compose would otherwise derive a project from the directory basename,
    # which differs per host, or fall back to the name pinned in the file —
    # production's. Neither is a property of the environment being asked for.
    for env_name in ("prod", "staging"):
        args = backup.compose_args(Path("/srv/whatever-this-host-calls-it"), env_name)
        assert args[0] == "-p"
        assert args[1] == backup.COMPOSE_PROJECTS[env_name]
        assert "whatever-this-host-calls-it" not in args[1]


def test_compose_args_never_hardcode_a_container_name():
    # The bug this replaces: a hardcoded `bloom_v2_{env}-db-prod-1` is wrong on
    # any host whose deploy directory is named differently. Project names are
    # fine — compose maps those to containers itself; assembled container names
    # are not.
    source = _SCRIPT.read_text()
    assert "-db-prod-" not in source, "container name must not be reconstructed by hand"
    # Resolution goes through `compose ps -q <service>`, so it is correct on any
    # host regardless of what the deploy directory is called.
    assert 'DB_SERVICE = "db-prod"' in source
    # Whitespace-insensitive: the formatter is free to wrap this call, and the
    # guarantee is the arguments, not the line they sit on.
    assert re.search(r'"ps",\s*"-q",\s*DB_SERVICE', source), (
        "the container is no longer resolved by asking compose for the service"
    )


# --------------------------------------------------------------------------
# Which stack a run targets
# --------------------------------------------------------------------------

DEPLOY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy.yml"


def test_each_environment_resolves_its_own_compose_project():
    assert backup.compose_project("prod") == "bloom_v2_prod"
    assert backup.compose_project("staging") == "bloom_v2_staging"


def test_the_two_projects_are_not_the_same_stack():
    # The failure this whole mapping exists to prevent: a staging run that
    # reads staging's env file and dumps the PRODUCTION database into staging's
    # Box folder, logging "staging" at every line.
    assert backup.compose_project("staging") != backup.compose_project("prod")


def test_an_unknown_environment_is_refused_rather_than_defaulted():
    # Falling through to compose's own default would silently mean the name
    # pinned in the file — production's.
    with pytest.raises(backup.ConfigError, match="no compose project known"):
        backup.compose_project("qa")


def test_the_project_name_is_passed_to_compose(tmp_path):
    args = backup.compose_args(tmp_path, "staging")
    assert args[:2] == ["-p", "bloom_v2_staging"], (
        "without -p, compose falls back to the name pinned in the file, which "
        "is production's"
    )
    assert str(tmp_path / ".env.staging") in args


def test_the_projects_match_the_ones_deploy_actually_brings_the_stacks_up_with():
    # This script attaches to stacks another workflow created. If deploy.yml
    # ever renames a project, resolving a container here starts finding nothing
    # — or, worse, finding the other environment's.
    deploy = DEPLOY_WORKFLOW.read_text()
    assert "-p bloom_v2_staging" in deploy, (
        "deploy.yml no longer brings staging up under bloom_v2_staging — "
        "backup.py's COMPOSE_PROJECTS is now wrong"
    )
    # Production is the pinned default, so deploy.yml passes no -p for it.
    compose = (REPO_ROOT / backup.COMPOSE_FILE).read_text()
    assert f"name: {backup.compose_project('prod')}" in compose, (
        "the compose file no longer pins the production project name that "
        "deploy.yml relies on for prod"
    )


def test_the_scheduled_environment_is_production():
    # Staging is reachable for rehearsal; the weekly run is not about staging.
    assert backup.DEFAULT_ENV == "prod"


def test_only_environments_with_a_known_project_are_accepted(tmp_path):
    # argparse choices are derived from the project map; a value it accepts is
    # one a container can actually be resolved for.
    with pytest.raises(SystemExit):
        backup._parse_args(["--env", "qa", "--deploy-dir", str(tmp_path)])


def _run_recorder(seen: list, output: str):
    """Stand in for _run: record the command, return canned stdout."""
    def _fake(cmd, cwd=None):
        seen.append((cmd, cwd))
        return output
    return _fake


def test_the_container_is_looked_up_under_this_environments_project(tmp_path, monkeypatch):
    # compose_args is a pure function with six tests; this is its only caller,
    # and the caller is where "staging run dumps production" would come back —
    # a hardcoded compose_args(deploy_dir, "prod") here is invisible to all six.
    seen: list = []
    monkeypatch.setattr(backup, "_which", lambda name: name)
    monkeypatch.setattr(backup, "_run", _run_recorder(seen, "abc123def456\n"))
    assert backup.resolve_container(tmp_path, "staging") == "abc123def456"
    cmd, cwd = seen[0]
    assert "bloom_v2_staging" in cmd
    assert "bloom_v2_prod" not in cmd, "a staging lookup must not reach production"
    assert cmd[-3:] == ["ps", "-q", backup.DB_SERVICE]
    assert cwd == tmp_path


def test_a_staging_run_looks_up_the_staging_stack(tmp_path, monkeypatch):
    # End to end through main(): --env has to reach the container lookup, not
    # just compose_args' argument list.
    _deploy_dir(tmp_path, env_name="staging")
    monkeypatch.setenv("BACKUP_STATE_DIR", str(_host_state_dir(tmp_path)))
    monkeypatch.setattr(backup, "_which", lambda name: name)
    seen: list = []
    monkeypatch.setattr(backup, "_run", _run_recorder(seen, "abc123def456\n"))
    artifact = tmp_path / "db.sql.gz"
    artifact.write_bytes(b"x" * 32)
    monkeypatch.setattr(backup, "dump_database", lambda *a: artifact)
    monkeypatch.setattr(backup, "dump_globals", lambda *a: artifact)

    rc = backup.main(["--env", "staging", "--deploy-dir", str(tmp_path), "--dry-run"])
    assert rc == backup.EXIT_OK
    lookup = next(cmd for cmd, _ in seen if "ps" in cmd)
    assert "bloom_v2_staging" in lookup
    assert "bloom_v2_prod" not in lookup


def test_a_stopped_stack_is_a_config_error_not_an_empty_backup(tmp_path, monkeypatch):
    # parse_container_id's error has to survive the trip through its caller.
    monkeypatch.setattr(backup, "_which", lambda name: name)
    monkeypatch.setattr(backup, "_run", _run_recorder([], "\n"))
    with pytest.raises(backup.ConfigError, match="no running"):
        backup.resolve_container(tmp_path, "prod")


# --------------------------------------------------------------------------
# Artifact verification
# --------------------------------------------------------------------------


def _write_gz(path: Path, payload: bytes) -> Path:
    with gzip.open(path, "wb") as handle:
        handle.write(payload)
    return path


def _database_dump(rows: int = 100, complete: bool = True) -> bytes:
    """A plain pg_dump, in the shape pg_dump really writes one."""
    body = ["--", "-- PostgreSQL database dump", "--", "",
            "COPY public.plants (id, barcode) FROM stdin;"]
    body += [f"{n}\tBRC{n:05d}" for n in range(rows)]
    body += ["\\.", ""]
    if complete:
        body.append("-- PostgreSQL database dump complete")
    return ("\n".join(body) + "\n").encode()


def _globals_dump(roles: int = 5, complete: bool = True) -> bytes:
    """A pg_dumpall --globals-only dump, in the shape pg_dumpall writes one."""
    body = ["--", "-- PostgreSQL database cluster dump", "--", ""]
    for n in range(roles):
        body += [f"CREATE ROLE bloom_role_{n};",
                 f"ALTER ROLE bloom_role_{n} WITH NOSUPERUSER LOGIN;"]
    if complete:
        body.append("-- PostgreSQL database cluster dump complete")
    return ("\n".join(body) + "\n").encode()


def _dump_writer(seen: list, payload: bytes):
    """Stand in for the real pipeline: record the call, write a real dump."""
    def _write(cmd, out, env=None):
        seen.append(cmd)
        _write_gz(out, payload)
    return _write


def _dump_recorder(calls: list, payload: bytes):
    """Like _dump_writer, but keeps the environment each command was given."""
    def _write(cmd, out, env=None):
        calls.append((cmd, env))
        _write_gz(out, payload)
    return _write


def test_a_good_artifact_verifies_and_returns_its_size(tmp_path):
    artifact = _write_gz(tmp_path / "dump.sql.gz", b"CREATE TABLE x();" * 500)
    assert backup.verify_artifact(artifact, min_bytes=16) == artifact.stat().st_size


def test_an_undersized_artifact_is_rejected(tmp_path):
    artifact = _write_gz(tmp_path / "dump.sql.gz", b"")
    with pytest.raises(backup.VerificationError, match="below the"):
        backup.verify_artifact(artifact, min_bytes=backup.MIN_DATABASE_BYTES)


def test_a_missing_artifact_is_rejected(tmp_path):
    with pytest.raises(backup.VerificationError, match="never written"):
        backup.verify_artifact(tmp_path / "absent.sql.gz", min_bytes=1)


def test_database_floor_is_above_the_globals_floor():
    assert backup.MIN_DATABASE_BYTES > backup.MIN_GLOBALS_BYTES > 0


def _writer_per_artifact(database: bytes, globals_: bytes):
    """Write a different payload per artifact, so one floor can fail alone."""
    def _write(cmd, out, env=None):
        out.write_bytes(database if out.name.startswith("postgres-") else globals_)
    return _write


def test_an_empty_database_dump_is_caught_even_when_the_globals_are_fine(
        tmp_path, monkeypatch):
    # Pins the size floor at the database dump's call site. Deleting that one
    # line uploads a 0-byte artifact and exits 0 — there are two such files on
    # Box already from before this check existed.
    _deploy_dir(tmp_path)
    monkeypatch.setenv("BACKUP_STATE_DIR", str(_host_state_dir(tmp_path)))
    monkeypatch.setattr(backup, "_which", lambda name: name)
    monkeypatch.setattr(backup, "resolve_container", lambda *a: "container123")
    monkeypatch.setattr(backup, "_stream_to_gzip",
                        _writer_per_artifact(b"", b"x" * 1000))
    uploaded: list = []
    monkeypatch.setattr(backup, "upload", lambda *a: uploaded.append(a))

    assert backup.main(["--env", "prod", "--deploy-dir", str(tmp_path)]) == backup.EXIT_VERIFY
    assert not uploaded, "an empty database dump must never reach Box"


def test_an_empty_globals_dump_is_caught_even_when_the_database_is_fine(
        tmp_path, monkeypatch):
    # The mirror, pinning the floor at the globals call site.
    _deploy_dir(tmp_path)
    monkeypatch.setenv("BACKUP_STATE_DIR", str(_host_state_dir(tmp_path)))
    monkeypatch.setattr(backup, "_which", lambda name: name)
    monkeypatch.setattr(backup, "resolve_container", lambda *a: "container123")
    monkeypatch.setattr(backup, "_stream_to_gzip",
                        _writer_per_artifact(b"x" * 5000, b""))
    uploaded: list = []
    monkeypatch.setattr(backup, "upload", lambda *a: uploaded.append(a))

    assert backup.main(["--env", "prod", "--deploy-dir", str(tmp_path)]) == backup.EXIT_VERIFY
    assert not uploaded


def test_a_bad_dump_exits_on_its_own_code_not_the_config_one(tmp_path, monkeypatch):
    # Exit 2 tells the operator to go and look at .env and rclone. A short or
    # corrupt dump is the one failure this job exists to catch, so it gets its
    # own code and the wiki's table can stay true.
    _deploy_dir(tmp_path)
    monkeypatch.setenv("BACKUP_STATE_DIR", str(_host_state_dir(tmp_path)))
    monkeypatch.setattr(backup, "_which", lambda name: name)
    monkeypatch.setattr(backup, "resolve_container", lambda *a: "container123")
    monkeypatch.setattr(backup, "_stream_to_gzip",
                        lambda cmd, out, env=None: out.write_bytes(b""))

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
    monkeypatch.setenv("BACKUP_STATE_DIR", str(_host_state_dir(tmp_path)))
    monkeypatch.setattr(backup, "_which", lambda name: name)
    monkeypatch.setattr(backup, "resolve_container", lambda *a: "container123")

    def _failing_dump(container, work_dir, timestamp, password):
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
    monkeypatch.setenv("BACKUP_STATE_DIR", str(_host_state_dir(tmp_path)))
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
    monkeypatch.setenv("BACKUP_STATE_DIR", str(_host_state_dir(tmp_path)))
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


def test_a_nonzero_rclone_fails_the_upload(tmp_path, monkeypatch):
    # A real non-zero exit, not a raised CalledProcessError: upload() must not
    # swallow one and hand main() a destination for a folder Box never got.
    fake_rclone = tmp_path / "rclone"
    fake_rclone.write_text("#!/bin/sh\necho 'quota exceeded' >&2\nexit 7\n")
    fake_rclone.chmod(0o755)
    monkeypatch.setenv("BACKUP_RCLONE_REMOTE", "box")
    monkeypatch.setattr(backup, "_which",
                        lambda name: str(fake_rclone) if name == "rclone" else name)
    work = tmp_path / "bloom-backup-run"
    work.mkdir()

    with pytest.raises(subprocess.CalledProcessError) as failure:
        backup.upload([work / "database.sql.gz"], "prod", "20260824T000000Z")
    assert failure.value.returncode == 7


def test_a_failed_upload_never_reports_a_destination(tmp_path, monkeypatch, capsys):
    # The failure this guards: rclone fails, the run still exits 0 and prints a
    # destination, GitHub goes green and nothing is on Box. The summary is what
    # the weekly glance reads, so it must not name a folder that does not exist.
    _deploy_dir(tmp_path)
    monkeypatch.setenv("BACKUP_STATE_DIR", str(_host_state_dir(tmp_path)))
    monkeypatch.setattr(backup, "_which", lambda name: name)
    monkeypatch.setattr(backup, "resolve_container", lambda *a: "container123")
    artifact = tmp_path / "db.sql.gz"
    artifact.write_bytes(b"x" * 32)
    monkeypatch.setattr(backup, "dump_database", lambda *a: artifact)
    monkeypatch.setattr(backup, "dump_globals", lambda *a: artifact)

    def _failing_upload(artifacts, env_name, timestamp):
        raise subprocess.CalledProcessError(7, ["rclone", "copy"])

    monkeypatch.setattr(backup, "upload", _failing_upload)

    rc = backup.main(["--env", "prod", "--deploy-dir", str(tmp_path)])
    assert rc == backup.EXIT_SUBPROCESS
    assert rc != backup.EXIT_OK
    assert "destination:" not in capsys.readouterr().out


def test_an_upload_that_cannot_be_configured_is_a_config_error(tmp_path, monkeypatch):
    # The other branch out of upload(): exit 2 sends the operator to .env and
    # rclone, which is where a missing remote is fixed.
    _deploy_dir(tmp_path)
    monkeypatch.setenv("BACKUP_STATE_DIR", str(_host_state_dir(tmp_path)))
    monkeypatch.setattr(backup, "_which", lambda name: name)
    monkeypatch.setattr(backup, "resolve_container", lambda *a: "container123")
    artifact = tmp_path / "db.sql.gz"
    artifact.write_bytes(b"x" * 32)
    monkeypatch.setattr(backup, "dump_database", lambda *a: artifact)
    monkeypatch.setattr(backup, "dump_globals", lambda *a: artifact)

    def _unconfigured_upload(artifacts, env_name, timestamp):
        raise backup.ConfigError("BACKUP_RCLONE_REMOTE is not set")

    monkeypatch.setattr(backup, "upload", _unconfigured_upload)

    assert backup.main(["--env", "prod", "--deploy-dir", str(tmp_path)]) == \
        backup.EXIT_CONFIG


def test_a_successful_upload_reports_the_folder_it_wrote(tmp_path, monkeypatch, capsys):
    # The mirror of the two above: the summary names the real destination, so a
    # green run can be checked against Box by eye.
    _deploy_dir(tmp_path)
    monkeypatch.setenv("BACKUP_STATE_DIR", str(_host_state_dir(tmp_path)))
    monkeypatch.setenv("BACKUP_RCLONE_REMOTE", "box")
    monkeypatch.setenv("BACKUP_RCLONE_DEST_DIR", "bloom-backups/prod")
    monkeypatch.setattr(backup, "_which", lambda name: name)
    monkeypatch.setattr(backup, "resolve_container", lambda *a: "container123")
    artifact = tmp_path / "db.sql.gz"
    artifact.write_bytes(b"x" * 32)
    monkeypatch.setattr(backup, "dump_database", lambda *a: artifact)
    monkeypatch.setattr(backup, "dump_globals", lambda *a: artifact)
    monkeypatch.setattr(backup, "_run", lambda cmd, cwd=None: "")

    assert backup.main(["--env", "prod", "--deploy-dir", str(tmp_path)]) == backup.EXIT_OK
    out = capsys.readouterr().out
    assert "destination: box:bloom-backups/prod/" in out


def test_the_job_never_deletes_anything_on_the_remote():
    # This job uploads only. Nothing on Box is ever removed by it, by design.
    source = _SCRIPT.read_text()
    for verb in DESTRUCTIVE_RCLONE_VERBS:
        assert f'"{verb}"' not in source, f"destructive rclone verb in source: {verb}"
    assert "--min-age" not in source
    assert not hasattr(backup, "prune_old_backups")


def test_a_missing_deploy_dir_is_a_config_error(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKUP_STATE_DIR", str(_host_state_dir(tmp_path)))
    rc = backup.main(["--env", "prod", "--deploy-dir", str(tmp_path / "absent")])
    assert rc == backup.EXIT_CONFIG


def test_dry_run_verifies_but_never_uploads(tmp_path, monkeypatch):
    _deploy_dir(tmp_path)
    monkeypatch.setenv("BACKUP_STATE_DIR", str(_host_state_dir(tmp_path)))
    touched = []
    monkeypatch.setattr(backup, "resolve_container", lambda *a: "container123")
    monkeypatch.setattr(backup, "dump_database", lambda *a: tmp_path / "db.sql.gz")
    monkeypatch.setattr(backup, "dump_globals", lambda *a: tmp_path / "globals.sql.gz")
    monkeypatch.setattr(backup, "upload", lambda *a: touched.append("upload"))
    rc = backup.main(["--env", "prod", "--deploy-dir", str(tmp_path), "--dry-run"])
    assert rc == backup.EXIT_OK
    assert not touched, "a dry run must not upload"


def test_the_working_directory_is_removed_on_failure(tmp_path, monkeypatch):
    # The _which patch is what makes this test mean anything. Without it, a host
    # with no rclone — CI included — fails the pre-flight and returns before a
    # working directory is ever made, so every assertion below holds vacuously.
    _deploy_dir(tmp_path)
    state = _host_state_dir(tmp_path)
    monkeypatch.setenv("BACKUP_STATE_DIR", str(state))
    monkeypatch.setattr(backup, "_which", lambda name: name)
    during_the_run: list[list[str]] = []

    def _stack_is_down(*a):
        # Called from inside the working directory's scope, so this is proof
        # the directory existed rather than an assumption that it did.
        during_the_run.append([p.name for p in state.glob("bloom-backup-*")])
        raise backup.ConfigError("stack down")

    monkeypatch.setattr(backup, "resolve_container", _stack_is_down)

    rc = backup.main(["--env", "prod", "--deploy-dir", str(tmp_path)])
    assert rc == backup.EXIT_CONFIG
    assert during_the_run and during_the_run[0], (
        "the run never got as far as making a working directory, so this proves "
        "nothing about removing one"
    )
    leftovers = list(state.glob("bloom-backup-*"))
    assert not leftovers, f"a dump directory outlived a failed run: {leftovers}"


def test_the_working_directory_is_removed_on_success(tmp_path, monkeypatch):
    _deploy_dir(tmp_path)
    state = tmp_path / "state"
    state.mkdir(mode=0o755)
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


def _orphan_in(state: Path) -> Path:
    """A working directory left behind by a run that was killed."""
    orphan = state / "bloom-backup-oldrun"
    orphan.mkdir(parents=True)
    (orphan / "postgres-postgres-20260824T021700Z.sql.gz").write_bytes(b"stale dump")
    return orphan


def test_a_failing_run_sweeps_the_directory_the_env_file_names(tmp_path, monkeypatch):
    # The path is only in the environment once the env file has been read, and
    # the failures this runs on are the ones where that did not happen. Reading
    # the environment instead of the file sent it to a default directory that
    # has nothing to do with this deployment, so the orphan stayed put.
    state = tmp_path / "state"
    orphan = _orphan_in(state)
    env_file = tmp_path / ".env.prod"
    env_file.write_text(f"BACKUP_STATE_DIR={state}\n")
    monkeypatch.delenv("BACKUP_STATE_DIR", raising=False)

    assert backup.sweep_best_effort(env_file) == 1
    assert not orphan.exists(), "a failing run stranded a full plaintext dump"


def test_an_unreadable_env_file_sweeps_nothing_rather_than_guessing(
        tmp_path, monkeypatch):
    # The safety net has a limit, and this is it. With no readable config there
    # is no way to know which directory is this job's, and a directory we had to
    # guess at is not one to delete from.
    default_location = tmp_path / "home/.local/state/bloom-weekly-backup"
    not_ours = _orphan_in(default_location)
    monkeypatch.delenv("BACKUP_STATE_DIR", raising=False)
    monkeypatch.setattr(backup, "DEFAULT_STATE_DIR", str(default_location))

    assert backup.sweep_best_effort(tmp_path / "no-such.env") == 0
    assert not_ours.exists(), "the default location must not be swept blind"


def test_the_sweep_leaves_everything_that_is_not_its_own_working_directory(tmp_path, caplog):
    # It runs unattended on a host holding the deploy tree and the database.
    state = tmp_path / "state"
    ours = _orphan_in(state)
    a_file = state / "bloom-backup-notes.txt"
    a_file.write_text("not a working directory")
    elsewhere = tmp_path / "somebody-elses-data"
    elsewhere.mkdir()
    (elsewhere / "keep.txt").write_text("keep me")
    link = state / "bloom-backup-link"
    link.symlink_to(elsewhere, target_is_directory=True)
    unrelated = state / "important-data"
    unrelated.mkdir()

    with caplog.at_level("ERROR"):
        assert backup.sweep_stale_work_dirs(state) == 1
    assert "could not remove" not in caplog.text, (
        "a file or a symlink is not a leftover working directory, and calling "
        "one out as a dump that could not be removed sends someone looking"
    )
    assert not ours.exists()
    assert a_file.exists(), "a file is not a working directory"
    assert link.is_symlink(), "a symlink must be left alone, not followed"
    assert (elsewhere / "keep.txt").exists(), "deleting through a symlink escaped"
    assert unrelated.exists()


def test_the_script_deletes_in_exactly_one_place():
    # The whole delete surface of a job that runs unattended, as root's
    # neighbour, on a host holding the deploy tree and the live database.
    # TemporaryDirectory removing the directory it created itself is not
    # counted here — it can only ever remove its own.
    source = _SCRIPT.read_text()
    for call in ("rmtree(", "os.remove(", "os.unlink(", "os.rmdir(",
                 ".unlink(", "shutil.move("):
        expected = 1 if call == "rmtree(" else 0
        assert source.count(call) == expected, (
            f"{call} appears {source.count(call)} time(s); this job deletes in "
            "one place and nowhere else"
        )
    # And that one call sits inside the sweep, not somewhere new.
    sweep = source.split("def sweep_stale_work_dirs")[1].split("\ndef ")[0]
    assert "rmtree(" in sweep


def test_the_sweep_matches_only_this_jobs_own_directories_and_never_recurses():
    source = _SCRIPT.read_text()
    assert 'glob("bloom-backup-*")' in source, (
        "the pattern decides what gets deleted; it must name this job's prefix"
    )
    assert "rglob" not in source, (
        "a recursive glob would reach below the working directory and delete "
        "matches anywhere underneath it"
    )
    assert 'prefix="bloom-backup-"' in source, (
        "the prefix the sweep deletes and the prefix a run creates must match"
    )


def test_a_sweep_touches_nothing_outside_its_own_directory(tmp_path):
    # The host layout as it really is: the deploy tree, the database's data,
    # and the manual backups all live under the same /data/bloom parent as the
    # working directory this job is allowed to delete from.
    root = tmp_path
    state = root / "data/bloom/backup-work/prod"
    state.mkdir(parents=True)
    ours = _orphan_in(state)

    keep = [
        root / "data/bloom/production/docker-compose.prod.yml",
        root / "data/bloom/production/.env.prod",
        root / "data/bloom/production/volumes/db/data/postgresql.conf",
        root / "data/bloom/staging/.env.staging",
        root / "data/bloom/backups/manual-dump.sql.gz",
        root / "data/bloom/backup-work/staging/bloom-backup-otherenv/dump.sql.gz",
    ]
    for path in keep:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"do not touch")

    assert backup.sweep_stale_work_dirs(state) == 1
    assert not ours.exists()
    for path in keep:
        assert path.exists(), f"the sweep reached {path.relative_to(root)}"
        assert path.read_bytes() == b"do not touch"


def test_the_sweep_counts_what_it_removed_not_what_it_found(tmp_path):
    # It reported the number it globbed. A leftover owned by another user
    # cannot be removed, and saying it was is how a plaintext dump outlives the
    # week the runbook promises while the log says it is gone.
    state = tmp_path / "state"
    stuck = _orphan_in(state)
    state.chmod(0o500)  # readable and listable, but nothing can be unlinked
    try:
        assert backup.sweep_stale_work_dirs(state) == 0, "reported a removal that failed"
        assert stuck.exists()
    finally:
        state.chmod(0o700)


@pytest.mark.skipif(os.geteuid() == 0, reason="root removes files regardless")
def test_a_leftover_that_cannot_be_removed_is_reported(tmp_path, caplog):
    state = tmp_path / "state"
    _orphan_in(state)
    state.chmod(0o500)
    try:
        with caplog.at_level("ERROR"):
            backup.sweep_stale_work_dirs(state)
        assert "may still hold a plaintext dump" in caplog.text
    finally:
        state.chmod(0o700)


def test_a_broken_sweep_does_not_mask_the_error_that_caused_it(tmp_path, monkeypatch):
    # The sweep runs on an already-failing path; it must report the config
    # error, not an OSError raised while tidying up.
    monkeypatch.setenv("BACKUP_STATE_DIR", str(_host_state_dir(tmp_path)))

    def _explode(_state_dir):
        raise OSError("state directory went away")

    monkeypatch.setattr(backup, "sweep_stale_work_dirs", _explode)
    rc = backup.main(["--env", "prod", "--deploy-dir", str(tmp_path / "gone")])
    assert rc == backup.EXIT_CONFIG


def test_the_working_copy_is_not_readable_by_other_users(tmp_path, monkeypatch):
    # It holds a full plaintext dump, auth.users included, on a host whose
    # runner runs other jobs.
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
    monkeypatch.setenv("BACKUP_STATE_DIR", str(_host_state_dir(tmp_path)))
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
    monkeypatch.setenv("POSTGRES_PASSWORD", DEPLOY_PASSWORD)
    seen: list[list[str]] = []
    monkeypatch.setattr(backup, "_stream_to_gzip", _dump_writer(seen, _database_dump()))
    monkeypatch.setattr(backup, "verify_artifact", lambda *a, **k: 999999)
    monkeypatch.setattr(backup, "_which", lambda name: name)
    backup.dump_database("container123", tmp_path, "20260824T000000Z", DEPLOY_PASSWORD)
    cmd = seen[0]
    assert "pg_dump" in cmd
    assert "--no-owner" not in cmd
    assert "--no-privileges" not in cmd
    assert "--no-acl" not in cmd


def test_the_dump_gives_up_on_a_lock_rather_than_queueing_behind_it(tmp_path, monkeypatch):
    # pg_dump takes ACCESS SHARE on every table. Waiting unboundedly for one
    # blocks every other reader on that table behind it, so a stuck dump becomes
    # a stalled application.
    monkeypatch.setenv("POSTGRES_PASSWORD", DEPLOY_PASSWORD)
    seen: list[list[str]] = []
    monkeypatch.setattr(backup, "_stream_to_gzip", _dump_writer(seen, _database_dump()))
    monkeypatch.setattr(backup, "verify_artifact", lambda *a, **k: 999999)
    monkeypatch.setattr(backup, "_which", lambda name: name)

    backup.dump_database("container123", tmp_path, "20260824T000000Z", DEPLOY_PASSWORD)
    waits = [arg for arg in seen[0] if arg.startswith("--lock-wait-timeout=")]
    assert waits, "an unbounded wait stalls readers for as long as the lock is held"
    assert int(waits[0].split("=")[1]) > 0


def test_the_lock_wait_is_short_enough_to_bound_a_stall():
    # Asserted against a literal, not the constant: the fixture above would
    # follow the constant anywhere, including up to an hour.
    assert 0 < backup.LOCK_WAIT_TIMEOUT_MS <= 120_000, (
        "the wait is how long the application can stall on a table before the "
        "run gives up"
    )


def test_globals_are_dumped_alongside_the_database(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTGRES_PASSWORD", DEPLOY_PASSWORD)
    seen: list[list[str]] = []
    monkeypatch.setattr(backup, "_stream_to_gzip", _dump_writer(seen, _globals_dump()))
    monkeypatch.setattr(backup, "verify_artifact", lambda *a, **k: 999)
    monkeypatch.setattr(backup, "_which", lambda name: name)
    backup.dump_globals("container123", tmp_path, "20260824T000000Z", DEPLOY_PASSWORD)
    assert "pg_dumpall" in seen[0]
    assert "--globals-only" in seen[0]


# --------------------------------------------------------------------------
# Authenticating against the database
# --------------------------------------------------------------------------


def test_the_database_dump_authenticates(tmp_path, monkeypatch):
    # db-prod sets no PGPASSWORD of its own (docker-compose.dev.yml does, which
    # is why dev calls work without one), so a dump that passes no password dies
    # at `fe_sendauth: no password supplied` and the run produces no backup.
    calls: list = []
    monkeypatch.setenv("POSTGRES_PASSWORD", DEPLOY_PASSWORD)
    monkeypatch.setattr(backup, "_stream_to_gzip", _dump_recorder(calls, _database_dump()))
    monkeypatch.setattr(backup, "verify_artifact", lambda *a, **k: 999999)
    monkeypatch.setattr(backup, "_which", lambda name: name)

    backup.dump_database("container123", tmp_path, "20260824T000000Z", DEPLOY_PASSWORD)
    cmd, env = calls[0]
    assert env["PGPASSWORD"] == DEPLOY_PASSWORD
    assert cmd[cmd.index("-e") + 1] == "PGPASSWORD"


def test_the_globals_dump_authenticates_too(tmp_path, monkeypatch):
    calls: list = []
    monkeypatch.setenv("POSTGRES_PASSWORD", DEPLOY_PASSWORD)
    monkeypatch.setattr(backup, "_stream_to_gzip", _dump_recorder(calls, _globals_dump()))
    monkeypatch.setattr(backup, "verify_artifact", lambda *a, **k: 999)
    monkeypatch.setattr(backup, "_which", lambda name: name)

    backup.dump_globals("container123", tmp_path, "20260824T000000Z", DEPLOY_PASSWORD)
    cmd, env = calls[0]
    assert env["PGPASSWORD"] == DEPLOY_PASSWORD
    assert cmd[cmd.index("-e") + 1] == "PGPASSWORD"


def test_the_password_is_never_written_on_a_command_line(monkeypatch):
    # `-e PGPASSWORD=<value>` — the form deploy.yml uses — puts production's
    # password in the host's process list, where any user's `ps` reads it. The
    # bare form makes docker copy it out of this process instead.
    monkeypatch.setenv("POSTGRES_PASSWORD", DEPLOY_PASSWORD)
    monkeypatch.setattr(backup, "_which", lambda name: name)
    cmd, env = backup.dump_command(
        "container123", ["pg_dump", "-U", "supabase_admin"], DEPLOY_PASSWORD
    )
    assert not any(DEPLOY_PASSWORD in arg for arg in cmd), cmd
    assert env["PGPASSWORD"] == DEPLOY_PASSWORD


def test_only_the_dump_process_is_given_the_password(tmp_path, monkeypatch):
    # gzip is started with env=None, which means "inherit everything this
    # process has" — so asserting on that argument proves nothing. What matters
    # is whether the password is in the environment it inherits.
    monkeypatch.setattr(subprocess, "Popen", subprocess.Popen)
    backup.apply_env_file(_deploy_dir(tmp_path) / ".env.prod")
    assert "POSTGRES_PASSWORD" not in os.environ, (
        "anything left here reaches gzip and rclone, which inherit it wholesale"
    )
    backup._stream_to_gzip(
        [sys.executable, "-c",
         "import os; print(os.environ.get('PGPASSWORD', 'absent'))"],
        tmp_path / "out.gz",
        env={**os.environ, "PGPASSWORD": DEPLOY_PASSWORD},
    )
    with gzip.open(tmp_path / "out.gz", "rb") as handle:
        assert handle.read().strip() == DEPLOY_PASSWORD.encode(), \
            "the dump process must receive the password it authenticates with"


def test_the_password_does_not_reach_a_child_that_inherits_our_environment(
        tmp_path, monkeypatch):
    # The real shape of the leak: rclone and gzip are started with no explicit
    # environment, so they receive a copy of everything this process holds.
    backup.apply_env_file(_deploy_dir(tmp_path) / ".env.prod")
    seen = subprocess.run(
        [sys.executable, "-c",
         "import os; print([k for k in os.environ if 'PASSWORD' in k])"],
        capture_output=True, text=True,
    ).stdout.strip()
    assert seen == "[]", f"a child inheriting our environment sees {seen}"


def test_the_password_still_reaches_the_dump_it_authenticates(tmp_path):
    # The other half: keeping it out of the environment must not stop the one
    # command that needs it from getting it.
    found = backup.apply_env_file(_deploy_dir(tmp_path) / ".env.prod")
    assert backup._pg_password(found) == DEPLOY_PASSWORD


# --------------------------------------------------------------------------
# A blank value is not a value
# --------------------------------------------------------------------------


def test_a_blank_line_counts_as_absent_for_every_key(tmp_path, monkeypatch):
    # `KEY=` is how someone asks for the default. Taken literally it becomes an
    # empty string, and `_env`'s fallback never fires.
    f = tmp_path / ".env.prod"
    f.write_text("".join(f"{key}=\n" for key in backup.ENV_KEYS))
    for key in backup.ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    assert backup.apply_env_file(f) == {}
    for key in backup.ENV_KEYS:
        assert key not in os.environ, f"{key} was imported as an empty string"


def test_a_blank_working_directory_does_not_become_the_current_one(tmp_path, monkeypatch):
    # The harm this guards: the workflow cds into the deploy directory before
    # running, so an empty BACKUP_STATE_DIR resolves to the git checkout — which
    # then gets chmodded to 0700 with a plaintext dump written inside it.
    f = tmp_path / ".env.prod"
    f.write_text("BACKUP_STATE_DIR=\n")
    monkeypatch.delenv("BACKUP_STATE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)

    backup.apply_env_file(f)
    assert backup._state_dir() != Path("."), "an empty value became the cwd"
    assert backup._state_dir() == Path(backup.DEFAULT_STATE_DIR).expanduser()


def test_a_blank_destination_does_not_upload_to_the_root_of_the_remote(
        tmp_path, monkeypatch):
    f = tmp_path / ".env.prod"
    f.write_text("BACKUP_RCLONE_REMOTE=box\nBACKUP_RCLONE_DEST_DIR=\n")
    for key in ("BACKUP_RCLONE_REMOTE", "BACKUP_RCLONE_DEST_DIR"):
        monkeypatch.delenv(key, raising=False)

    backup.apply_env_file(f)
    remote, dest_dir = backup.backup_destination("prod")
    assert dest_dir == "bloom-backups/prod", f"uploads would land at {remote}:{dest_dir}/"


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a 0000 file regardless")
def test_an_unreadable_env_file_is_a_config_error(tmp_path, monkeypatch):
    # The runner writes .env.<env> at mode 600 and the deploy user reads it.
    # deploy.yml carries a whole step for the case where those two diverge.
    # Unguarded this exits 1, which the exit table defines as "subprocess
    # failed" — sending the operator to look at docker for a permissions bug.
    _deploy_dir(tmp_path)
    (tmp_path / ".env.prod").chmod(0o000)
    monkeypatch.setattr(backup, "_which", lambda name: name)

    rc = backup.main(["--env", "prod", "--deploy-dir", str(tmp_path)])
    assert rc == backup.EXIT_CONFIG
    assert rc != backup.EXIT_SUBPROCESS


def test_a_missing_password_is_a_config_error_before_the_dump_window(tmp_path, monkeypatch):
    # Exit 2 sends the operator to the env file. Reaching pg_dump first would
    # spend the whole dump window and report a subprocess failure instead.
    lines = ["BACKUP_RCLONE_REMOTE=box", "BACKUP_RCLONE_DEST_DIR=bloom-backups/prod"]
    (tmp_path / ".env.prod").write_text("\n".join(lines) + "\n")
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    monkeypatch.setenv("BACKUP_STATE_DIR", str(_host_state_dir(tmp_path)))
    monkeypatch.setattr(backup, "_which", lambda name: name)
    resolved: list = []
    monkeypatch.setattr(backup, "resolve_container",
                        lambda *a: resolved.append(a) or "container123")

    rc = backup.main(["--env", "prod", "--deploy-dir", str(tmp_path)])
    assert rc == backup.EXIT_CONFIG
    assert not resolved, "the run must fail before it touches the stack"


def test_an_empty_password_counts_as_missing(monkeypatch):
    monkeypatch.setenv("POSTGRES_PASSWORD", "")
    with pytest.raises(backup.ConfigError, match="POSTGRES_PASSWORD"):
        backup._pg_password({})


# --------------------------------------------------------------------------
# Free space, which the database also depends on
# --------------------------------------------------------------------------


def _free_bytes(monkeypatch, free: int):
    """Pin what disk_usage reports, so no test depends on the host's own disk."""
    monkeypatch.setattr(
        backup.shutil, "disk_usage",
        lambda path: SimpleNamespace(total=free * 2, used=free, free=free),
    )


def test_room_for_the_dump_lets_the_run_start(tmp_path, monkeypatch):
    _free_bytes(monkeypatch, backup.DEFAULT_MIN_FREE_BYTES + 1)
    assert backup.verify_free_space(tmp_path) == backup.DEFAULT_MIN_FREE_BYTES + 1


def test_a_volume_too_full_to_hold_a_dump_stops_the_run(tmp_path, monkeypatch):
    # The working copy shares a filesystem with volumes/db/data, so a dump that
    # fills it stops Postgres writing WAL — a backup job taking production with
    # it, unattended, at 02:17 on a Sunday.
    _free_bytes(monkeypatch, backup.DEFAULT_MIN_FREE_BYTES - 1)
    with pytest.raises(backup.ConfigError, match="below the"):
        backup.verify_free_space(tmp_path)


def test_the_floor_says_how_much_was_free_and_how_much_was_needed(tmp_path, monkeypatch):
    # "not enough space" with no numbers leaves the operator running df by hand.
    _free_bytes(monkeypatch, 1024)
    monkeypatch.setenv("BACKUP_MIN_FREE_BYTES", "4096")
    with pytest.raises(backup.ConfigError) as refused:
        backup.verify_free_space(tmp_path)
    assert "1,024" in str(refused.value) and "4,096" in str(refused.value)


def test_the_floor_is_tunable_per_host(tmp_path, monkeypatch):
    # Hosts differ, and a floor nobody can lower is one somebody works around.
    _free_bytes(monkeypatch, 5000)
    monkeypatch.setenv("BACKUP_MIN_FREE_BYTES", "4096")
    assert backup.verify_free_space(tmp_path) == 5000


@pytest.mark.parametrize("value", ["20GB", "", "-1", "0", "1.5"])
def test_an_unusable_floor_is_a_config_error_not_a_silent_default(value, monkeypatch):
    # Falling back to the default on a typo would run with a floor nobody chose;
    # an empty value is the one case that legitimately means "use the default".
    monkeypatch.setenv("BACKUP_MIN_FREE_BYTES", value)
    if value == "":
        assert backup._min_free_bytes() == backup.DEFAULT_MIN_FREE_BYTES
    else:
        with pytest.raises(backup.ConfigError, match="BACKUP_MIN_FREE_BYTES"):
            backup._min_free_bytes()


def test_a_full_volume_stops_the_run_before_the_dump_window(tmp_path, monkeypatch):
    _deploy_dir(tmp_path)
    monkeypatch.setenv("BACKUP_STATE_DIR", str(_host_state_dir(tmp_path)))
    monkeypatch.setattr(backup, "_which", lambda name: name)
    monkeypatch.setenv("BACKUP_MIN_FREE_BYTES", "4096")
    _free_bytes(monkeypatch, 1024)
    resolved: list = []
    monkeypatch.setattr(backup, "resolve_container",
                        lambda *a: resolved.append(a) or "container123")

    rc = backup.main(["--env", "prod", "--deploy-dir", str(tmp_path)])
    assert rc == backup.EXIT_CONFIG
    assert not resolved, "the run must fail before it starts writing"


def test_room_an_orphan_is_holding_is_reclaimed_before_the_space_check(
        tmp_path, monkeypatch):
    # A killed run's leftover dump can be gigabytes. Measuring before sweeping
    # would refuse a run over space that was about to come back.
    _deploy_dir(tmp_path)
    state = tmp_path / "state"
    state.mkdir(mode=0o755)
    monkeypatch.setenv("BACKUP_STATE_DIR", str(state))
    monkeypatch.setattr(backup, "_which", lambda name: name)
    order: list[str] = []
    monkeypatch.setattr(backup, "sweep_stale_work_dirs",
                        lambda d: order.append("sweep") or 0)
    monkeypatch.setattr(backup, "verify_free_space",
                        lambda d: order.append("check space") or 1)
    monkeypatch.setattr(backup, "resolve_container", lambda *a: "container123")
    artifact = tmp_path / "db.sql.gz"
    artifact.write_bytes(b"x" * 32)
    monkeypatch.setattr(backup, "dump_database", lambda *a: artifact)
    monkeypatch.setattr(backup, "dump_globals", lambda *a: artifact)

    backup.main(["--env", "prod", "--deploy-dir", str(tmp_path), "--dry-run"])
    assert order == ["sweep", "check space"]


def test_the_defaults_files_carry_the_floor():
    # The floor is a host property, so it belongs in the env surface rather
    # than only in the script's own default. Asserted against a literal: the
    # tests above take their expectations from the constant, so lowering it
    # would otherwise change what they check rather than fail them.
    for name in ("prod", "staging"):
        value = int(_defaults_value(name, "BACKUP_MIN_FREE_BYTES"))
        assert value >= 10 * 1024**3, (
            f".env.{name}.defaults sets a {value}-byte floor, which no longer "
            "stands between a dump and a full volume"
        )


def test_the_scripts_own_floor_is_not_a_token_value():
    assert backup.DEFAULT_MIN_FREE_BYTES >= 10 * 1024**3


def _defaults_value(env_name: str, key: str) -> str:
    for line in (REPO_ROOT / f".env.{env_name}.defaults").read_text().splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1]
    raise AssertionError(f"{key} missing from .env.{env_name}.defaults")


def test_the_working_copy_is_configured_onto_the_data_volume():
    # The script's own fallback is under the invoking user's home, which on the
    # deploy host is the root filesystem — far too small for a dump of a
    # database that lives on the data volume, and filling it takes docker, the
    # Actions runner and sshd with it.
    for name in ("prod", "staging"):
        state_dir = _defaults_value(name, "BACKUP_STATE_DIR")
        assert state_dir.startswith("/"), f"{name}: must be an absolute path"
        assert not state_dir.startswith("/home/"), (
            f"{name}: {state_dir} is on the root filesystem"
        )
        assert "/data/bloom/" in state_dir, f"{name}: {state_dir}"


def test_neither_environment_writes_into_a_deploy_directory():
    # A working copy inside a deploy directory is a full plaintext dump sitting
    # in a git checkout that a deployment rewrites.
    for name, deploy_dir in (("prod", "/data/bloom/production"),
                             ("staging", "/data/bloom/staging")):
        assert not _defaults_value(name, "BACKUP_STATE_DIR").startswith(deploy_dir)


# The folders that exist on Box today. Pinned verbatim rather than merely
# asserted to differ: a run uploads wherever this points, so changing it is a
# decision to be made deliberately, not a line edit that CI waves through.
BOX_DESTINATIONS = {
    "prod": "bloom-backups/prod",
    "staging": "bloom-backups/staging",
}


@pytest.mark.parametrize("env_name", sorted(BOX_DESTINATIONS))
def test_each_environment_uploads_to_the_folder_that_exists_on_box(env_name):
    assert _defaults_value(env_name, "BACKUP_RCLONE_DEST_DIR") == BOX_DESTINATIONS[env_name]
    assert _defaults_value(env_name, "BACKUP_RCLONE_REMOTE") == "box"


def test_the_two_environments_do_not_share_a_box_folder():
    # A staging dump landing in the production folder is two files with the
    # right names, the right shape and the wrong database — indistinguishable
    # from that week's backup in a listing.
    assert (_defaults_value("prod", "BACKUP_RCLONE_DEST_DIR")
            != _defaults_value("staging", "BACKUP_RCLONE_DEST_DIR"))


def test_the_two_environments_do_not_share_a_working_directory():
    # They share the host, and the startup sweep removes what it finds, so one
    # directory means a rehearsal can delete a production run's working copy.
    assert (_defaults_value("prod", "BACKUP_STATE_DIR")
            != _defaults_value("staging", "BACKUP_STATE_DIR"))


@pytest.mark.parametrize("relative", [
    "dtaa/bloom/backup-work/prod",   # a typo above the working directory
    "backup-work/prd",               # and one in its own name
])
def test_a_missing_working_directory_is_refused_rather_than_created(
        relative, tmp_path, monkeypatch):
    # The host's working directory is set up by hand. Creating it here would
    # turn a typo in BACKUP_STATE_DIR into a new directory with a full
    # plaintext dump in it, somewhere nobody is looking.
    _deploy_dir(tmp_path)
    (tmp_path / "backup-work").mkdir()
    typo = tmp_path / relative
    monkeypatch.setenv("BACKUP_STATE_DIR", str(typo))
    monkeypatch.setattr(backup, "_which", lambda name: name)
    resolved: list = []
    monkeypatch.setattr(backup, "resolve_container",
                        lambda *a: resolved.append(a) or "container123")

    rc = backup.main(["--env", "prod", "--deploy-dir", str(tmp_path)])
    assert rc == backup.EXIT_CONFIG
    assert not typo.exists(), "the run must not build the path it was given"
    assert not resolved


def test_a_working_directory_the_host_provides_is_used_as_it_stands(
        tmp_path, monkeypatch):
    # The one the host set up, tightened but never replaced.
    _deploy_dir(tmp_path)
    parent = tmp_path / "backup-work"
    parent.mkdir()
    state_dir = parent / "prod"
    state_dir.mkdir(mode=0o755)
    monkeypatch.setenv("BACKUP_STATE_DIR", str(state_dir))
    monkeypatch.setattr(backup, "_which", lambda name: name)
    monkeypatch.setattr(backup, "resolve_container", lambda *a: "container123")
    artifact = tmp_path / "db.sql.gz"
    artifact.write_bytes(b"x" * 32)
    monkeypatch.setattr(backup, "dump_database", lambda *a: artifact)
    monkeypatch.setattr(backup, "dump_globals", lambda *a: artifact)

    rc = backup.main(["--env", "prod", "--deploy-dir", str(tmp_path), "--dry-run"])
    assert rc == backup.EXIT_OK
    assert state_dir.is_dir()
    assert state_dir.stat().st_mode & 0o777 == 0o700


def test_an_unusable_state_directory_is_a_config_error(tmp_path, monkeypatch):
    # Creating it can fail on a volume this user does not own. Exit 2 naming the
    # path beats a PermissionError traceback that exits 1 as "subprocess failed".
    _deploy_dir(tmp_path)
    blocked = tmp_path / "blocked"
    blocked.mkdir(mode=0o500)
    monkeypatch.setenv("BACKUP_STATE_DIR", str(blocked / "work"))
    monkeypatch.setattr(backup, "_which", lambda name: name)

    rc = backup.main(["--env", "prod", "--deploy-dir", str(tmp_path)])
    assert rc == backup.EXIT_CONFIG


def test_an_artifact_is_readable_only_by_the_user_that_wrote_it(tmp_path):
    # It is a full plaintext dump, auth.users included. The 0700 directories
    # above it are what stop other accounts today; an owner-only file is what
    # keeps one loosened directory from being enough on its own.
    out = tmp_path / "dump.sql.gz"
    backup._stream_to_gzip(["printf", "dump"], out)
    assert out.stat().st_mode & 0o777 == 0o600


def test_an_artifact_is_owner_only_from_the_moment_it_exists(tmp_path):
    # Tightening the mode after the dump finishes would leave it loose for the
    # whole dump — the only part of the run where the file is growing and the
    # window is measured in tens of minutes. A dump that fails partway proves
    # which of the two it is.
    out = tmp_path / "partial.sql.gz"
    with pytest.raises(subprocess.CalledProcessError):
        backup._stream_to_gzip(["false"], out)
    assert out.exists(), "the partial artifact is the thing being checked"
    assert out.stat().st_mode & 0o777 == 0o600


def test_a_dumped_artifact_reaches_disk_owner_only(tmp_path, monkeypatch):
    # The same guarantee through the real dump path rather than the helper.
    monkeypatch.setenv("POSTGRES_PASSWORD", DEPLOY_PASSWORD)
    monkeypatch.setattr(backup, "_which", lambda name: name)
    monkeypatch.setattr(backup, "verify_artifact", lambda *a, **k: 999999)
    monkeypatch.setattr(
        backup, "dump_command",
        lambda container, argv, password: ([sys.executable, "-c", "print('dump')"], None),
    )
    artifact = backup.dump_database(
        "container123", tmp_path, "20260824T000000Z", DEPLOY_PASSWORD
    )
    assert artifact.stat().st_mode & 0o077 == 0, "group or other can read the dump"


def test_a_full_disk_is_not_reported_as_a_dead_pg_dump(tmp_path, monkeypatch, caplog):
    # gzip fails first when the volume fills, and the SIGPIPE it sends upstream
    # makes the source exit non-zero too. Checking the source first sends the
    # operator to look at the database for a disk problem.
    # A gzip that dies without draining stdin, so the source upstream takes a
    # SIGPIPE and exits non-zero too — both processes failing, which is what a
    # full volume actually looks like and the only case where the order decides
    # which one gets reported.
    fake_gzip = tmp_path / "gzip"
    fake_gzip.write_text("#!/bin/sh\nexit 1\n")
    fake_gzip.chmod(0o755)
    monkeypatch.setattr(backup, "_which",
                        lambda name: str(fake_gzip) if name == "gzip" else name)
    flood = "import sys\nfor _ in range(20000): sys.stdout.write('x' * 4096)"

    with caplog.at_level("ERROR"):
        with pytest.raises(subprocess.CalledProcessError) as failure:
            backup._stream_to_gzip([sys.executable, "-c", flood],
                                   tmp_path / "out.gz")
    assert failure.value.cmd == ["gzip"], (
        "the write side is what failed; reporting the source sends the operator "
        "to look at the database for a disk problem"
    )
    assert "bytes free" in caplog.text, "the log must name the real cause"


# --------------------------------------------------------------------------
# The database name, which reaches a filename
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["postgres", "bloom_v2", "_internal", "db$1"])
def test_a_plain_database_name_is_accepted(name, monkeypatch):
    monkeypatch.setenv("POSTGRES_DB", name)
    assert backup._pg_database() == name


@pytest.mark.parametrize("name", [
    "../../../../tmp/pwned",   # the one that matters: escapes the state dir
    "sub/dir",
    "postgres\n",              # a `$` anchor accepts a trailing newline; \Z does not
    "postgres\n../escape",
    "",
    "9lives",
    "a" * 64,                  # past Postgres' own identifier limit
])
def test_a_database_name_that_is_not_an_identifier_is_refused(name, monkeypatch):
    monkeypatch.setenv("POSTGRES_DB", name)
    with pytest.raises(backup.ConfigError, match="POSTGRES_DB"):
        backup._pg_database()


def test_a_traversing_database_name_writes_nothing_outside_the_working_dir(
        tmp_path, monkeypatch):
    # Unchecked, `POSTGRES_DB=../../../../tmp/pwned` lands the dump outside the
    # 0700 state directory at 0644, outside TemporaryDirectory's cleanup and
    # outside the sweep's glob — so nothing ever removes a plaintext auth.users.
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    escape = tmp_path / "escape"
    escape.mkdir()
    monkeypatch.setenv("POSTGRES_DB", f"../{escape.name}/pwned")
    monkeypatch.setattr(backup, "_which", lambda name: name)
    monkeypatch.setattr(backup, "_stream_to_gzip",
                        _dump_writer([], _database_dump()))

    with pytest.raises(backup.ConfigError):
        backup.dump_database("container123", work_dir, "20260824T000000Z", DEPLOY_PASSWORD)
    assert list(escape.iterdir()) == [], "a dump must never be written outside"
    assert list(work_dir.iterdir()) == []


def test_a_bad_database_name_stops_the_run_before_the_dump_window(tmp_path, monkeypatch):
    _deploy_dir(tmp_path)
    monkeypatch.setenv("POSTGRES_DB", "../../../../tmp/pwned")
    monkeypatch.setenv("BACKUP_STATE_DIR", str(_host_state_dir(tmp_path)))
    monkeypatch.setattr(backup, "_which", lambda name: name)
    resolved: list = []
    monkeypatch.setattr(backup, "resolve_container",
                        lambda *a: resolved.append(a) or "container123")

    rc = backup.main(["--env", "prod", "--deploy-dir", str(tmp_path)])
    assert rc == backup.EXIT_CONFIG
    assert not resolved, "the run must fail before it touches the stack"


def test_a_run_dumps_and_uploads_both_artifacts(tmp_path, monkeypatch):
    # The sibling test proves dump_globals works when called. This proves a run
    # calls it: the database dump's GRANT statements name roles only the globals
    # file defines, so shipping one without the other is half a backup.
    _deploy_dir(tmp_path)
    monkeypatch.setenv("BACKUP_STATE_DIR", str(_host_state_dir(tmp_path)))
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
    monkeypatch.setenv("POSTGRES_PASSWORD", DEPLOY_PASSWORD)
    monkeypatch.setattr(backup, "_stream_to_gzip", _dump_writer([], _database_dump()))
    monkeypatch.setattr(backup, "verify_artifact", lambda *a, **k: 999)
    monkeypatch.setattr(backup, "_which", lambda name: name)
    stamp = "20260824T010203Z"
    db = backup.dump_database("c", tmp_path, stamp, DEPLOY_PASSWORD)
    globals_ = backup.dump_globals("c", tmp_path, stamp, DEPLOY_PASSWORD)
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


def test_only_this_jobs_keys_are_imported_from_the_env_file(tmp_path, monkeypatch):
    # Importing the whole file gives rclone its own option surface: one
    # RCLONE_CONFIG line redirects a plaintext auth.users dump to another
    # remote, and LD_PRELOAD runs code as the deploy user. Both defaults files
    # are checked in, so this reach would be writable from a config-only change.
    f = tmp_path / ".env.prod"
    f.write_text(
        "POSTGRES_DB=postgres\n"
        "RCLONE_CONFIG=/tmp/somebody-elses-remotes.conf\n"
        "RCLONE_CONFIG_BOX_TYPE=local\n"
        "LD_PRELOAD=/tmp/evil.so\n"
        "SERVICE_ROLE_KEY=a-jwt-this-job-has-no-use-for\n"
    )
    smuggled = ("RCLONE_CONFIG", "RCLONE_CONFIG_BOX_TYPE",
                "LD_PRELOAD", "SERVICE_ROLE_KEY")
    for key in smuggled:
        monkeypatch.delenv(key, raising=False)

    assert backup.apply_env_file(f) == {"POSTGRES_DB": "postgres"}, (
        "only POSTGRES_DB is this job's to take"
    )
    for key in smuggled:
        assert key not in os.environ, f"{key} must not reach any child process"


def test_the_allowlist_covers_every_key_the_script_reads():
    # Both directions. A new `_env("FOO")` left off the list would silently run
    # on its default forever; a stale entry widens the import for nothing.
    # Either quote style: matching only one lets a key be read without the list
    # noticing, because both sides of this equality then shrink together.
    read = set(re.findall(r"""_env\(["']([A-Z_][A-Z0-9_]*)["']""", _SCRIPT.read_text()))
    assert read == set(backup.ENV_KEYS)


def test_settings_are_read_through_one_helper_and_nowhere_else():
    # Reading os.environ directly would bypass the allowlist and the drift test
    # above at once, so the job would run on a value nothing had vetted.
    source = _SCRIPT.read_text()
    helper = source.split("def _env(")[1].split("\ndef ")[0]
    everywhere_else = source.replace(helper, "")
    for way_in in ("os.environ.get(", "os.environ["):
        assert way_in not in everywhere_else, (
            f"{way_in} outside _env reads a setting the allowlist never sees"
        )


# --------------------------------------------------------------------------
# Asking the script where the backup goes
# --------------------------------------------------------------------------


def test_printing_the_destination_reports_where_a_run_would_upload(
        tmp_path, monkeypatch, capsys):
    # The weekly summary asks the script for this rather than re-parsing the env
    # file in shell. Nothing covered the flag, so deleting it left every test
    # green while the Box listing in the summary stopped working.
    _deploy_dir(tmp_path)

    rc = backup.main(["--env", "prod", "--deploy-dir", str(tmp_path),
                      "--print-destination"])
    assert rc == backup.EXIT_OK
    assert capsys.readouterr().out.strip() == "box:bloom-backups/prod"


def test_printing_the_destination_takes_no_dump(tmp_path, monkeypatch):
    # It runs in the summary step, which fires even when the backup failed. It
    # must not touch the stack, and must not need the working directory that a
    # failing run may be the reason we are here at all.
    _deploy_dir(tmp_path)
    monkeypatch.setenv("BACKUP_STATE_DIR", str(tmp_path / "never-created"))
    touched: list = []
    monkeypatch.setattr(backup, "resolve_container",
                        lambda *a: touched.append(a) or "container123")
    monkeypatch.setattr(backup, "upload", lambda *a: touched.append(a))

    rc = backup.main(["--env", "prod", "--deploy-dir", str(tmp_path),
                      "--print-destination"])
    assert rc == backup.EXIT_OK
    assert not touched, "printing a destination must not reach the database or Box"


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
