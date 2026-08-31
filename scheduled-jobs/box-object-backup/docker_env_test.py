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

    def fake_run(cmd, *, input_text=None, check=True):
        calls.append(cmd)
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
