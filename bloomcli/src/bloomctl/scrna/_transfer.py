"""Resumable upload and checked download of dataset objects, over the storage API.

Uploads go through the storage service's TUS endpoint, so a dropped connection resumes from the
last byte the server acknowledged rather than sending hundreds of MB again. Downloads stream,
decompressing and hashing as they arrive, so a large file is never held in memory.
"""

from __future__ import annotations

import base64
import hashlib
import re
import time
import zlib
from pathlib import Path
from typing import Any, Callable, NamedTuple

import httpx

TUS_VERSION = "1.0.0"

# The storage service's resumable chunk size; every chunk but the last must be this long.
CHUNK_BYTES = 6 * 1024 * 1024

# How many entries one listing request asks for; the storage service caps a page at 100.
LIST_PAGE = 100

# A moment before asking a server again, so one that is overloaded is not asked twice at once.
RETRY_PAUSE_SECONDS = 0.5


class TransferError(RuntimeError):
    """Storage refused a request, or a transfer ended early."""


class AlreadyStored(TransferError):
    """An object with this name is already stored."""


class NotStored(TransferError):
    """No object with this name is stored."""


class Forbidden(TransferError):
    """The login is understood and not permitted; logging in again changes nothing."""


class SessionExpired(TransferError):
    """Storage refused the request because this session is no longer valid."""


class Endpoint(NamedTuple):
    api_url: str
    anon_key: str
    token: str

    def url(self, suffix: str) -> str:
        return f"{self.api_url.rstrip('/')}/storage/v1/{suffix}"

    def headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}", "apikey": self.anon_key, **(extra or {})}


def open_client() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(120.0, connect=15.0))


def _metadata(**pairs: str) -> str:
    return ",".join(f"{key} {base64.b64encode(value.encode()).decode()}" for key, value in pairs.items())


def _duplicate(response: httpx.Response) -> bool:
    """Storage refusing to start an upload under a name it already holds.

    Read from the status: the body is plain text on one storage version and JSON on another.
    Only the create is asked this -- TUS answers 409 to a chunk whose offset does not match,
    which is a different thing entirely.
    """
    return response.status_code == 409


# What storage says when the token itself is the problem, as opposed to what it may reach.
_EXPIRED_IN_BODY = ("jwt expired", "invalid jwt", "bad_jwt", "token is expired")


EXPIRED_HINT = "your session is no longer valid — log in again with `bloomctl login`."

# An upload's id, as it may appear in a URL: no separators, no dot segments, nothing to escape.
_UPLOAD_ID = re.compile(r"\A[A-Za-z0-9._~-]{1,200}\Z")


def _checked_id(upload_id: str) -> str:
    if not isinstance(upload_id, str) or not _UPLOAD_ID.match(upload_id) \
            or upload_id.strip(".") == "":
        raise TransferError(f"storage named an upload this cannot address: {str(upload_id)[:60]!r}")
    return upload_id


def _refuse_if_refused(response: httpx.Response) -> None:
    """Separate a session that has ended from a login that was never allowed this.

    Both arrive as 401/403, and telling someone to log in again does nothing about the second.
    """
    refused = 400 <= response.status_code < 500
    body = (response.text or "").lower() if refused else ""
    if any(marker in body for marker in _EXPIRED_IN_BODY) or response.status_code == 401:
        raise SessionExpired(EXPIRED_HINT)
    if response.status_code == 403:
        raise Forbidden(
            "this login is not allowed to do that here. Logging in again will not change it; "
            "ask an admin what this account may read and write."
        )


