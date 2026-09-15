"""The job's rclone remote-control daemon, run as a child process on loopback.

The daemon listens on 127.0.0.1 inside the job's own container, so no other
container on the stack's network can reach its API, and nothing on the host
can either. It gets an environment of its own — the Box config, its RC
password, HOME and PATH — so none of the job's credentials reach it except
inside the remote-control calls that use them.

The Box config is mounted read-only, so a token refresh lives only in the
daemon's memory for the run; two runs more than an hour apart show whether the
refresh token on disk still works.
"""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import backup_lib as lib
from rclone_rc import redact

# rclone's environment equivalent of --rc-pass, so the password is never in an argv.
RC_PASS_ENV = "RCLONE_RC_PASS"
RC_USER = "bloom"

# How long a stopping daemon gets before it is killed, and again after the kill.
STOP_TIMEOUT_SECONDS = 30


class DaemonError(lib.BackupError):
    """rclone could not be started, or never became ready."""


@dataclass
class Daemon:
    process: subprocess.Popen = field(repr=False)
    url: str
    user: str
    # Kept out of the repr: the run's whole log reaches the job summary.
    password: str = field(repr=False)
    log_path: Path

    def exit_code(self) -> int | None:
        """The daemon's exit code, or None while it is still running."""
        return self.process.poll()

    def log_tail(self, lines: int = 40) -> str:
        """The end of the daemon's own log, with credentials redacted."""
        try:
            text = self.log_path.read_text(errors="replace")
        except OSError:
            return "(the daemon wrote no log)"
        return redact("\n".join(text.splitlines()[-lines:]))

    def stop(self, timeout: float = STOP_TIMEOUT_SECONDS) -> None:
        """Terminate, wait, kill after the bound, then remove the log. Never raises."""
        if self.process.poll() is None:
            try:
                self.process.terminate()
                self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.process.kill()
                try:
                    self.process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    pass
            except OSError:
                pass
        # Any failure has already quoted the log's tail.
        try:
            self.log_path.unlink(missing_ok=True)
        except OSError:
            pass


def ensure_running(daemon: Daemon, when: str) -> None:
    """Raise DaemonError, with the end of its log, if the daemon has exited."""
    code = daemon.exit_code()
    if code is not None:
        raise DaemonError(
            f"rclone daemon exited ({code}) {when}. Its log:\n{daemon.log_tail()}"
        )


def start(
    *, rclone_config: str, port: int, transfers: int, bwlimit: str = ""
) -> Daemon:
    """Start `rclone rcd` bound to loopback, in its own session.

    Its own session, so a signal aimed at the job is not also delivered to
    rclone mid-copy; the job stops it through `Daemon.stop`. Its output goes to
    a log file rather than a pipe nothing reads while copies run.
    """
    rclone = shutil.which("rclone")
    if not rclone:
        raise DaemonError("rclone is not on PATH")
    password = secrets.token_urlsafe(24)
    argv = [
        rclone,
        "rcd",
        f"--rc-addr=127.0.0.1:{port}",
        f"--rc-user={RC_USER}",
        f"--transfers={transfers}",
        "--retries=1",  # retry/backoff is the caller's job, per object
        "--stats=0",
        # NOTICE, not INFO: rclone echoes the source remote into its own log
        # lines, and ours is a connection string carrying MinIO's root keys.
        "--log-level=NOTICE",
    ]
    if bwlimit:
        argv.append(f"--bwlimit={bwlimit}")
    env = {
        "RCLONE_CONFIG": rclone_config,
        RC_PASS_ENV: password,
        "HOME": os.environ.get("HOME", tempfile.gettempdir()),
        "PATH": os.environ.get("PATH", os.defpath),
    }
    try:
        fd, log_name = tempfile.mkstemp(prefix="rclone-rcd-", suffix=".log")
        os.close(fd)
        with open(log_name, "w") as log:
            process = subprocess.Popen(
                argv,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
    except OSError as exc:
        raise DaemonError(f"could not start rclone: {exc}") from exc
    return Daemon(
        process=process,
        url=f"http://127.0.0.1:{port}",
        user=RC_USER,
        password=password,
        log_path=Path(log_name),
    )
