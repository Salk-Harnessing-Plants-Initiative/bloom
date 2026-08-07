"""Storage helpers for the download commands: fetching objects and writing them to disk.

The client refreshes its auth token on a background timer. A bucket handle captures the
token it was created with, so a handle kept for a whole download stops working once that
token is replaced. Every download here resolves its own handle, which is cheap — it's a
small object over the client's shared connection pool.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

IMAGES_BUCKET = "images"

# Downloaded frames should be as readable as the scans.csv beside them. mkstemp makes files
# owner-only, so the mode is set explicitly before the file is moved into place.
FRAME_MODE = 0o644

# Read once at import. os.umask is process-wide and briefly clears the mask, so calling it
# from a download thread would affect every other thread creating a file at that moment.
_UMASK = os.umask(0)
os.umask(_UMASK)

# Storage answers a caller whose token has expired with a 404 "Bucket not found" — it won't
# confirm that a private bucket exists to someone who can't read it.
_EXPIRED_MARKERS = ("bucket not found", "jwt expired", "invalid jwt")
_EXPIRED_HINT = "expired session (storage reports an unauthenticated caller as a missing bucket)"

_TRANSIENT_MARKERS = ("500", "502", "503", "504", "timeout", "timed out", "connection")


class StorageError(RuntimeError):
    """A storage request failed, after any retry."""


def looks_like_expired_session(error: BaseException) -> bool:
    """True for the 404 storage returns when the caller's token is no longer valid."""
    text = str(error).lower()
    return any(marker in text for marker in _EXPIRED_MARKERS)


def is_retryable(error: BaseException) -> bool:
    """True for failures a second attempt could plausibly fix.

    Deliberately narrow: an object that genuinely isn't there should fail on the first
    attempt rather than doubling the number of requests across a whole experiment.
    """
    if looks_like_expired_session(error):
        return True
    text = str(error).lower()
    return any(marker in text for marker in _TRANSIENT_MARKERS)


def describe_storage_error(error: BaseException) -> StorageError:
    """Wrap an error, naming an expired session rather than leaving it as 'bucket not found'."""
    if looks_like_expired_session(error):
        return StorageError(f"{_EXPIRED_HINT}: {error}")
    return StorageError(str(error))


def download_object(client: Any, object_path: str, *, bucket: str = IMAGES_BUCKET) -> bytes:
    """Download one object's bytes.

    Resolves the bucket handle on every call so a long run keeps working after the client
    renews its token. Retries once if the failure looks like an expired token or a transient
    server error, which also covers a renewal landing between resolving the handle and using it.
    """
    try:
        return _fetch(client, bucket, object_path)
    except Exception as exc:
        if not is_retryable(exc):
            raise describe_storage_error(exc) from exc
        try:
            return _fetch(client, bucket, object_path)
        except Exception as retry_exc:
            raise describe_storage_error(retry_exc) from retry_exc


def _fetch(client: Any, bucket: str, object_path: str) -> bytes:
    data = client.storage.from_(bucket).download(object_path)
    if data is None:
        raise ValueError("empty response from storage")
    return data


def _unlink_quietly(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def atomic_write_bytes(path: Path, data: bytes, *, mode: int = FRAME_MODE) -> None:
    """Write bytes to ``path`` so nothing can ever read a half-written file.

    Content goes to a temp file first and is then moved into place in one step. This matters
    twice over: a resumed run must not mistake a partial file for a finished one, and two
    workers handed the same destination must not interleave their writes.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".dl-", suffix=".tmp")
    try:
        try:
            handle = os.fdopen(fd, "wb")
        except BaseException:
            os.close(fd)  # fdopen didn't take the descriptor, so it's still ours to close
            raise
        with handle:
            handle.write(data)
        os.chmod(tmp, mode & ~_UMASK)
        os.replace(tmp, path)
    except BaseException:
        _unlink_quietly(tmp)
        raise


def sweep_orphan_temps(out_dir: Path) -> int:
    """Delete temp files left behind by a run that was killed outright; return how many.

    A normal failure cleans up after itself, but a hard kill or a power cut doesn't get
    the chance.
    """
    images_root = Path(out_dir) / "images"
    if not images_root.exists():
        return 0
    removed = 0
    for tmp in images_root.rglob(".dl-*.tmp"):
        _unlink_quietly(str(tmp))
        removed += 1
    return removed


def already_downloaded(path: Path, expected_size: int | None = None) -> bool:
    """True if ``path`` already holds this frame, so a resumed run can skip fetching it.

    Given ``expected_size`` this is a real completeness check. Without it, all that can be
    said is that the file isn't empty — which won't catch a file truncated by a version of
    bloomctl that wrote frames without the temp-file step.
    """
    if not path.is_file():
        return False
    size = path.stat().st_size
    if expected_size is not None:
        return size == expected_size
    return size > 0
