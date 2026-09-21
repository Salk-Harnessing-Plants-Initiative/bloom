"""`bloomctl scrna upload`: put a dataset's h5ad into storage, gzipped, under its fingerprint."""

from __future__ import annotations

from pathlib import Path
from typing import Any

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


def _stored(http, ep, bucket: str, path: str) -> bool | None:
    """Whether storage holds the object: True, False, or None when it could not be asked.

    Asked twice: one blip on this question used to turn a stored object into a failure that
    told the user to send every byte again.
    """
    for attempt in (1, 2):
        try:
            return _transfer.object_exists(http, ep, bucket, path)
        except _transfer.SessionExpired:
            raise
        except (_transfer.TransferError, httpx.HTTPError):
            if attempt == 2:
                return None
    return None


def _settle(http, ep, stage: Path, staged: _object.Staged, name: str, already: bool) -> None:
    """Report only what storage confirms, and leave behind only what a rerun needs."""
    bucket, path = _object.BUCKET, _object.object_path(staged.fingerprint)
    stored = _stored(http, ep, bucket, path)
    if stored:
        _object.clear(stage, staged.fingerprint)
        if already:
            click.echo(f"Already uploaded: {bucket}/{path}")
        else:
            click.echo(
                f"Uploaded {name}\n  fingerprint  {staged.fingerprint}\n"
                f"  stored at    {bucket}/{path}"
            )
        return
    # The server's upload cannot be finished, but the gzipped form can start a new one.
    _object.forget_upload(stage, staged.fingerprint)
    if stored is False:
        raise click.ClickException(
            f"storage took every byte of {name} but has not stored it. The prepared copy is "
            f"kept in {stage}; run the same command again to send it as a new upload."
        )
    raise click.ClickException(
        f"{name} may be stored — storage could not be asked. The prepared copy is kept in "
        f"{stage}; run the same command again to find out."
    )


def _send(http, ep, stage: Path, staged: _object.Staged, name: str) -> None:
    bucket, path = _object.BUCKET, _object.object_path(staged.fingerprint)
    held = 0  # bytes storage holds for this upload

    def acknowledged(offset: int, _size: int) -> None:
        nonlocal held
        held = offset

    try:
        if _transfer.object_exists(http, ep, bucket, path):
            raise _transfer.AlreadyStored(path)
        # Only an upload for these bytes, on this server, is worth resuming.
        saved = _object.load_upload(
            stage, staged.fingerprint, api_url=ep.api_url, size=staged.size
        )
        url = _transfer.resumable_url(ep, saved["id"]) if saved else None
        offset = _transfer.upload_offset(http, ep, url) if url else None
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
        # A name that is the content's fingerprint is taken only by this same content --
        # but say so only once storage shows it.
        _settle(http, ep, stage, staged, name, already=True)
        return
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
    _settle(http, ep, stage, staged, name, already=False)
