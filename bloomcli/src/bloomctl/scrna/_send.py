"""Sending a staged h5ad to storage through the resumable upload, and saying what happened.

An object is named by the fingerprint of the file's contents, so storage holding that name
means it holds these bytes. An interrupted send resumes from the last byte storage took.
"""

from __future__ import annotations

import time
from pathlib import Path

import click
import httpx

from . import _object, _session, _transfer


def send_through_expiry(http, conn, profile: str, stage, staged, name: str) -> None:
    """Send the file, signing in again if the session expires while it is in flight.

    A login lasts about an hour and a large file can take longer, so an expiry part-way is
    ordinary rather than exceptional. The credentials that made the session are on disk, so
    this is ours to put right: sign in again and carry on from the last byte storage took.
    One retry only -- a second expiry is not a token running out.
    """
    try:
        _send(http, conn.endpoint, stage, staged, name)
        return
    except _transfer.SessionExpired:
        pass
    try:
        _send(http, _session.connect(profile).endpoint, stage, staged, name)
    except _transfer.SessionExpired as exc:
        raise click.ClickException(
            f"{exc} Nothing was lost: what has been sent of {name} is kept, and the same "
            "command continues it once you are logged in."
        ) from exc


def _resumable(ep, saved) -> str | None:
    """The address of the upload a record names, or None when the record cannot name one."""
    if not saved:
        return None
    try:
        return _transfer.resumable_url(ep, saved["id"])
    except _transfer.TransferError:
        # A record this cannot use is a record to forget, not one to fail on every run.
        return None


def _size_in_storage(http, ep, staged: _object.Staged) -> int | None | str:
    """The size storage holds for this file: a number, None for nothing there, "unknown"."""
    bucket, path = _object.BUCKET, _object.object_path(staged.fingerprint)
    for attempt in (1, 2):
        try:
            return _transfer.stored_size(http, ep, bucket, path)
        except (_transfer.SessionExpired, _transfer.Forbidden):
            raise
        except (_transfer.TransferError, httpx.HTTPError):
            if attempt == 2:
                return "unknown"
            # A server that is overloaded needs a moment, not a second request at once.
            time.sleep(_transfer.RETRY_PAUSE_SECONDS)
    return "unknown"


def _settle(http, ep, stage: Path, staged: _object.Staged, name: str, *, sent: bool) -> None:
    """Report what storage holds, and leave behind only what a rerun needs.

    An object's name is the fingerprint of the file's contents, so storage holding that name
    means it holds these bytes -- there is nothing to compare and nothing to send.
    """
    bucket, path = _object.BUCKET, _object.object_path(staged.fingerprint)
    size = _size_in_storage(http, ep, staged)
    if size == "unknown":
        raise click.ClickException(
            f"{name} may be stored — storage could not be asked. Nothing was lost: what is "
            "prepared is kept. Run the same command again."
        )
    if size:
        _object.clear(stage, staged.fingerprint)
        click.echo(
            f"Uploaded {name}\n  fingerprint  {staged.fingerprint}\n"
            f"  stored at    {bucket}/{path}" if sent else
            f"Already uploaded: {bucket}/{path}\n"
            f"  {name} is byte-for-byte the file already stored under that fingerprint."
        )
        return
    if size == 0:
        # A finalisation that kept nothing. The name cannot be reused and writers cannot delete.
        _object.forget_upload(stage, staged.fingerprint)
        raise click.ClickException(
            f"{bucket}/{path} is stored and holds nothing, so {name} cannot be sent under that "
            f"name — a stored object cannot be replaced. Ask an admin to remove it. What is "
            "prepared is kept."
        )
    if sent:
        # Storage took every byte and made nothing of them; the protocol will not finish an
        # upload already at full length, so the record goes and the gzipped form starts anew.
        _object.forget_upload(stage, staged.fingerprint)
        raise click.ClickException(
            f"storage took every byte of {name} but has not stored it. What is prepared is "
            "kept; run the same command again to send it as a new upload."
        )
    raise click.ClickException(
        f"storage refused the name {bucket}/{path} and holds nothing under it — another upload "
        "of the same file may be in flight. Nothing was sent; what is prepared is kept. "
        "Run the same command again shortly."
    )


def _send(http, ep, stage: Path, staged: _object.Staged, name: str) -> None:
    bucket, path = _object.BUCKET, _object.object_path(staged.fingerprint)
    held = 0  # bytes storage holds for this upload

    def acknowledged(offset: int, _size: int) -> None:
        nonlocal held
        held = offset

    try:
        already = _size_in_storage(http, ep, staged)
        if isinstance(already, int) and already > 0:
            # The name is this file's fingerprint, so storage holding it holds these bytes.
            _object.clear(stage, staged.fingerprint)
            click.echo(
                f"Already uploaded: {bucket}/{path}\n"
                f"  {name} is byte-for-byte the file already stored under that fingerprint."
            )
            return
        # Only an upload for these bytes, on this server, is worth resuming.
        saved = _object.load_upload(
            stage, staged.fingerprint, api_url=ep.api_url, size=staged.size
        )
        url = _resumable(ep, saved)
        offset = _transfer.upload_offset(http, ep, url, staged.size) if url else None
        if offset is None:
            url = _transfer.create_upload(http, ep, bucket, path, staged.size)
            _object.save_upload(
                stage, staged.fingerprint, _transfer.upload_id_of(url), staged.size, ep.api_url
            )
            offset = 0
        held = offset
        final = _transfer.send(
            http, ep, url, staged.gz_path, offset, staged.size, on_progress=acknowledged
        )
    except _transfer.AlreadyStored:
        # Storage refused the name. Whether that means the file is there is its answer to give.
        _settle(http, ep, stage, staged, name, sent=held > 0)
        return
    except _transfer.Forbidden as exc:
        raise click.ClickException(
            f"{exc} Nothing was lost: what has been sent of {name} is kept."
        ) from exc
    except _transfer.SessionExpired:
        raise  # a token that ran out is answered by signing in again, not by stopping
    except (_transfer.TransferError, httpx.HTTPError) as exc:
        reason = str(exc) or type(exc).__name__
        if held:
            raise click.ClickException(
                f"the upload of {name} stopped after {held:,} of {staged.size:,} bytes: "
                f"{reason}. Run the same command again to resume from there."
            ) from exc
        raise click.ClickException(
            f"the upload of {name} did not start: {reason}. Nothing was sent; run the same "
            "command again."
        ) from exc
    if final != staged.size:
        raise click.ClickException(
            f"storage took {final:,} of {staged.size:,} bytes of {name}. Nothing was lost — "
            "run the same command again to finish it."
        )
    # Sending every byte is not the same as storage having stored the object.
    _settle(http, ep, stage, staged, name, sent=True)
