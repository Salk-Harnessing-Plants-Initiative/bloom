"""Tests for the rclone daemon the job runs as a child process.

`subprocess.Popen` and `shutil.which` are faked; the argv, environment and
process options rclone is started with are the thing under test.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import backup_lib as lib  # noqa: E402
import rclone_daemon  # noqa: E402


class FakeProcess:
    """Stands in for the Popen object; records how the daemon is stopped."""

    def __init__(self, *, exits_on_terminate=True, returncode=None):
        self.returncode = returncode
        self.exits_on_terminate = exits_on_terminate
        self.calls: list[str] = []

    def poll(self):
        return self.returncode

    def terminate(self):
        self.calls.append("terminate")
        if self.exits_on_terminate:
            self.returncode = -15

    def kill(self):
        self.calls.append("kill")
        self.returncode = -9

    def wait(self, timeout=None):
        self.calls.append("wait")
        if self.returncode is None:
            raise subprocess.TimeoutExpired("rclone", timeout)
        return self.returncode


@pytest.fixture
def started(monkeypatch, tmp_path):
    """Start a daemon against a fake Popen; return (daemon, argv, options)."""
    seen: dict = {}

    def fake_popen(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(rclone_daemon.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(rclone_daemon.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(rclone_daemon.tempfile, "tempdir", str(tmp_path))

    def start(**overrides):
        options = dict(
            rclone_config="/config/rclone/rclone.conf", port=5572, transfers=8
        )
        options.update(overrides)
        daemon = rclone_daemon.start(**options)
        return daemon, seen["argv"], seen["kwargs"]

    return start


class TestNothingOutsideTheContainerCanReachIt:
    def test_the_rc_api_listens_on_loopback_only(self, started):
        _, argv, _ = started()
        assert "--rc-addr=127.0.0.1:5572" in argv
        assert not any(a.startswith("--rc-addr=:") for a in argv), (
            "bound to every interface, every container on the network reaches it"
        )

    def test_the_client_is_pointed_at_loopback(self, started):
        daemon, _, _ = started()
        assert daemon.url == "http://127.0.0.1:5572"

    def test_docker_is_nowhere_in_the_command(self, started):
        _, argv, _ = started()
        assert argv[:2] == ["/usr/bin/rclone", "rcd"]
        assert not any("docker" in a for a in argv)


class TestThePasswordIsNotDiscoverable:
    def test_it_is_in_no_argument(self, started):
        daemon, argv, _ = started()
        assert daemon.password
        assert not any(daemon.password in a for a in argv)
        assert not any(a.startswith("--rc-pass") for a in argv)

    def test_rclone_reads_it_from_its_environment(self, started):
        daemon, _, kwargs = started()
        assert kwargs["env"][rclone_daemon.RC_PASS_ENV] == daemon.password

    def test_it_is_not_in_the_daemon_s_repr(self, started):
        daemon, _, _ = started()
        assert daemon.password not in repr(daemon)


class TestItsEnvironmentIsItsOwn:
    """rclone takes its whole option surface from `RCLONE_*`, and a child
    inherits whatever its parent holds. So it gets a fresh environment."""

    def test_it_holds_exactly_what_rclone_needs(self, started):
        _, _, kwargs = started()
        assert set(kwargs["env"]) == {"RCLONE_CONFIG", "RCLONE_RC_PASS", "HOME", "PATH"}

    def test_the_config_is_the_one_given(self, started):
        _, _, kwargs = started(rclone_config="/somewhere/rclone.conf")
        assert kwargs["env"]["RCLONE_CONFIG"] == "/somewhere/rclone.conf"

    def test_none_of_the_job_s_credentials_reach_it(self, started, monkeypatch):
        for key in ("POSTGRES_PASSWORD", "MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD"):
            monkeypatch.setenv(key, "leaked")
        monkeypatch.setenv("RCLONE_CONFIG", "/tmp/theirs.conf")
        _, _, kwargs = started()
        assert "leaked" not in kwargs["env"].values()
        assert kwargs["env"]["RCLONE_CONFIG"] == "/config/rclone/rclone.conf"


class TestHowItRuns:
    def test_it_runs_in_its_own_session(self, started):
        # A signal aimed at the job is not also delivered to rclone mid-copy.
        _, _, kwargs = started()
        assert kwargs["start_new_session"] is True

    def test_it_logs_at_notice_not_info(self, started):
        # rclone echoes the source remote into INFO lines, and ours is a
        # connection string carrying MinIO's root credentials.
        _, argv, _ = started()
        assert "--log-level=NOTICE" in argv
        assert not any(a.startswith("--log-level=INFO") for a in argv)

    def test_retries_are_left_to_the_job(self, started):
        _, argv, _ = started(transfers=5)
        assert "--retries=1" in argv
        assert "--transfers=5" in argv
        assert "--stats=0" in argv

    def test_a_bandwidth_limit_is_passed_only_when_set(self, started):
        _, argv, _ = started()
        assert not any(a.startswith("--bwlimit") for a in argv)
        _, argv, _ = started(bwlimit="20M")
        assert "--bwlimit=20M" in argv

    def test_its_output_goes_to_a_log_file_not_a_pipe(self, started):
        # Nothing reads a pipe while copies run; a full one would block rclone.
        daemon, _, kwargs = started()
        assert kwargs["stdout"] is not subprocess.PIPE
        assert kwargs["stderr"] == subprocess.STDOUT
        assert daemon.log_path.exists()


class TestStopping:
    def daemon(self, process, tmp_path):
        return rclone_daemon.Daemon(
            process=process,
            url="http://127.0.0.1:5572",
            user="bloom",
            password="p",
            log_path=tmp_path / "rcd.log",
        )

    def test_it_terminates_and_waits(self, tmp_path):
        process = FakeProcess()
        self.daemon(process, tmp_path).stop()
        assert process.calls == ["terminate", "wait"]

    def test_it_kills_after_the_bound(self, tmp_path):
        process = FakeProcess(exits_on_terminate=False)
        self.daemon(process, tmp_path).stop(timeout=0.01)
        assert process.calls == ["terminate", "wait", "kill", "wait"]

    def test_a_daemon_that_already_exited_is_left_alone(self, tmp_path):
        process = FakeProcess(returncode=1)
        self.daemon(process, tmp_path).stop()
        assert process.calls == []

    def test_its_exit_code_is_visible(self, tmp_path):
        assert self.daemon(FakeProcess(), tmp_path).exit_code() is None
        assert self.daemon(FakeProcess(returncode=2), tmp_path).exit_code() == 2


class TestItsLogTail:
    def test_it_is_redacted(self, tmp_path):
        log = tmp_path / "rcd.log"
        log.write_text(
            'ERROR : :s3,access_key_id=root,secret_access_key="hunter2":b: failed\n'
        )
        daemon = TestStopping().daemon(FakeProcess(), tmp_path)
        tail = daemon.log_tail()
        assert "hunter2" not in tail
        assert "failed" in tail

    def test_it_keeps_the_last_lines(self, tmp_path):
        (tmp_path / "rcd.log").write_text("".join(f"line {i}\n" for i in range(100)))
        tail = TestStopping().daemon(FakeProcess(), tmp_path).log_tail(lines=3)
        assert tail.splitlines() == ["line 97", "line 98", "line 99"]

    def test_a_missing_log_does_not_raise(self, tmp_path):
        assert TestStopping().daemon(FakeProcess(), tmp_path).log_tail()


class TestEveryFailureIsASetupError:
    def test_the_error_is_a_backup_error(self):
        assert issubclass(rclone_daemon.DaemonError, lib.BackupError)

    def test_no_rclone_on_the_path_raises_it(self, monkeypatch):
        monkeypatch.setattr(rclone_daemon.shutil, "which", lambda name: None)
        with pytest.raises(rclone_daemon.DaemonError, match="rclone"):
            rclone_daemon.start(rclone_config="/c", port=5572, transfers=8)

    def test_rclone_that_cannot_start_raises_it(self, monkeypatch, tmp_path):
        def refuse(*a, **kw):
            raise PermissionError("permission denied")

        monkeypatch.setattr(
            rclone_daemon.shutil, "which", lambda name: "/usr/bin/rclone"
        )
        monkeypatch.setattr(rclone_daemon.subprocess, "Popen", refuse)
        monkeypatch.setattr(rclone_daemon.tempfile, "tempdir", str(tmp_path))
        with pytest.raises(rclone_daemon.DaemonError, match="permission denied"):
            rclone_daemon.start(rclone_config="/c", port=5572, transfers=8)
