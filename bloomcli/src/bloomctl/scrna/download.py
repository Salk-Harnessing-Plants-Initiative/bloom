"""`bloomctl scrna download`: fetch a dataset's h5ad, decompressed and checked against its fingerprint."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

import click
import httpx

from ..credentials import DEFAULT_PROFILE
from . import _object, _session, _transfer


@click.command(name="download")
@click.argument("dataset", required=False)
@click.option(
    "--checksum",
    "fingerprint",
    default=None,
    help="Download the object with this SHA-256 instead of naming a dataset.",
)
@click.option(
    "--out",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Where to write the file (default: the dataset's name, as .h5ad).",
)
@click.option(
    "-p",
    "--profile",
    default=DEFAULT_PROFILE,
    show_default=True,
    help="Credentials profile to use.",
)
def download(dataset: str | None, fingerprint: str | None, out: Path | None, profile: str) -> None:
    """Download a dataset's AnnData file (.h5ad), checked against its recorded SHA-256.

    DATASET is a dataset's name or id. The file is written only once its fingerprint matches.
    """
    if bool(dataset) == bool(fingerprint):
        raise click.UsageError("Name a DATASET, or pass --checksum; one of the two.")
    conn = _session.connect(profile)
    if dataset:
        row = _resolve(conn.client, dataset)
        label = f"Dataset {row['name']!r} (id {row['id']})"
        fingerprint = row.get("source_checksum")
        if not fingerprint:
            raise click.ClickException(
                f"{label} records no source file fingerprint, so there is no file to download."
            )
        default_name = f"{_file_name(row['name'])}.h5ad"
    else:
        label = f"The object {fingerprint}"
        default_name = f"{fingerprint}.h5ad"
    try:
        path = _object.object_path(fingerprint)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    dest = out or Path(default_name)
    if dest.exists():
        if _object.fingerprint_of(dest) == fingerprint:
            click.echo(f"{dest} is already this file (fingerprint {fingerprint}).")
            return
        raise click.ClickException(
            f"{dest} already exists and holds a different file; pass --out to write elsewhere."
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.{uuid4().hex}.tmp")
    try:
        with _transfer.open_client() as http:
            got = _transfer.download_to(http, conn.endpoint, _object.BUCKET, path, tmp)
        if got != fingerprint:
            raise click.ClickException(
                f"the downloaded file's fingerprint {got} does not match {fingerprint}; "
                "nothing was saved."
            )
        os.replace(tmp, dest)
    except _transfer.NotStored as exc:
        raise click.ClickException(
            f"{label} has no stored file yet: expected {_object.BUCKET}/{path}."
        ) from exc
    except (_transfer.TransferError, httpx.HTTPError) as exc:
        raise click.ClickException(
            f"the download stopped: {str(exc) or type(exc).__name__}. Nothing was saved; run it "
            "again."
        ) from exc
    finally:
        tmp.unlink(missing_ok=True)
    click.echo(f"Downloaded {dest}\n  fingerprint  {fingerprint}")


def _resolve(client: Any, value: str) -> dict[str, Any]:
    """The one dataset ``value`` names, by id or by name."""
    from .._postgrest import queried

    def fetch(column: str, wanted: Any) -> list[dict[str, Any]]:
        return queried(
            "datasets",
            lambda: client.table("scrna_datasets")
            .select("id, name, source_checksum")
            .eq(column, wanted)
            .is_("deleted_at", "null")
            .execute()
            .data,
        ) or []

    rows = fetch("id", int(value)) if value.isdigit() else []
    if not rows:
        rows = fetch("name", value)
    if not rows:
        raise click.ClickException(f"No dataset is named or numbered {value!r}.")
    if len(rows) > 1:
        ids = ", ".join(str(r["id"]) for r in rows)
        raise click.ClickException(
            f"More than one dataset is named {value!r} (ids {ids}); pass the id instead."
        )
    return rows[0]


def _file_name(name: str) -> str:
    """A dataset name made safe as a file name: spaces to underscores, no path characters."""
    return re.sub(r'[<>:"/\\|?*]', "", re.sub(r"\s+", "_", name.strip())) or "dataset"
