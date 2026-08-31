"""Tests for the docker-side plumbing, which had none.

Every mutation applied to this module survived the suite: removing the
read-only pin from the psql session, removing FETCH_COUNT so psql buffers
millions of rows inside the database container, removing the rclone
container's memory cap, unbinding its RC port from loopback, and mounting the
Box token read-write. None of that is exotic — the module simply had no test
file, so nothing could notice.

`run` and `which` are faked; the argv construction they receive is the thing
under test.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import docker_env as dock  # noqa: E402


@pytest.fixture
def captured(monkeypatch):
    """Capture the argv `docker_env` builds, and script what `run` returns."""
    calls: list[list[str]] = []
    scripted = {"stdout": ""}

    def fake_run(cmd, *, input_text=None, check=True, env=None):
        calls.append(cmd)
        scripted.setdefault("envs", []).append(env)
        return scripted["stdout"]

    monkeypatch.setattr(dock, "run", fake_run)
    monkeypatch.setattr(dock, "which", lambda name: f"/usr/bin/{name}")
    return calls, scripted


class TestDatabaseNow:
    """The watermark's source. A wrong value here is silent total data loss.

    The next run filters `updated_at > <watermark>`. A watermark in the future
    matches nothing, the run records `ok`, and the watermark advances — so
    every object written since is skipped, permanently, with a green tick.
    """

    def test_the_time_comes_from_the_database_not_the_host(self, captured):
        calls, scripted = captured
        scripted["stdout"] = "2019-07-04T12:00:00+00\n"
        assert dock.database_now("c", "u", "d") == "2019-07-04T12:00:00+00"
        # A 2019 stamp cannot have come from a clock; it came from the fake.
        assert calls, "no command was run — the value was invented locally"
        assert calls[0][:3] == ["/usr/bin/docker", "exec", "-i"]

    def test_the_query_normalises_to_utc(self, captured):
        # WITHOUT `AT TIME ZONE 'UTC'`, `OF` renders the SESSION offset. On a
        # host running America/Los_Angeles that is a watermark seven hours in
        # the future, which matches no rows at all.
        calls, scripted = captured
        scripted["stdout"] = "2026-08-31T02:17:03+00\n"
        dock.database_now("c", "u", "d")
        sql = calls[0][-1]
        assert "AT TIME ZONE 'UTC'" in sql
        assert "now()" in sql

    def test_the_format_matches_what_the_manifest_reports(self, captured):
        # Both sides of `updated_at > <watermark>` must agree on shape; the
        # manifest column is built with the same to_char format.
        import backup_lib as lib

        calls, scripted = captured
        scripted["stdout"] = "2026-08-31T02:17:03+00\n"
        dock.database_now("c", "u", "d")
        fmt = 'YYYY-MM-DD"T"HH24:MI:SSOF'
        assert fmt in calls[0][-1]
        assert fmt in lib.objects_query(), "the two formats have diverged"

    def test_only_the_first_line_is_taken(self, captured):
        calls, scripted = captured
        scripted["stdout"] = "2026-08-31T02:17:03+00\n\n"
        assert dock.database_now("c", "u", "d") == "2026-08-31T02:17:03+00"

    def test_an_empty_answer_is_an_error_not_an_empty_watermark(self, captured):
        # An empty watermark would make the next run enumerate everything —
        # survivable — but it must not pass silently as a value.
        calls, scripted = captured
        scripted["stdout"] = "   \n"
        with pytest.raises(dock.DockerError):
            dock.database_now("c", "u", "d")

    def test_the_sql_travels_as_one_argument(self, captured):
        # No shell is involved anywhere: `run` takes a list. The quotes inside
        # the to_char format are literal text for psql, not shell quoting.
        calls, scripted = captured
        scripted["stdout"] = "2026-08-31T02:17:03+00\n"
        dock.database_now("c", "u", "d")
        assert calls[0][-2] == "-c"


class TestManifestQueryIsBoundedAndReadOnly:
    """`psql_query_to_file` uses subprocess.Popen directly, so fake that."""

    @pytest.fixture
    def piped(self, monkeypatch):
        seen = {"argv": None, "stdin": None}

        class FakeProc:
            returncode = 0

            def communicate(self, input=None):
                seen["stdin"] = input
                return ("", "")

        def fake_popen(argv, **kwargs):
            seen["argv"] = argv
            return FakeProc()

        monkeypatch.setattr(dock.subprocess, "Popen", fake_popen)
        monkeypatch.setattr(dock, "which", lambda name: f"/usr/bin/{name}")
        return seen

    def test_the_session_is_pinned_read_only(self, piped, tmp_path):
        # A mistake in query construction must not be able to write.
        dock.psql_query_to_file("c", "SELECT 1", "u", "d", tmp_path / "m.tsv")
        assert "default_transaction_read_only = on" in piped["stdin"]

    def test_psql_streams_rather_than_buffering_the_result(self, piped, tmp_path):
        # Without FETCH_COUNT, psql pulls every row into memory inside the
        # database container before writing a byte — millions of them, beside
        # Postgres's own memory, on a host running the whole stack.
        dock.psql_query_to_file("c", "SELECT 1", "u", "d", tmp_path / "m.tsv")
        assert f"FETCH_COUNT={dock.FETCH_COUNT}" in piped["argv"]

    def test_the_sql_arrives_on_stdin_not_in_argv(self, piped, tmp_path):
        # Keeps a long bucket list off the argv length limit, and keeps query
        # text out of `ps`.
        dock.psql_query_to_file("c", "SELECT secret_thing", "u", "d", tmp_path / "m.tsv")
        assert "secret_thing" in piped["stdin"]
        assert not any("secret_thing" in a for a in piped["argv"])


class TestRcloneDaemonArgv:
    """The container holding the Box token and MinIO's root credentials."""

    def daemon_call(self, captured, **kwargs):
        """Returns (argv, env) for one start_rc_daemon call."""
        calls, scripted = captured
        scripted["stdout"] = "containerid\n"
        options = dict(
            network="supanet", rclone_config="/conf/rclone.conf", port=5572,
            transfers=8,
        )
        options.update(kwargs)
        daemon = dock.start_rc_daemon(**options)
        return calls[0], scripted["envs"][0], daemon

    def daemon_argv(self, captured, **kwargs):
        calls, scripted = captured
        scripted["stdout"] = "containerid\n"
        options = dict(
            network="supanet", rclone_config="/conf/rclone.conf", port=5572,
            transfers=8,
        )
        options.update(kwargs)
        dock.start_rc_daemon(**options)
        return calls[0]

    def test_the_rc_port_is_bound_to_loopback(self, captured):
        argv = self.daemon_argv(captured)
        assert "127.0.0.1:5572:5572" in argv, (
            "unbound, the RC API is reachable from every host interface"
        )

    def test_the_box_token_is_mounted_read_only(self, captured):
        argv = self.daemon_argv(captured)
        assert any(a.endswith("rclone.conf:ro") for a in argv), (
            "a read-write mount lets a throwaway container rewrite the token"
        )

    def test_the_container_has_a_memory_ceiling(self, captured):
        # It shares the host with the entire Bloom stack.
        argv = self.daemon_argv(captured)
        assert "--memory" in argv
        assert dock.RC_MEMORY_LIMIT in argv

    def test_rclone_does_not_log_at_info(self, captured):
        # rclone echoes the source remote into its own log lines, and ours is a
        # connection string carrying MinIO's root credentials.
        argv = self.daemon_argv(captured)
        assert "--log-level=NOTICE" in argv
        assert "--log-level=INFO" not in argv

    def test_the_state_dir_is_mounted_read_only_when_given(self, captured):
        argv = self.daemon_argv(captured, state_dir="/var/lib/x")
        assert f"/var/lib/x:{dock.STATE_MOUNT}:ro" in argv

    def test_no_state_dir_means_no_mount(self, captured):
        argv = self.daemon_argv(captured)
        assert not any(dock.STATE_MOUNT in a for a in argv)


