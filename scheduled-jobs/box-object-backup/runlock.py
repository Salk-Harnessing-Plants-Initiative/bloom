"""Host-wide lock that keeps two backup runs off the same ledger.

The seed run walks millions of objects and takes days. The weekly workflow
fires on a calendar and knows nothing about it, so without a lock the Saturday
run would start a second pass over a ledger the seed is still writing. Two
processes sharing one SQLite ledger is how you get duplicate copies and a
`database is locked` failure mid-seed.

`threading.Lock` in copier.py and ledger.py guards threads inside one process;
this guards whole processes, including a detached seed started by hand over SSH
and a workflow run that arrives while it is going. `flock` is the right
primitive: the kernel drops it when the holder dies, so a killed seed does not
strand the lock the way a stale pidfile would.

A run that cannot take the lock is NOT a failure — the seed is doing exactly
the work the weekly run would have done. It exits 0 and says so.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

LOCK_FILENAME = "backup.lock"

# Printed verbatim when a run stands down. The workflow greps for it to label
# the run summary "skipped" rather than "succeeded", so it must stay in sync
# with .github/workflows/box-object-backup.yml.
SKIP_MARKER = "box-object-backup: SKIPPED — another run holds the lock"


@dataclass(frozen=True)
class LockHolder:
    """Whatever the current holder recorded about itself, for the log."""

    pid: int | None
    started_at: float | None
    argv: str | None

    def describe(self) -> str:
        if self.pid is None:
            return "an unidentified process (the lock file held no readable metadata)"
        parts = [f"pid {self.pid}"]
        if self.started_at is not None:
            elapsed = max(0.0, time.time() - self.started_at)
            parts.append(f"running for {_format_elapsed(elapsed)}")
        if self.argv:
            parts.append(f"as `{self.argv}`")
        return ", ".join(parts)


def _format_elapsed(seconds: float) -> str:
    hours, rem = divmod(int(seconds), 3600)
    minutes = rem // 60
    if hours >= 24:
        days, hours = divmod(hours, 24)
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


class LockHeld(Exception):
    """Raised when another live run already owns the lock."""

    def __init__(self, path: Path, holder: LockHolder) -> None:
        self.path = path
        self.holder = holder
        super().__init__(f"{path} is held by {holder.describe()}")


class RunLock:
    """Exclusive, non-blocking flock over a file in the state directory.

    Used as a context manager. Entering raises LockHeld if another run owns
    it; the caller decides that this is a clean skip rather than an error.
    """

    def __init__(self, state_dir: Path | str, filename: str = LOCK_FILENAME) -> None:
        self.path = Path(state_dir) / filename
        self._fd: int | None = None

    def __enter__(self) -> "RunLock":
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()

    def acquire(self) -> "RunLock":
        # Opened before the flock attempt so a failed attempt can still read
        # the holder's metadata out of the same file.
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as err:
            if err.errno not in (errno.EACCES, errno.EAGAIN):
                os.close(fd)
                raise
            holder = _read_holder(fd)
            os.close(fd)
            raise LockHeld(self.path, holder) from err

        # Held now. Record who we are so the next run's log names us.
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(
            fd,
            json.dumps(
                {
                    "pid": os.getpid(),
                    "started_at": time.time(),
                    "argv": " ".join(os.sys.argv[:4]),
                }
            ).encode(),
        )
        os.fsync(fd)
        self._fd = fd
        return self

    def release(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        # Blank the metadata so a reader never attributes the lock to a dead
        # pid; the flock itself is dropped by the close.
        try:
            os.ftruncate(fd, 0)
        except OSError:
            pass
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _read_holder(fd: int) -> LockHolder:
    """Best-effort read of the holder's metadata. Never raises."""
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        raw = os.read(fd, 4096).decode(errors="replace").strip()
        if not raw:
            return LockHolder(None, None, None)
        data = json.loads(raw)
        return LockHolder(
            pid=_as_int(data.get("pid")),
            started_at=_as_float(data.get("started_at")),
            argv=data.get("argv") or None,
        )
    except (OSError, ValueError, TypeError):
        return LockHolder(None, None, None)


def _as_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _as_float(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
