"""Helpers for backup_objects.py — object model, path mapping, planning.

Pure functions only, so backup_lib_test.py can exercise them without a
database, an rclone daemon, or a Box account. The one stateful piece, the
copy ledger, lives in ledger.py.

The mapping this module implements is the whole point of the job. Supabase
Storage stores every object in one backing MinIO bucket under a key of
`<bucket_id>/<name>/<version>`, so the last path segment is a version UUID
and the file has no extension — Box shows those as unpreviewable blobs. We
copy each object to `<bucket_id>/<name>` instead, which is the path the
Storage API serves it under, so the Box copy keeps its `.png` and previews.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Iterable, Iterator, Sequence

# The single MinIO bucket the Storage API writes every tenant object into.
# Matches STORAGE_S3_BUCKET in docker-compose.prod.yml.
BACKING_BUCKET = "bloom-storage"

# Scratch bucket for in-flight resumable (tus) uploads. Its contents are
# partial by definition, so mirroring them to Box is noise.
DEFAULT_EXCLUDED_BUCKETS = ("tus-files",)

# Box rejects these outright in file and folder names, and silently trims
# trailing spaces/periods. An object whose logical name contains one cannot
# round-trip, so we skip it loudly rather than mangle the path.
BOX_ILLEGAL_CHARS = frozenset('\\/:*?"<>|')
BOX_TRAILING_ILLEGAL = " ."

# Box caps a single file at 50 GB on business plans; nothing in Bloom comes
# close, but a surprise here means a doomed transfer we should skip early.
BOX_MAX_FILE_BYTES = 50 * 1024**3

FIELD_SEP = "\t"

# SQLite's compiled-in parameter cap (SQLITE_MAX_VARIABLE_NUMBER) is 999 on
# older builds. Batched lookups stay under it rather than assume a newer one.
SQLITE_MAX_VARIABLES = 900


@dataclass(frozen=True)
class StorageObject:
    """One row of `storage.objects` — the logical view of a stored file."""

    bucket_id: str
    name: str
    version: str | None
    size: int | None
    updated_at: str

    @property
    def minio_key(self) -> str:
        """Key inside the backing bucket, as the Storage API writes it.

        Pre-version objects (written before storage-api added versioning)
        have no suffix, so the key is just `<bucket>/<name>`.
        """
        if self.version:
            return f"{self.bucket_id}/{self.name}/{self.version}"
        return f"{self.bucket_id}/{self.name}"

    @property
    def storage_path(self) -> str:
        """Path the Storage API serves this object under — the Box path."""
        return f"{self.bucket_id}/{self.name}"

    @property
    def ledger_key(self) -> tuple[str, str]:
        return (self.bucket_id, self.name)


@dataclass(frozen=True)
class SkippedObject:
    obj: StorageObject
    reason: str


@dataclass(frozen=True)
class CopyPlan:
    """What the run intends to do, after ledger and safety filtering."""

    copies: tuple[StorageObject, ...]
    skipped: tuple[SkippedObject, ...]
    already_current: int

    @property
    def total_bytes(self) -> int:
        return sum(o.size or 0 for o in self.copies)


class BackupError(Exception):
    """Configuration or input error that should stop the run."""


# ---------------------------------------------------------------------------
# Manifest query + parsing
# ---------------------------------------------------------------------------

def objects_query(
    buckets: Sequence[str] | None = None,
    excluded: Sequence[str] = DEFAULT_EXCLUDED_BUCKETS,
    since: str | None = None,
) -> str:
    """SQL listing the objects to mirror, newest-first within each bucket.

    `since` filters on `updated_at` so the weekly delta never enumerates the
    whole table. Identifiers are quoted by `_sql_literal`; every caller-
    supplied value reaches Postgres as a literal, never as raw SQL.
    """
    where = ["o.name IS NOT NULL"]
    if buckets:
        where.append(f"o.bucket_id IN ({_in_list(buckets)})")
    if excluded:
        where.append(f"o.bucket_id NOT IN ({_in_list(excluded)})")
    if since:
        where.append(f"o.updated_at > {_sql_literal(since)}::timestamptz")
    return (
        "SELECT o.bucket_id, o.name, o.version, "
        "COALESCE((o.metadata->>'size')::bigint, -1), "
        "to_char(o.updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SSOF') "
        "FROM storage.objects o WHERE " + " AND ".join(where) + " "
        "ORDER BY o.bucket_id, o.updated_at"
    )


def _in_list(values: Sequence[str]) -> str:
    return ", ".join(_sql_literal(v) for v in values)


def _sql_literal(value: str) -> str:
    """Single-quoted SQL literal. Doubles embedded quotes, rejects NUL."""
    if "\x00" in value:
        raise BackupError(f"NUL byte in SQL value: {value!r}")
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def parse_manifest(raw: str) -> list[StorageObject]:
    """Parse tab-separated `psql -At -F '\\t'` output into objects."""
    return list(iter_manifest(raw.splitlines()))


def iter_manifest(lines: Iterable[str]) -> Iterator[StorageObject]:
    """Stream objects out of tab-separated psql output, one line at a time.

    Prod has millions of rows, so the manifest is written to a file and
    walked lazily rather than held as one string. Object names may contain
    almost anything except a tab or a newline (Postgres would have to quote
    those, and the Storage API never writes them), so a plain split is safe.
    A row with the wrong field count is a parser bug, not a recoverable
    condition — surface it rather than mirror garbage.
    """
    for lineno, line in enumerate(lines, start=1):
        line = line.rstrip("\n")
        if not line.strip():
            continue
        fields = line.split(FIELD_SEP)
        if len(fields) != 5:
            raise BackupError(
                f"manifest line {lineno}: expected 5 fields, got {len(fields)}: {line!r}"
            )
        bucket_id, name, version, size_raw, updated_at = fields
        size = int(size_raw) if size_raw not in ("", "-1") else None
        yield StorageObject(
            bucket_id=bucket_id,
            name=name,
            version=version or None,
            size=size,
            updated_at=updated_at,
        )


def batches(objects: Iterable[StorageObject], size: int) -> Iterator[list[StorageObject]]:
    """Group a stream of objects into fixed-size lists."""
    batch: list[StorageObject] = []
    for obj in objects:
        batch.append(obj)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------

def unsafe_reason(obj: StorageObject) -> str | None:
    """Why this object cannot be mirrored to Box under its logical path.

    Returns None when the object is safe to copy.
    """
    name = obj.name
    if not name or name.startswith("/"):
        return "empty or absolute object name"
    if ".." in PurePosixPath(name).parts:
        return "parent-directory segment in object name"
    if any(ord(ch) < 32 for ch in name):
        return "control character in object name"
    illegal = sorted(BOX_ILLEGAL_CHARS.intersection(set(name)) - {"/"})
    if illegal:
        return f"Box-illegal character(s) in object name: {''.join(illegal)}"
    for segment in PurePosixPath(name).parts:
        if segment != segment.rstrip(BOX_TRAILING_ILLEGAL):
            return f"path segment ends with space or period: {segment!r}"
        if not segment.strip():
            return "empty path segment in object name"
    if obj.size is not None and obj.size > BOX_MAX_FILE_BYTES:
        return f"object exceeds Box per-file limit: {obj.size} bytes"
    return None


def box_path(obj: StorageObject, root: str = "") -> str:
    """Destination path under the Box root, NFC-normalized.

    Box normalizes unicode in names; normalizing here keeps the ledger's
    idea of the destination identical to what Box actually stores, so a
    re-run doesn't see every accented filename as missing.
    """
    path = unicodedata.normalize("NFC", obj.storage_path)
    root = root.strip("/")
    return f"{root}/{path}" if root else path



# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------

def build_plan(
    objects: Iterable[StorageObject],
    copied: dict[tuple[str, str], str | None],
    limit: int | None = None,
) -> CopyPlan:
    """Decide what to copy: skip unsafe paths and already-current versions."""
    copies: list[StorageObject] = []
    skipped: list[SkippedObject] = []
    already = 0
    for obj in objects:
        reason = unsafe_reason(obj)
        if reason:
            skipped.append(SkippedObject(obj, reason))
            continue
        key = obj.ledger_key
        if key in copied and copied[key] == obj.version:
            already += 1
            continue
        if limit is not None and len(copies) >= limit:
            continue
        copies.append(obj)
    return CopyPlan(tuple(copies), tuple(skipped), already)


def chunked(items: Sequence, size: int) -> Iterator[Sequence]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def format_bytes(count: int) -> str:
    value = float(count)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(value) < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} TiB"
