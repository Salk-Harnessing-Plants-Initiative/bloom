"""`bloomctl scrna hdf5 upload`: put a dataset's h5ad into storage, gzipped, under its fingerprint."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

import click
import httpx

from ..credentials import DEFAULT_PROFILE
from . import _format, _object, _session, _summary, _transfer

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
@click.option("--umap-key", help="The obsm array holding the UMAP, when it is not X_umap.")
@click.option(
    "--no-umap",
    is_flag=True,
    help="Upload a file whose obsm holds a two-column array that is not a UMAP.",
)
@click.option(
    "-y",
    "--yes",
    is_flag=True,
    help="Upload without asking; needed when there is no terminal (scripts, CI).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Sign in and run every check, then stop before sending anything.",
)
def upload(
    file: Path, profile: str, umap_key: str | None, no_umap: bool, yes: bool, dry_run: bool
) -> None:
    """Upload a dataset's AnnData file (.h5ad), gzipped and named by its SHA-256.

    Needs a writer or admin login. Every check runs first; then what the file holds is shown
    on the terminal and the upload goes ahead once confirmed. An interrupted upload resumes
    when the same command is run again.
    """
    if umap_key is not None and no_umap:
        raise click.UsageError("--umap-key and --no-umap cannot both be given.")
    if not dry_run and not yes and not _interactive():
        raise click.ClickException(
            "there is no terminal to ask for confirmation in. Pass --yes to upload without "
            "asking, or --dry-run to run the checks. Nothing was sent."
        )
    conn = _session.connect(profile)
    if conn.role not in _session.WRITE_ROLES:
        raise click.ClickException(
            f"this login signs in as {conn.role or 'no role'}; uploading needs bloom_writer or "
            "bloom_admin. Nothing was read."
        )
    summary = _checked(file, umap_key, no_umap)

    stage = _object.staging_dir()
    staged = _object.stage(file, stage)
    try:
        recorded_on = None
        if summary.normalization is None:
            recorded_on = _normalization_on_record(conn.client, staged.fingerprint, summary.layers)
            if recorded_on is None:
                raise click.ClickException(
                    f"{file.name} has no uns['normalization'] saying how X was made. Add one "
                    "with transform (log1p, log2p or none), scaling (library_size, none or "
                    "other), and target_sum for library_size or a description for other."
                )
        if staged.size > _object.MAX_OBJECT_BYTES:
            raise click.ClickException(
                f"{file.name} gzips to {staged.size / MB:,.0f} MB; the limit is "
                f"{_object.MAX_OBJECT_BYTES // MB} MB. Nothing was sent."
            )
    except click.ClickException:
        _release(stage, staged)
        raise

    _show(summary, file.name, recorded_on)
    if dry_run:
        _release(stage, staged)
        click.echo("Dry run — every check passed. Nothing was sent.")
        return
    if not yes and not click.confirm("Upload this file?", default=False, err=True):
        _release(stage, staged)
        click.echo("Nothing was sent.", err=True)
        raise click.exceptions.Exit(1)

    with _transfer.open_client() as http:
        _send_through_expiry(http, conn, profile, stage, staged, file.name)


def _interactive() -> bool:
    return sys.stdin.isatty()


def _checked(file: Path, umap_key: str | None, no_umap: bool) -> _format.Summary:
    try:
        return _format.check_structure(file, umap_key=umap_key, no_umap=no_umap)
    except _format.MissingDependency as exc:
        raise click.ClickException(str(exc)) from exc
    except _format.FormatError as exc:
        raise click.ClickException(f"{file.name} does not meet Bloom's h5ad format: {exc}") from exc


def _release(stage: Path, staged: _object.Staged) -> None:
    """Drop the gzipped form, unless an earlier upload of it is waiting to be resumed."""
    if not _object.upload_recorded(stage, staged.fingerprint):
        _object.clear(stage, staged.fingerprint)


def _show(summary: _format.Summary, name: str, recorded_on: dict[str, Any] | None) -> None:
    """The summary goes to the terminal, beside the question, even when output is redirected."""
    for line in _summary.describe(summary, name, recorded_on=recorded_on):
        click.echo(line, err=True)


def _send_through_expiry(http, conn, profile: str, stage, staged, name: str) -> None:
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


def _normalization_on_record(client: Any, fingerprint: str, layers) -> dict[str, Any] | None:
    """The dataset loaded from this exact file that records how its X was made, if any."""
    from .._postgrest import queried

    rows = queried(
        "the datasets loaded from this file",
        lambda: client.table("scrna_datasets")
        .select("id, name, metadata")
        .eq("source_checksum", fingerprint)
        .is_("deleted_at", "null")
        .execute()
        .data,
    ) or []
    for row in rows:
        block = (row.get("metadata") or {}).get("normalization")
        if isinstance(block, dict) and _format.normalization_problem(block, layers=layers) is None:
            return row
    return None


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
