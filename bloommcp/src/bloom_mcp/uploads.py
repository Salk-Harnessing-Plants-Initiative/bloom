"""Receive and register uploaded analysis inputs (auth-deferred, flat namespace).

Files land at ``bloommcp_input/<name>`` under ``bloom_agent``. Small files pass
through the backend and are validated in memory (:func:`receive_upload`); large
files use a scoped signed upload URL so the client streams **directly to Storage**
(:func:`signed_input_upload`), with content validated lazily when the reader loads
the object. Per-user identity/namespacing is deferred (#406) — the namespace is flat
and shared, so a same-named upload overwrites.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from bloom_mcp import input_formats, supabase_client


def buffered_limit_exceeded(content_length: str | None) -> int | None:
    """Return the declared upload size if it exceeds the buffered-route cap, else
    ``None`` — so the ``/uploads`` route can reject an oversized file from its
    ``Content-Length`` **before** reading the body into memory.

    ``None`` when the header is absent or unparseable (nothing to pre-check): the
    per-format cap still applies after buffering, and large files should use the
    signed-URL path regardless.
    """
    if content_length is None:
        return None
    try:
        size = int(content_length)
    except ValueError:
        return None
    return size if size > input_formats.MAX_BUFFERED_UPLOAD_SIZE else None


def _basename(filename: str) -> str:
    """Strip any directory components so an upload cannot target a sub-path."""
    name = PurePosixPath(filename or "").name
    if not name:
        raise input_formats.UnsupportedFormatError("upload filename is empty")
    return name


def receive_upload(filename: str, data: bytes) -> dict:
    """Validate an uploaded input (bounded peek) and store it flat in
    ``bloommcp_input/``.

    Returns ``{"input_ref", "format", "columns"}``. Raises
    :class:`input_formats.FormatError` subclasses on an unsupported, oversize, or
    unparseable upload — the caller maps these to structured HTTP errors.
    """
    name = _basename(filename)
    peek = input_formats.validate_upload(name, data)
    spec = input_formats.get_format_by_filename(name)
    ref = supabase_client.write_input(name, data)
    return {"input_ref": ref, "format": spec.id, "columns": list(peek.columns)}


def signed_input_upload(filename: str) -> dict:
    """Validate the format is registered and mint a scoped signed upload URL.

    The client uploads bytes directly to ``bloommcp_input/<name>``; content is
    validated lazily when the reader loads the object (the large-file path avoids
    buffering GB through the backend). Returns
    ``{"input_ref", "format", "upload"}``. Raises
    :class:`input_formats.UnsupportedFormatError` for an unregistered format.
    """
    name = _basename(filename)
    spec = input_formats.get_format_by_filename(name)
    if spec is None:
        raise input_formats.UnsupportedFormatError(
            f"unsupported input format for {name!r}; accepted extensions: "
            f"{', '.join(input_formats.registered_extensions())}"
        )
    signed = supabase_client.create_signed_upload_url(name)
    return {"input_ref": name, "format": spec.id, "upload": signed}