def list_objects(
    http: httpx.Client,
    ep: Endpoint,
    bucket: str,
    prefix: str,
    *,
    search: str = "",
    limit: int = LIST_PAGE,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """One page of what storage holds under `prefix`: each entry's name, size and arrival."""
    response = http.post(
        ep.url(f"object/list/{bucket}"),
        headers=ep.headers(),
        json={"prefix": prefix, "search": search, "limit": limit, "offset": offset},
    )
    _refuse_if_refused(response)
    if response.status_code != 200:
        raise TransferError(
            f"storage answered {response.status_code} when asked what {bucket}/{prefix} holds"
        )
    try:
        listed = response.json()
    except ValueError as exc:
        raise TransferError(f"storage did not say what {bucket}/{prefix} holds") from exc
    if not isinstance(listed, list):
        raise TransferError(f"storage did not say what {bucket}/{prefix} holds")
    return [entry for entry in listed if isinstance(entry, dict)]


def stored_size(http: httpx.Client, ep: Endpoint, bucket: str, path: str) -> int | None:
    """How many bytes storage holds under this name, or None when it holds nothing there.

    Read from the listing, which reports a size outright. A ranged read cannot answer it: an
    empty object comes back as a server error, and a partial response describes the range it
    sent rather than the object it came from.
    """
    folder, _, name = path.rpartition("/")
    for entry in list_objects(http, ep, bucket, folder, search=name):
        if entry.get("name") == name:
            return object_size(entry)
    return None


def object_size(entry: dict[str, Any]) -> int:
    """The byte count a listing entry reports."""
    return int((entry.get("metadata") or {}).get("size") or 0)


def create_upload(http: httpx.Client, ep: Endpoint, bucket: str, path: str, size: int) -> str:
    """Start a resumable upload; return its address on the gateway."""
    response = http.post(
        ep.url("upload/resumable"),
        headers=ep.headers({
            "Tus-Resumable": TUS_VERSION,
            "Upload-Length": str(size),
            "Upload-Metadata": _metadata(
                bucketName=bucket, objectName=path, contentType="application/gzip"
            ),
            "x-upsert": "false",
        }),
    )
    _refuse_if_refused(response)
    if _duplicate(response):
        raise AlreadyStored(f"{bucket}/{path}")
    location = response.headers.get("location")
    if response.status_code != 201 or not location:
        raise TransferError(
            f"storage refused to start the upload ({response.status_code}): {response.text[:200]}"
        )
    # Storage may answer with its own internal address; the upload's id is what identifies it.
    return ep.url("upload/resumable/" + _checked_id(location.rstrip("/").rsplit("/", 1)[-1]))


def upload_id_of(url: str) -> str:
    """The upload's id inside its address."""
    return url.rstrip("/").rsplit("/", 1)[-1]


def resumable_url(ep: Endpoint, upload_id: str) -> str:
    return ep.url(f"upload/resumable/{_checked_id(upload_id)}")


def _offset_header(response: httpx.Response) -> int:
    try:
        return int(response.headers["upload-offset"])
    except (KeyError, ValueError) as exc:
        raise TransferError("storage did not say how many bytes it holds (upload-offset)") from exc


def upload_offset(
    http: httpx.Client, ep: Endpoint, url: str, size: int | None = None
) -> int | None:
    """Bytes the server holds for an upload, or None when this session cannot resume it.

    Anything but a plain answer means starting a new upload: an upload the server has forgotten
    (404/410) and one this session may not ask about (401/403) both leave nothing to resume, and
    failing instead would wedge every later run on a record the user cannot see.
    """
    for attempt in (1, 2):
        try:
            response = http.head(url, headers=ep.headers({"Tus-Resumable": TUS_VERSION}))
        except httpx.HTTPError:
            if attempt == 2:
                return None
            time.sleep(RETRY_PAUSE_SECONDS)
            continue
        if response.status_code == 200:
            break
        # A server that cannot answer right now may answer the next time; one that says the
        # upload is gone -- or will not discuss it with this login -- never will, and re-asking
        # only delays starting again. The refusal itself is reported by the next call.
        if attempt == 2 or response.status_code < 500:
            return None
        time.sleep(RETRY_PAUSE_SECONDS)
    try:
        offset = int(response.headers["upload-offset"])
    except (KeyError, ValueError):
        return None
    # An offset this file cannot continue from is not a resume point: seeking behind it raises,
    # and seeking past the end sends nothing and fails at the same place on every later run.
    if offset < 0 or (size is not None and offset > size):
        return None
    return offset


def send(
    http: httpx.Client,
    ep: Endpoint,
    url: str,
    source: Path,
    offset: int,
    size: int,
    on_progress: Callable[[int, int], None] | None = None,
) -> int:
    """Send ``source`` from ``offset`` to the end, one chunk per request.

    Returns the bytes storage acknowledged. Each chunk starts where storage says it is, not
    where the last read finished: it may keep fewer bytes than were sent, and continuing from
    the wrong place would store an object that is not the file its name claims.
    """
    with source.open("rb") as fh:
        while offset < size:
            fh.seek(offset)
            chunk = fh.read(CHUNK_BYTES)
            if not chunk:
                raise TransferError(f"{source} is shorter than the upload it belongs to")
            response = http.patch(
                url,
                content=chunk,
                headers=ep.headers({
                    "Tus-Resumable": TUS_VERSION,
                    "Upload-Offset": str(offset),
                    "Content-Type": "application/offset+octet-stream",
                }),
            )
            _refuse_if_refused(response)
            if response.status_code != 204:
                raise TransferError(
                    f"storage refused bytes {offset:,} to {offset + len(chunk):,} "
                    f"({response.status_code}): {response.text[:200]}"
                )
            acknowledged = _offset_header(response)
            # Never past what this chunk carried: bytes beyond it were never sent, and taking
            # storage's word would leave a hole in an object named after the whole file.
            if acknowledged <= offset or acknowledged > offset + len(chunk):
                raise TransferError(
                    f"storage acknowledged {acknowledged:,} bytes of {size:,} after "
                    f"{offset:,} were already sent"
                )
            offset = acknowledged
            if on_progress:
                on_progress(offset, size)
    return offset


def download_to(http: httpx.Client, ep: Endpoint, bucket: str, path: str, dest: Path) -> str:
    """Stream an object into ``dest`` decompressed; return the SHA-256 of what was written."""
    decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    digest = hashlib.sha256()
    with http.stream(
        "GET", ep.url(f"object/authenticated/{bucket}/{path}"), headers=ep.headers()
    ) as response:
        if response.status_code != 200:
            response.read()  # a streamed response holds nothing until it is read
            _refuse_if_refused(response)
            if response.status_code in (400, 404):
                raise NotStored(f"{bucket}/{path}")
            raise TransferError(f"storage answered {response.status_code} for {bucket}/{path}")
        try:
            with dest.open("wb") as out:
                for block in response.iter_bytes():
                    data = decompressor.decompress(block)
                    digest.update(data)
                    out.write(data)
                data = decompressor.flush()
                digest.update(data)
                out.write(data)
        except zlib.error as exc:
            raise TransferError(f"{bucket}/{path} is not a gzipped file: {exc}") from exc
    if not decompressor.eof:
        raise TransferError(f"the download of {bucket}/{path} ended before the file did")
    return digest.hexdigest()
