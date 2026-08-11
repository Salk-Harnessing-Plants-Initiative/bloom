"""Storage helpers for the download commands: fetching objects and writing them to disk.

The client refreshes its auth token on a background timer. A bucket handle captures the
token it was created with, so a handle kept for a whole download stops working once that
token is replaced. Every download here resolves its own handle, which is cheap — it's a
small object over the client's shared connection pool.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

IMAGES_BUCKET = "images"

# Storage answers a caller whose token has expired with a 404 "Bucket not found" — it won't
# confirm that a private bucket exists to someone who can't read it. A genuinely absent object
# is also a 404, so the status alone can't tell them apart; the message is the only signal.
_EXPIRED_MARKERS = ("bucket not found", "jwt expired", "invalid jwt", "bad_jwt")
_EXPIRED_HINT = "expired session (storage reports an unauthenticated caller as a missing bucket)"

# Wait this long before the retry, so a server that is rate-limiting or overloaded gets a
# moment rather than an immediate second request from every worker.
RETRY_DELAY_SECONDS = 0.5

# Storage returns a page at a time. The ceiling is a guard against a prefix that never stops
# paginating; a cylinder scan holds frames in the thousands, not the hundreds of thousands.
LIST_PAGE_SIZE = 100
LIST_MAX_OBJECTS = 100_000


class StorageError(RuntimeError):
    """A storage request failed, after any retry."""


def looks_like_expired_session(error: BaseException) -> bool:
    """True for the 404 storage returns when the caller's token is no longer valid."""
    text = str(error).lower()
    return any(marker in text for marker in _EXPIRED_MARKERS)


def status_of(error: BaseException) -> int | None:
    """HTTP status carried by a storage API error, if it has one."""
    try:
        return int(getattr(error, "status", None))  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None


def _is_transport_error(error: BaseException) -> bool:
    """True for a network-level httpx failure.

    These have to be recognised by type: a timeout or a dropped connection carries no message
    at all (`str(httpx.ReadTimeout())` is empty), so there is nothing to match text against.
    """
    try:
        import httpx
    except ImportError:  # pragma: no cover - httpx is a hard dependency of supabase
        return False
    return isinstance(error, httpx.TransportError)


def is_retryable(error: BaseException) -> bool:
    """True for failures a second attempt could plausibly fix.

    Deliberately narrow: an object that genuinely isn't there should fail on the first attempt
    rather than doubling the number of requests across a whole experiment. Classification uses
    the status code and the exception type rather than searching the formatted message, which
    would both miss transport errors and match on digits appearing inside an object path.
    """
    if looks_like_expired_session(error):
        return True
    status = status_of(error)
    if status is not None:
        return status == 429 or 500 <= status <= 599
    return _is_transport_error(error)


def describe_storage_error(error: BaseException) -> StorageError:
    """Wrap an error, naming an expired session rather than leaving it as 'bucket not found'.

    Falls back to the exception's type name when it has no message of its own — an httpx
    timeout would otherwise be recorded in the log as an empty string.
    """
    detail = str(error) or type(error).__name__
    if looks_like_expired_session(error):
        return StorageError(f"{_EXPIRED_HINT}: {detail}")
    return StorageError(detail)


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
        time.sleep(RETRY_DELAY_SECONDS)
        try:
            return _fetch(client, bucket, object_path)
        except Exception as retry_exc:
            raise describe_storage_error(retry_exc) from retry_exc


def _fetch(client: Any, bucket: str, object_path: str) -> bytes:
    data = client.storage.from_(bucket).download(object_path)
    if data is None:
        raise ValueError("empty response from storage")
    return data


def object_sizes(client: Any, prefix: str, *, bucket: str = IMAGES_BUCKET) -> dict[str, int]:
    """Byte size of every object directly under ``prefix``, keyed by full object path.

    Used to tell a complete frame on disk from one an interrupted older version left part
    written. Storage is the only place that knows the size — `cyl_images` records the path
    but not the length.

    Returns what it managed to read. A listing that fails yields nothing rather than raising:
    not knowing a frame's size is the position every run was in before this existed, so it
    costs a re-download at worst and must never fail a download that would otherwise work.
    """
    sizes: dict[str, int] = {}
    for offset in range(0, LIST_MAX_OBJECTS, LIST_PAGE_SIZE):
        try:
            page = client.storage.from_(bucket).list(
                prefix, {"limit": LIST_PAGE_SIZE, "offset": offset}
            )
        except Exception:
            return sizes
        for entry in page or []:
            name = entry.get("name") if isinstance(entry, dict) else None
            size = (entry.get("metadata") or {}).get("size") if isinstance(entry, dict) else None
            # bool is an int; a stray True would otherwise become a size of 1.
            if name and isinstance(size, int) and not isinstance(size, bool):
                sizes[f"{prefix}/{name}" if prefix else name] = size
        if not page or len(page) < LIST_PAGE_SIZE:
            break
    return sizes


def _unlink_quietly(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write bytes to ``path`` so nothing can ever read a half-written file.

    Content goes to a temp file first and is then renamed into place, which happens in one
    step. This matters twice over: a resumed run must not mistake a partial file for a
    finished one, and two workers handed the same destination must not interleave their
    writes. The temp file is written with an ordinary open, so it ends up with the same
    permissions any other file in the directory would get.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".dl-{uuid4().hex}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except BaseException:
        _unlink_quietly(str(tmp))
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
