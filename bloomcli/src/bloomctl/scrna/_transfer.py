"""Resumable upload and checked download of dataset objects, over the storage API.

Uploads go through the storage service's TUS endpoint, so a dropped connection resumes from the
last byte the server acknowledged rather than sending hundreds of MB again. Downloads stream,
decompressing and hashing as they arrive, so a large file is never held in memory.
"""

from __future__ import annotations

import base64
import hashlib
import zlib
from pathlib import Path
from typing import Callable, NamedTuple

import httpx

TUS_VERSION = "1.0.0"

# The storage service's resumable chunk size; every chunk but the last must be this long.
CHUNK_BYTES = 6 * 1024 * 1024


class TransferError(RuntimeError):
    """Storage refused a request, or a transfer ended early."""


class AlreadyStored(TransferError):
    """An object with this name is already stored."""


class NotStored(TransferError):
    """No object with this name is stored."""


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
    """Storage's answer when an object with this name exists (not a TUS offset conflict)."""
    if response.status_code != 409:
        return False
    try:
        body = response.json()
    except ValueError:
        return False
    return isinstance(body, dict) and (
        body.get("error") == "Duplicate" or "already exists" in str(body.get("message", "")).lower()
    )


def _session_expired(response: httpx.Response) -> bool:
    """Storage answers a caller whose session is no longer valid much as it answers a missing
    object, so the message is the only signal — the same trap `_storage.py` documents."""
    from .._storage import looks_like_expired_session

    return looks_like_expired_session(RuntimeError(response.text))


def _refuse_if_expired(response: httpx.Response) -> None:
    if _session_expired(response):
        raise SessionExpired(
            "your session is no longer valid — log in again (`bloomctl login`) and retry"
        )


def object_exists(http: httpx.Client, ep: Endpoint, bucket: str, path: str) -> bool:
    response = http.head(ep.url(f"object/authenticated/{bucket}/{path}"), headers=ep.headers())
    if response.status_code == 200:
        return True
    if response.status_code in (400, 404):
        _refuse_if_expired(response)
        return False
    raise TransferError(f"storage answered {response.status_code} when looking for {bucket}/{path}")


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
    if _duplicate(response):
        raise AlreadyStored(f"{bucket}/{path}")
    location = response.headers.get("location")
    if response.status_code != 201 or not location:
        raise TransferError(
            f"storage refused to start the upload ({response.status_code}): {response.text[:200]}"
        )
    # Storage may answer with its own internal address; the upload's id is what identifies it.
    return ep.url("upload/resumable/" + location.rstrip("/").rsplit("/", 1)[-1])


def upload_id_of(url: str) -> str:
    """The upload's id inside its address."""
    return url.rstrip("/").rsplit("/", 1)[-1]


def resumable_url(ep: Endpoint, upload_id: str) -> str:
    return ep.url(f"upload/resumable/{upload_id}")


def _offset_header(response: httpx.Response) -> int:
    try:
        return int(response.headers["upload-offset"])
    except (KeyError, ValueError) as exc:
        raise TransferError("storage did not say how many bytes it holds (upload-offset)") from exc


def upload_offset(http: httpx.Client, ep: Endpoint, url: str) -> int | None:
    """Bytes the server holds for an upload, or None when this session cannot resume it.

    Anything but a plain answer means starting a new upload: an upload the server has forgotten
    (404/410) and one this session may not ask about (401/403) both leave nothing to resume, and
    failing instead would wedge every later run on a record the user cannot see.
    """
    response = http.head(url, headers=ep.headers({"Tus-Resumable": TUS_VERSION}))
    if response.status_code in (401, 403, 404, 410):
        return None
    if response.status_code != 200:
        raise TransferError(f"storage answered {response.status_code} for the upload's progress")
    return _offset_header(response)


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
            if _duplicate(response):
                raise AlreadyStored(url)
            if response.status_code != 204:
                raise TransferError(
                    f"storage refused bytes {offset:,} to {offset + len(chunk):,} "
                    f"({response.status_code}): {response.text[:200]}"
                )
            acknowledged = _offset_header(response)
            if acknowledged <= offset or acknowledged > size:
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
        if response.status_code in (400, 404):
            response.read()  # a streamed response holds nothing until it is read
            _refuse_if_expired(response)
            raise NotStored(f"{bucket}/{path}")
        if response.status_code != 200:
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
