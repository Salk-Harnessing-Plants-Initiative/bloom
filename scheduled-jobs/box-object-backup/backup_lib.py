"""Helpers for backup_objects.py — object model, path mapping, planning.

Pure functions only, so backup_lib_test.py can exercise them without a
database, an rclone daemon, or a Box account. The one stateful piece, the
copy ledger, lives in ledger.py.

The mapping this module implements is the whole point of the job. Supabase
Storage stores every object in one backing MinIO bucket, under a tenant
prefix, at `<bucket_id>/<name>/<version>` — so the last path segment is a
version UUID
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
        """Key RELATIVE to the backing bucket and tenant prefix.

        This is not a whole MinIO address: `source_remote` prepends the
        tenant prefix, and `MinioSource.fs` names the bucket. Callers that
        hand this straight to rclone against a provider-root fs get the
        object's own `bucket_id` read as a MinIO bucket name.

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
        """What the ledger records, in the SAME form `box_path` writes.

        Normalized to match the destination. Two names can differ as text and
        still be one file on Box — an accent written as a single character, or
        as a letter plus a combining mark, looks identical and normalizes the
        same way. Keyed on the raw name, the ledger held two entries claiming
        two backups while Box held one file, the second having overwritten the
        first, with nothing reporting it.

        This makes the record honest; it does not stop the collision. Two
        objects sharing a destination still means only one survives, and
        catching that needs a check at planning time.

        Changed while the ledger was empty. Afterwards it would not be worth
        it: every existing entry would stop matching and the next run would
        treat all eight million objects as never copied.
        """
        return (self.bucket_id, unicodedata.normalize("NFC", self.name))


@dataclass(frozen=True)
class SkippedObject:
    obj: StorageObject
    reason: str


@dataclass(frozen=True)
class CopiedRecord:
    """What the ledger holds for one destination path.

    `raw_name` is the object's name as Postgres stores it, which is NOT the
    key it is filed under — the key is normalized, because that is what Box
    writes. Keeping the raw name is what makes a collision detectable: a
    second object whose name normalizes onto an existing row, but reads
    differently in the database, is a different object heading for a path
    that is already taken.
    """

    version: str | None
    raw_name: str | None = None


@dataclass(frozen=True)
class CopyPlan:
    """What the run intends to do, after ledger and safety filtering."""

    copies: tuple[StorageObject, ...]
    skipped: tuple[SkippedObject, ...]
    already_current: int
    # Counted apart from `skipped`, which is mostly names Box will not accept.
    # A collision is a different problem: the object is fine, another one is
    # already using its destination, and no rename at this end can fix it.
    collisions: int = 0

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
    """SQL listing the objects to mirror, oldest-first within each bucket.

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


def source_remote(obj: StorageObject, prefix: str = "") -> str:
    """Remote of one object within the backing bucket's fs.

    storage-api in single-tenant mode files everything under a tenant
    prefix, so the full address is
    `<backing bucket>/<prefix>/<bucket_id>/<name>/<version>`. The bucket is
    carried by the fs; this supplies everything after it.

    On the prod stack that is
    `bloom-storage/storage-single-tenant/<bucket_id>/<name>/<version>`.

    Configuration rather than a constant because nothing in the stack declares
    it: storage-api chooses the prefix, and no compose file or env var names
    it. The same path is assumed by services/video-worker/video_listener.py.
    """
    tail = obj.minio_key
    cleaned = prefix.strip("/")
    return f"{cleaned}/{tail}" if cleaned else tail


def box_path(obj: StorageObject, root: str = "") -> str:
    """Destination path under the Box root, NFC-normalized.

    Box normalizes unicode in names, so normalizing here means a re-run does
    not see every accented filename as missing.

    `ledger_key` normalizes the same way, so the record and the destination
    agree. It did not always — this docstring claimed they did while the
    ledger was keyed on the raw name, which is how the divergence went
    unnoticed.
    """
    path = unicodedata.normalize("NFC", obj.storage_path)
    root = root.strip("/")
    return f"{root}/{path}" if root else path



# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------

def collision_reason(held_by: str) -> str:
    """Why an object is refused when its destination is already spoken for.

    The holder is rendered with `ascii()`, not `repr()`. The two names are
    identical to a human by definition — that is what makes them collide — so
    printing them plainly gives the operator no way to tell which file in
    Supabase is which. Escaped, one reads `caf\\xe9.png` and the other
    `cafe\\u0301.png`.

    The remedy names THIS object, not either of them. Renaming the holder
    leaves its ledger row still claiming the path, and nothing prunes that row,
    so this object would be refused for ever.
    """
    return (
        f"its name normalizes onto {ascii(held_by)}, which is a different "
        "object in the database but the same path on Box — backing up both "
        "would leave only one, so neither is overwritten. Rename THIS object "
        "at the source, not the one named above."
    )


def build_plan(
    objects: Iterable[StorageObject],
    copied: dict[tuple[str, str], CopiedRecord],
    limit: int | None = None,
) -> CopyPlan:
    """Decide what to copy: skip unsafe paths, collisions, and current versions.

    Two names that differ as text can normalize to one Box path — an accent
    written as a single character, or as a letter plus a combining mark. Both
    are real, distinct objects in `storage.objects`; only one file can exist at
    the destination. Copying both means one silently overwrites the other, and
    on the next run the ledger's version no longer matches the loser, so it is
    copied back over the winner — forever, with nothing reporting it.

    So the first name to reach a path keeps it, and anything else landing there
    is skipped and named. That is deterministic across runs, because the ledger
    records which raw name holds the path.
    """
    copies: list[StorageObject] = []
    skipped: list[SkippedObject] = []
    already = 0
    # Claimed within this plan as well as in the ledger: a batch can contain
    # both halves of a collision, neither of which has been copied yet.
    claimed: dict[tuple[str, str], str] = {}
    collisions = 0
    for obj in objects:
        reason = unsafe_reason(obj)
        if reason:
            skipped.append(SkippedObject(obj, reason))
            continue
        key = obj.ledger_key
        record = copied.get(key)
        holder = claimed.get(key)
        if holder is None and record is not None:
            # None on rows written before the column existed, which reads as
            # "nobody is known to hold this" — the only safe reading, since
            # treating unknown as taken would refuse every object in an
            # existing ledger.
            holder = record.raw_name
        if holder is not None and holder != obj.name:
            skipped.append(SkippedObject(obj, collision_reason(holder)))
            collisions += 1
            continue
        claimed[key] = obj.name
        if record is not None and record.version == obj.version:
            already += 1
            continue
        if limit is not None and len(copies) >= limit:
            continue
        copies.append(obj)
    return CopyPlan(tuple(copies), tuple(skipped), already, collisions)


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