class TestTheDaemonPasswordIsNotDiscoverable:
    """The RC API is reachable from every container on the deploy network.

    It has to be — MinIO's S3 port is published nowhere else. So the password
    is the only thing keeping `storage`, `kong`, `postgrest` and `studio` out
    of an API whose `config/dump` returns the Box OAuth token in plaintext.

    It was passed as `--rc-pass=<secret>`, and /proc/<pid>/cmdline is
    world-readable: every user on the host could read it, for the days a seed
    takes. Generating it with `secrets` was beside the point.
    """

    def call(self, captured, **kwargs):
        calls, scripted = captured
        scripted["stdout"] = "containerid\n"
        options = dict(
            network="supanet", rclone_config="/conf/rclone.conf", port=5572,
            transfers=8,
        )
        options.update(kwargs)
        daemon = dock.start_rc_daemon(**options)
        return calls[0], scripted["envs"][0], daemon

    def test_the_password_is_nowhere_in_the_command_line(self, captured):
        argv, env, daemon = self.call(captured)
        assert daemon.password, "no password was generated"
        for arg in argv:
            assert daemon.password not in arg, (
                f"the password is in the argv ({arg[:32]}…), which every user "
                "on the host can read from /proc/<pid>/cmdline"
            )

    def test_the_password_reaches_the_container_through_the_environment(self, captured):
        argv, env, daemon = self.call(captured)
        assert env is not None, "run() was called without an env"
        assert env[dock.RC_PASS_ENV] == daemon.password

    def test_docker_is_told_to_pass_the_variable_through_by_name(self, captured):
        # `--env NAME=value` would put the secret in docker's OWN command line,
        # which is just as readable. Valueless means "copy it from my env".
        argv, env, daemon = self.call(captured)
        assert dock.RC_PASS_ENV in argv, "docker was not told to forward it"
        assert not any(
            a.startswith(f"{dock.RC_PASS_ENV}=") for a in argv
        ), "the value is in docker's argv"

    def test_the_rc_pass_flag_is_gone(self, captured):
        argv, env, daemon = self.call(captured)
        assert not any(a.startswith("--rc-pass") for a in argv)

    def test_the_container_drops_privileges_like_every_other_service(self, captured):
        # docker-compose.prod.yml sets both on every service. This container
        # holds the Box token and MinIO's root credentials.
        argv, env, daemon = self.call(captured)
        assert "no-new-privileges" in argv
        assert "--cap-drop" in argv
        assert "ALL" in argv


