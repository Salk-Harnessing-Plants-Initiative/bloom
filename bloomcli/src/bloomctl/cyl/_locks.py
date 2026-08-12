"""Shared file-based lock/lease primitive for `cyl` commands.

The first concrete implementation of bloom #481's deferred cross-command lock design — kept
generic (not `download_for_predict`-specific) so a future command needing the same primitive
doesn't need a rename/extraction first.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

# The one threshold both the per-scan and manifest locks default to (see download_for_predict.py's
# --lock-staleness-seconds option).
DEFAULT_LOCK_STALENESS_SECONDS = 900

# The directory (sibling of every {scan_key}/ directory, at out_dir's own root) that holds every
# lock file this module manages, and the manifest lock's fixed filename within it. Named here,
# not re-literaled at each call site, for the same reason RUN_MANIFEST_FILENAME is imported
# rather than copied: a typo in one of two otherwise-identical string literals wouldn't be
# caught by anything.
LOCKS_DIRNAME = ".locks"
MANIFEST_LOCK_FILENAME = "manifest.lock"


class LockContendedError(RuntimeError):
    """Raised when a lock is held by another live (non-stale) process, or its reclaim raced."""


def _read_lock_info(path: Path) -> dict[str, Any] | None:
    """The lock file's parsed JSON body, or None if it can't be read/parsed right now.

    Uses ``Path.read_text`` (auto-closing), never a raw ``os.open`` — this is a read of
    someone else's lock file, not the acquire path, so there is no fd-lifetime concern here.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _reclaim_or_raise(path: Path, staleness_seconds: float) -> None:
    """Unlink `path` if it's provably stale and unchanged since being judged so; else raise.

    The re-read-immediately-before-unlink step exists to close a specific race: two
    processes observing the same stale lock must not both conclude it's safe to delete,
    since the second one would otherwise unlink whatever the first one just created (see
    design.md's "Lock mechanism" section for the full trace).
    """
    first_seen = _read_lock_info(path)
    if first_seen is None or not isinstance(first_seen.get("acquired_at"), (int, float)):
        raise LockContendedError(f"{path} is locked by another process (lock file unreadable)")

    acquired_at = first_seen["acquired_at"]
    age = time.time() - acquired_at
    if age <= staleness_seconds:
        raise LockContendedError(
            f"{path} is locked by pid {first_seen.get('pid')} "
            f"(age {age:.1f}s, staleness threshold {staleness_seconds}s)"
        )

    current = _read_lock_info(path)
    if current is None or current.get("acquired_at") != acquired_at:
        raise LockContendedError(f"{path} was reclaimed or re-acquired by another process")

    try:
        os.unlink(str(path))
    except (FileNotFoundError, PermissionError):
        # FileNotFoundError: a peer's own reclaim attempt already removed it.
        # PermissionError: a peer's file handle overlapped the unlink (observed on Windows,
        # this repo's own dev platform — a peer can hold the path open transiently around
        # its own create/write).
        raise LockContendedError(f"{path} was reclaimed by another process") from None


def _create_lock_file(path: Path) -> int | None:
    """Attempt the atomic exclusive create; return its fd, or None if it already exists."""
    try:
        return os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None


def _write_lock_body(fd: int, path: Path) -> None:
    """Write this process's pid/acquired_at through `fd`, closing it before returning.

    If the write itself fails partway (crash, disk error), the just-created lock file is
    removed rather than left behind — an unreadable lock file can never be judged stale (see
    `_reclaim_or_raise`), so leaving one behind on a failed write would permanently wedge this
    path for every future invocation, exactly the outcome this whole primitive exists to avoid.
    """
    try:
        body = json.dumps({"pid": os.getpid(), "acquired_at": time.time()}).encode("utf-8")
        os.write(fd, body)
    except BaseException:
        os.close(fd)
        try:
            path.unlink()
        except OSError:
            pass
        raise
    else:
        # Closed immediately, before the guarded code runs — never held open for the `with`
        # block's duration. On Windows an open handle blocks deletion (no FILE_SHARE_DELETE),
        # which would make this lock's own release-on-exit fail with PermissionError; POSIX
        # allows unlinking an open file unconditionally, so this has no signal from this
        # repo's Linux-only CI (see design.md).
        os.close(fd)


def _release(path: Path) -> None:
    """Remove `path` only if it still records this process's own pid.

    Never unconditionally delete whatever lock file happens to be there: a peer may have
    legitimately reclaimed this lock as stale while this process was still (slowly) working —
    an accepted trade-off of pure age-based staleness with no heartbeat/renewal (see design.md).
    Blindly deleting on release would then remove that peer's brand-new live lock, letting a
    third process acquire a "free" lock while the peer is still mid-critical-section — exactly
    the corruption this primitive exists to prevent. An unreadable/unparseable file is treated
    as still ours (the ambiguous case: the only realistic way our own successfully-written file
    becomes unreadable is a peer's write racing in mid-flight, which is already covered by the
    reclaim-side re-read mitigation, not a case release needs to additionally guard).
    """
    current = _read_lock_info(path)
    if current is not None and current.get("pid") != os.getpid():
        return
    try:
        path.unlink()
    except OSError:
        pass


@contextmanager
def acquire_lock(path: Path, *, staleness_seconds: float) -> Iterator[None]:
    """Hold an exclusive, staleness-reclaimable lock at `path` for the `with` block's duration.

    Raises `LockContendedError` (fail fast, no blocking/wait/retry loop) if the lock is held
    by another live process. A lock whose age exceeds `staleness_seconds` is reclaimed once;
    if that reclaim loses a race to another process, `LockContendedError` is raised instead
    of retrying further.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd = _create_lock_file(path)
    if fd is None:
        _reclaim_or_raise(path, staleness_seconds)
        fd = _create_lock_file(path)
        if fd is None:
            raise LockContendedError(f"{path} was re-acquired by another process during reclaim")

    _write_lock_body(fd, path)

    try:
        yield
    finally:
        _release(path)
