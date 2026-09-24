"""`bloomctl scrna upload`: put a dataset's h5ad into storage, gzipped, under its fingerprint."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import click
import httpx

from ..credentials import DEFAULT_PROFILE
from . import _format, _object, _session, _transfer

MB = 1024 * 1024


@click.command(name="upload")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "-p",
    "--profile",
    default=DEFAULT_PROFILE,
    show_default=True,
    help="Credentials profile to use.",
)
def upload(file: Path, profile: str) -> None:
    """Upload a dataset's AnnData file (.h5ad), gzipped and named by its SHA-256.

    Needs a writer or admin login. The file's structure is checked before anything is sent.
    An interrupted upload resumes when the same command is run again.
    """
    conn = _session.connect(profile)
    if conn.role not in _session.WRITE_ROLES:
        raise click.ClickException(
            f"this login signs in as {conn.role or 'no role'}; uploading needs bloom_writer or "
            "bloom_admin. Nothing was read."
        )
    try:
        summary = _format.check_structure(file)
    except _format.MissingExtra as exc:
        raise click.ClickException(str(exc)) from exc
    except _format.FormatError as exc:
        raise click.ClickException(f"{file.name} does not meet Bloom's h5ad format: {exc}") from exc

    stage = _object.staging_dir()
    staged = _object.stage(file, stage)
    try:
        if summary.normalization is None and not _normalization_on_record(
            conn.client, staged.fingerprint, summary.layers
        ):
            raise click.ClickException(
                f"{file.name} has no uns['normalization'] saying how X was made. Add one with "
                "transform (log1p, log2p or none), scaling (library_size, none or other), and "
                "target_sum for library_size or a description for other."
            )
        if staged.size > _object.MAX_OBJECT_BYTES:
            raise click.ClickException(
                f"{file.name} gzips to {staged.size / MB:,.0f} MB; the limit is "
                f"{_object.MAX_OBJECT_BYTES // MB} MB. Nothing was sent."
            )
    except click.ClickException:
        _object.clear(stage, staged.fingerprint)
        raise

    with _transfer.open_client() as http:
        _send(http, conn.endpoint, stage, staged, file.name)


def _normalization_on_record(client: Any, fingerprint: str, layers) -> bool:
    """Whether a dataset loaded from this exact file records how its X was made."""
    from .._postgrest import queried

    rows = queried(
        "the datasets loaded from this file",
        lambda: client.table("scrna_datasets")
        .select("id, metadata")
        .eq("source_checksum", fingerprint)
        .is_("deleted_at", "null")
        .execute()
        .data,
    ) or []
    for row in rows:
        block = (row.get("metadata") or {}).get("normalization")
        if isinstance(block, dict) and _format.normalization_problem(block, layers=layers) is None:
            return True
    return False


def _resumable(ep, saved) -> str | None:
    """The address of the upload a record names, or None when the record cannot name one."""
    if not saved:
        return None
    try:
        return _transfer.resumable_url(ep, saved["id"])
    except _transfer.TransferError:
        # A record this cannot use is a record to forget, not one to fail on every run.
        return None


def _stored(http, ep, stage: Path, staged: _object.Staged) -> str:
    """What storage holds under this file's name.

    "stored" this exact file · "empty" the name taken by nothing · "other" taken by different
    content · "absent" nothing there · "unknown" storage could not be asked.
    """
    bucket, path = _object.BUCKET, _object.object_path(staged.fingerprint)
    for attempt in (1, 2):
        try:
            length = _transfer.object_length(http, ep, bucket, path)
            break
        except (_transfer.SessionExpired, _transfer.Forbidden):
            raise
        except (_transfer.TransferError, httpx.HTTPError):
            if attempt == 2:
                return "unknown"
            # A server that is overloaded needs a moment, not a second request at once.
            time.sleep(_transfer.RETRY_PAUSE_SECONDS)
    if length is None:
        return "absent"
    if length == 0:
        return "empty"
    if length == staged.size:
        return "stored"
    # Another writer's gzip of the same file is a different length, so only the content says.
    return _holds_this_file(http, ep, stage, staged, bucket, path)


def _holds_this_file(http, ep, stage: Path, staged: _object.Staged, bucket, path) -> str:
    """Fetch what is stored and fingerprint it; nothing else tells these two apart."""
    checking = stage / f".checking-{uuid4().hex}.tmp"
    try:
        got = _transfer.download_to(http, ep, bucket, path, checking)
    except (_transfer.SessionExpired, _transfer.Forbidden):
        raise
    except (_transfer.TransferError, httpx.HTTPError):
        return "unknown"
    finally:
        checking.unlink(missing_ok=True)
    return "stored" if got == staged.fingerprint else "other"


def _settle(http, ep, stage: Path, staged: _object.Staged, name: str, *, finished: bool) -> None:
    """Report what storage holds, and leave behind only what a rerun needs.

    ``finished`` is whether this run sent the file to its end, which separates an upload
    storage never made an object of from a name that was taken before it started.
    """
    bucket, path = _object.BUCKET, _object.object_path(staged.fingerprint)
    state = _stored(http, ep, stage, staged)
    if state == "stored":
        _object.clear(stage, staged.fingerprint)
        click.echo(
            f"Uploaded {name}\n  fingerprint  {staged.fingerprint}\n"
            f"  stored at    {bucket}/{path}" if finished else
            f"Already uploaded: {bucket}/{path}"
        )
        return
    if state == "unknown":
        raise click.ClickException(
            f"{name} may be stored — storage could not be asked. Nothing was lost: what is "
            f"prepared is kept in {stage}. Run the same command again."
        )
    if state in ("empty", "other"):
        held = "nothing" if state == "empty" else "different content"
        _object.forget_upload(stage, staged.fingerprint)
        raise click.ClickException(
            f"{bucket}/{path} is already stored and holds {held}, so {name} cannot be sent "
            f"under that name — a stored object cannot be replaced. Ask an admin to remove it. "
            f"What is prepared is kept in {stage}."
        )
    if finished:
        # Storage holds every byte and made nothing of them. The protocol will not finish an
        # upload already at full length, so the record goes and the gzipped form starts anew.
        _object.forget_upload(stage, staged.fingerprint)
        raise click.ClickException(
            f"storage took every byte of {name} but has not stored it. What is prepared is "
            f"kept in {stage}; run the same command again to send it as a new upload."
        )
    raise click.ClickException(
        f"storage refused the name {bucket}/{path} and holds nothing under it — another "
        f"upload of the same file may be in flight. Nothing was sent; what is prepared is "
        f"kept in {stage}. Run the same command again shortly."
    )


def _send(http, ep, stage: Path, staged: _object.Staged, name: str) -> None:
    bucket, path = _object.BUCKET, _object.object_path(staged.fingerprint)
    held = 0  # bytes storage holds for this upload

    def acknowledged(offset: int, _size: int) -> None:
        nonlocal held
        held = offset

    try:
        if _stored(http, ep, stage, staged) == "stored":
            _object.clear(stage, staged.fingerprint)
            click.echo(f"Already uploaded: {bucket}/{path}")
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
        _settle(http, ep, stage, staged, name, finished=False)
        return
    except (_transfer.SessionExpired, _transfer.Forbidden) as exc:
        raise click.ClickException(
            f"{exc} Nothing was lost: what has been sent of {name} is kept in {stage}, and the "
            "same command continues it once you are logged in."
        ) from exc
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
    _settle(http, ep, stage, staged, name, finished=True)
