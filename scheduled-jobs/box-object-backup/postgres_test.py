"""Tests for the psql calls: the watermark's source and the manifest read.

`subprocess` and `shutil.which` are faked; the argv, stdin and environment psql
receives are the thing under test.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import backup_lib as lib  # noqa: E402
import postgres  # noqa: E402

CONN = postgres.Connection(
    host="db-prod",
    port=5432,
    user="supabase_admin",
    database="postgres",
    password="db-secret-9x",
)


@pytest.fixture
def ran(monkeypatch):
    """Fake `subprocess.run` for the clock read, and script what psql answers."""
    seen: dict[str, list] = {"argv": [], "env": [], "stdin": []}
    scripted = {"stdout": "2026-08-31T02:17:03+00\n", "stderr": "", "returncode": 0}

    def fake_run(argv, *, input=None, capture_output=None, text=None, env=None):
        seen["argv"].append(argv)
        seen["env"].append(env)
        seen["stdin"].append(input)
        return subprocess.CompletedProcess(
            argv, scripted["returncode"], scripted["stdout"], scripted["stderr"]
        )

    monkeypatch.setattr(postgres.subprocess, "run", fake_run)
    monkeypatch.setattr(postgres.shutil, "which", lambda name: f"/usr/bin/{name}")
    return seen, scripted


@pytest.fixture
def piped(monkeypatch):
    """Fake `subprocess.Popen` for the manifest read; rows go to its stdout file."""
    seen: dict = {"argv": None, "env": None, "stdin": None}
    scripted = {"rows": "", "stderr": "", "returncode": 0}

    class FakeProc:
        def __init__(self, out):
            self.out = out
            self.returncode = scripted["returncode"]

        def communicate(self, input=None):
            seen["stdin"] = input
            self.out.write(scripted["rows"])
            return ("", scripted["stderr"])

    def fake_popen(argv, **kwargs):
        seen["argv"] = argv
        seen["env"] = kwargs.get("env")
        return FakeProc(kwargs["stdout"])

    monkeypatch.setattr(postgres.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(postgres.shutil, "which", lambda name: f"/usr/bin/{name}")
    return seen, scripted


class TestTheClockComesFromTheDatabase:
    """The watermark's source. A wrong value here is silent total data loss.

    The next run filters `updated_at > <watermark>`. A watermark in the future
    matches nothing, the run records `ok`, and the watermark advances — so
    every object written since is skipped, permanently, with a green tick.
    """

    def test_the_time_comes_from_psql_not_the_host(self, ran):
        seen, scripted = ran
        scripted["stdout"] = "2019-07-04T12:00:00+00\n"
        assert postgres.database_now(CONN) == "2019-07-04T12:00:00+00"
        assert seen["argv"][0][0] == "/usr/bin/psql"

    def test_the_query_normalises_to_utc(self, ran):
        # Without `AT TIME ZONE 'UTC'`, `OF` renders the session offset: on a
        # server in America/Los_Angeles, a watermark seven hours in the future.
        seen, _ = ran
        postgres.database_now(CONN)
        assert "AT TIME ZONE 'UTC'" in seen["stdin"][0]
        assert "now()" in seen["stdin"][0]

    def test_the_format_matches_what_the_manifest_reports(self, ran):
        seen, _ = ran
        postgres.database_now(CONN)
        fmt = 'YYYY-MM-DD"T"HH24:MI:SSOF'
        assert fmt in seen["stdin"][0]
        assert fmt in lib.objects_query(), "the two formats have diverged"

    def test_only_the_first_line_is_taken(self, ran):
        _, scripted = ran
        scripted["stdout"] = "2026-08-31T02:17:03+00\n\n"
        assert postgres.database_now(CONN) == "2026-08-31T02:17:03+00"

    def test_an_empty_answer_is_an_error_not_an_empty_watermark(self, ran):
        _, scripted = ran
        scripted["stdout"] = "   \n"
        with pytest.raises(postgres.PostgresError):
            postgres.database_now(CONN)

    def test_the_sql_never_reaches_an_argv(self, ran):
        seen, _ = ran
        postgres.database_now(CONN)
        assert seen["argv"][0][-2:] == ["-f", "-"]
        assert not any("now()" in arg for arg in seen["argv"][0])

    def test_the_clock_session_is_pinned_read_only(self, ran):
        # The job connects as superuser; nothing server-side refuses a write.
        seen, _ = ran
        postgres.database_now(CONN)
        stdin = seen["stdin"][0]
        assert "default_transaction_read_only = on" in stdin
        # Ahead of the query: the setting only governs later transactions.
        assert stdin.index("read_only") < stdin.index("now()")


class TestTheManifestIsBoundedAndReadOnly:
    def test_the_session_is_pinned_read_only(self, piped, tmp_path):
        seen, _ = piped
        postgres.query_to_file(CONN, "SELECT 1", tmp_path / "m.tsv")
        assert seen["stdin"].startswith(postgres.READ_ONLY_PREAMBLE)

    def test_psql_streams_rather_than_buffering_the_result(self, piped, tmp_path):
        seen, _ = piped
        postgres.query_to_file(CONN, "SELECT 1", tmp_path / "m.tsv")
        assert f"FETCH_COUNT={postgres.FETCH_COUNT}" in seen["argv"]

    def test_the_sql_arrives_on_stdin_not_in_argv(self, piped, tmp_path):
        seen, _ = piped
        postgres.query_to_file(CONN, "SELECT secret_thing", tmp_path / "m.tsv")
        assert "secret_thing" in seen["stdin"]
        assert not any("secret_thing" in a for a in seen["argv"])

    def test_the_rows_are_written_to_the_file_and_counted(self, piped, tmp_path):
        _, scripted = piped
        scripted["rows"] = "images\ta.png\n\nimages\tb.png\n"
        destination = tmp_path / "m.tsv"
        assert postgres.query_to_file(CONN, "SELECT 1", destination) == 2
        assert destination.read_text() == scripted["rows"]

    def test_a_failure_raises_with_stderr_rather_than_counting_rows(
        self, piped, tmp_path
    ):
        _, scripted = piped
        scripted.update(rows="partial\n", returncode=3, stderr="canceling statement")
        with pytest.raises(postgres.PostgresError, match="canceling statement"):
            postgres.query_to_file(CONN, "SELECT 1", tmp_path / "m.tsv")


class TestItConnectsOverTheNetwork:
    def argv(self, ran):
        seen, _ = ran
        postgres.database_now(CONN)
        return seen["argv"][0]

    def test_it_names_the_host_port_user_and_database(self, ran):
        argv = self.argv(ran)
        for flag, value in (
            ("-h", "db-prod"),
            ("-p", "5432"),
            ("-U", "supabase_admin"),
        ):
            assert argv[argv.index(flag) + 1] == value
        assert argv[argv.index("-d") + 1] == "postgres"

    def test_a_missing_password_fails_as_missing_not_as_wrong(self, ran):
        assert "--no-password" in self.argv(ran)

    def test_docker_is_nowhere_in_the_command(self, ran, piped, tmp_path):
        assert not any("docker" in a for a in self.argv(ran))
        seen, _ = piped
        postgres.query_to_file(CONN, "SELECT 1", tmp_path / "m.tsv")
        assert not any("docker" in a for a in seen["argv"])

    def test_psql_stops_at_the_first_error(self, ran, piped, tmp_path):
        # Without it psql carries on past a failed statement and can exit 0.
        assert "ON_ERROR_STOP=1" in self.argv(ran)
        seen, _ = piped
        postgres.query_to_file(CONN, "SELECT 1", tmp_path / "m.tsv")
        assert "ON_ERROR_STOP=1" in seen["argv"]

    def test_an_unreachable_host_fails_in_seconds(self, ran):
        seen, _ = ran
        postgres.database_now(CONN)
        assert int(seen["env"][0]["PGCONNECT_TIMEOUT"]) <= 30


class TestThePasswordStaysOutOfTheArgv:
    def test_psql_gets_it_through_its_environment(self, ran, piped, tmp_path):
        seen, _ = ran
        postgres.database_now(CONN)
        assert seen["env"][0]["PGPASSWORD"] == "db-secret-9x"
        streamed, _ = piped
        postgres.query_to_file(CONN, "SELECT 1", tmp_path / "m.tsv")
        assert streamed["env"]["PGPASSWORD"] == "db-secret-9x"

    def test_it_is_in_no_argument(self, ran, piped, tmp_path):
        seen, _ = ran
        postgres.database_now(CONN)
        streamed, _ = piped
        postgres.query_to_file(CONN, "SELECT 1", tmp_path / "m.tsv")
        for argv in (seen["argv"][0], streamed["argv"]):
            assert not any("db-secret-9x" in a for a in argv)

    def test_it_is_not_in_the_connection_s_repr(self):
        assert "db-secret-9x" not in repr(CONN)


class TestEveryFailureIsASetupError:
    """`main` maps a BackupError to exit 2. Anything else escapes as a
    traceback and exit 1, which means "objects failed after retries"."""

    def test_the_error_is_a_backup_error(self):
        assert issubclass(postgres.PostgresError, lib.BackupError)

    def test_a_rejected_password_raises_it(self, ran):
        _, scripted = ran
        scripted.update(
            returncode=2,
            stdout="",
            stderr='FATAL:  password authentication failed for user "supabase_admin"',
        )
        with pytest.raises(postgres.PostgresError, match="password authentication"):
            postgres.database_now(CONN)

    def test_an_unreachable_host_raises_it(self, ran):
        _, scripted = ran
        scripted.update(
            returncode=2,
            stdout="",
            stderr='could not translate host name "db-prod" to address',
        )
        with pytest.raises(postgres.PostgresError, match="db-prod"):
            postgres.database_now(CONN)

    def test_no_psql_on_the_path_raises_it(self, monkeypatch):
        monkeypatch.setattr(postgres.shutil, "which", lambda name: None)
        with pytest.raises(postgres.PostgresError, match="psql"):
            postgres.database_now(CONN)

    def test_psql_that_cannot_start_raises_it(self, monkeypatch, tmp_path):
        def refuse(*a, **kw):
            raise PermissionError("permission denied")

        monkeypatch.setattr(postgres.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(postgres.subprocess, "Popen", refuse)
        monkeypatch.setattr(postgres.subprocess, "run", refuse)
        with pytest.raises(postgres.PostgresError):
            postgres.database_now(CONN)
        with pytest.raises(postgres.PostgresError):
            postgres.query_to_file(CONN, "SELECT 1", tmp_path / "m.tsv")