class TestStaleDaemonDetection:
    """The leak this exists to catch.

    `daemon.stop()` lives in a `finally`, and `finally` does not run when the
    process is killed with SIGTERM — a reboot, a `kill`, a cancelled workflow.
    The container survives, keeps the RC port, and the next run dies on
    `port is already allocated` with nothing pointing at the cause.
    """

    def test_no_leftovers_is_an_empty_list(self, captured):
        calls, scripted = captured
        scripted["stdout"] = ""
        assert dock.find_stale_daemons() == []

    def test_a_leftover_is_reported_with_its_status(self, captured):
        calls, scripted = captured
        scripted["stdout"] = "bloom-box-backup-rclone-a1b2 (Up 3 days)\n"
        assert dock.find_stale_daemons() == ["bloom-box-backup-rclone-a1b2 (Up 3 days)"]

    def test_stopped_containers_count_too(self, captured):
        # --all, not just running: an exited container still holds its name and
        # its published port binding until it is removed.
        calls, scripted = captured
        scripted["stdout"] = "x (Exited (137) 2 days ago)\n"
        dock.find_stale_daemons()
        assert "--all" in calls[0]

    def test_only_this_job_s_containers_are_considered(self, captured):
        calls, scripted = captured
        scripted["stdout"] = ""
        dock.find_stale_daemons()
        assert f"name={dock.RC_CONTAINER_PREFIX}" in calls[0]
