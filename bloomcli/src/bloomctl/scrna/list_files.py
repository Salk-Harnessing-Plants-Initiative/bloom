"""`bloomctl scrna hdf5 list`: what dataset files storage holds, and which dataset each belongs to."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import click
import httpx

from .._output import MACHINE_FORMATS, print_table, render, resolve_output_format
from ..credentials import DEFAULT_PROFILE
from . import _object, _session, _transfer

# Columns for the table, in display order.
COLUMNS = ["Dataset", "Fingerprint", "Size", "Bytes", "Uploaded"]
# How much of the fingerprint the table shows. A full SHA-256 folds over five lines in an
# ordinary terminal; 12 hex characters identify an object and `--output json` carries all 64.
FINGERPRINT_SHOWN = 12
# Fields for --output json/csv. Machine output carries exact bytes, not the rounded size.
RECORD_FIELDS = ["dataset", "dataset_id", "fingerprint", "bytes", "uploaded", "path"]

# A ceiling on how many objects to fetch, so the listing is never unbounded.
DEFAULT_LIMIT = 1000


@click.command(name="list")
@click.argument("search", required=False)
@click.option(
    "--file",
    "local_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Show whether this local .h5ad is already stored, by its fingerprint.",
)
@click.option(
    "--limit",
    type=click.IntRange(min=1),
    default=DEFAULT_LIMIT,
    show_default=True,
    help="Most objects to list.",
)
@click.option("--output", "output_fmt", type=click.Choice(MACHINE_FORMATS), default=None,
              help="Print machine-readable output instead of a table.")
@click.option("--json", "as_json", is_flag=True, help="Alias for --output json.")
@click.option(
    "-p",
    "--profile",
    default=DEFAULT_PROFILE,
    show_default=True,
    help="Credentials profile to use.",
)
def list_files(
    search: str | None,
    local_file: Path | None,
    limit: int,
    output_fmt: str | None,
    as_json: bool,
    profile: str,
) -> None:
    """List the dataset files (.h5ad) stored in the scrna bucket.

    SEARCH keeps only the entries whose fingerprint or dataset name contains it. The table
    shortens each fingerprint; --output json carries all 64 characters.
    """
    fmt = resolve_output_format(output_fmt, as_json)
    if local_file and search:
        raise click.UsageError("Pass a SEARCH or --file, not both.")
    conn = _session.connect(profile)
    with _transfer.open_client() as http:
        if local_file:
            # One name, so ask storage about that name rather than reading the folder: the
            # answer is exact however many objects the bucket holds.
            fingerprint = _object.fingerprint_of(local_file)
            entries = _named(http, conn.endpoint, fingerprint)
            if not entries:
                raise click.ClickException(
                    f"{local_file.name} is not stored. Its fingerprint is {fingerprint}, and "
                    f"storage holds no object under that name — run `bloomctl scrna hdf5 "
                    f"upload` to send it."
                )
        else:
            entries = _fetch(http, conn.endpoint, limit)
    records = _annotate(conn.client, entries)
    if search:
        wanted = search.lower()
        records = [r for r in records if wanted in _searchable(r)]
    if fmt:
        click.echo(render(records, RECORD_FIELDS, fmt))
        return
    print_table(
        f"Stored dataset files ({len(records)})",
        COLUMNS,
        [_row(r) for r in records],
        empty=_nothing_found(search),
    )


def _searchable(record: dict[str, Any]) -> str:
    """The text a SEARCH is matched against: the fingerprint and the dataset's name."""
    return f"{record['fingerprint']} {record['dataset'] or ''}".lower()


def _nothing_found(search: str | None) -> str:
    if search:
        return f"No stored dataset file matches {search!r}."
    return "Storage holds no dataset files."


def _named(http: httpx.Client, endpoint: Any, fingerprint: str) -> list[dict[str, Any]]:
    """The one object stored under this fingerprint, or nothing, in a single request."""
    name = f"{fingerprint}{_object.SUFFIX}"
    found = _asked(
        lambda: _transfer.list_objects(
            http, endpoint, _object.BUCKET, _object.FOLDER, search=name
        )
    )
    return [entry for entry in found if entry.get("name") == name]


def _asked(call: Callable[[], list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Put a question to storage, turning a refusal into a sentence rather than a traceback."""
    try:
        return call()
    except (_transfer.SessionExpired, _transfer.Forbidden) as exc:
        raise click.ClickException(str(exc)) from exc
    except (_transfer.TransferError, httpx.HTTPError) as exc:
        reason = str(exc) or type(exc).__name__
        raise click.ClickException(f"storage could not be asked what it holds: {reason}") from exc


def _fetch(http: httpx.Client, endpoint: Any, limit: int) -> list[dict[str, Any]]:
    """Every object under the bucket's h5ad folder, a page at a time, up to `limit`."""
    entries: list[dict[str, Any]] = []
    while len(entries) < limit:
        page = _asked(
            lambda: _transfer.list_objects(
                http,
                endpoint,
                _object.BUCKET,
                _object.FOLDER,
                limit=min(_transfer.LIST_PAGE, limit - len(entries)),
                offset=len(entries),
            )
        )
        entries.extend(page)
        if len(page) < _transfer.LIST_PAGE:
            break
    return entries


def _annotate(client: Any, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn listing entries into records, naming the dataset each object belongs to."""
    records = []
    for entry in entries:
        name = entry.get("name") or ""
        if not name.endswith(_object.SUFFIX):
            continue
        records.append({
            "dataset": None,
            "dataset_id": None,
            "fingerprint": name[: -len(_object.SUFFIX)],
            "bytes": _transfer.object_size(entry),
            "uploaded": _when(entry),
            "path": f"{_object.BUCKET}/{_object.FOLDER}/{name}",
        })
    named = _datasets_by_checksum(client, [r["fingerprint"] for r in records])
    for record in records:
        row = named.get(record["fingerprint"])
        if row:
            record["dataset"], record["dataset_id"] = row.get("name"), row.get("id")
    records.sort(key=lambda r: (r["uploaded"] or "", r["fingerprint"]), reverse=True)
    return records


def _when(entry: dict[str, Any]) -> str:
    """When the object arrived, to the minute; storage reports it in ISO-8601."""
    stamp = entry.get("created_at") or entry.get("updated_at") or ""
    return stamp.replace("T", " ")[:16] if isinstance(stamp, str) else ""


def _datasets_by_checksum(client: Any, fingerprints: list[str]) -> dict[str, dict[str, Any]]:
    """The dataset row recorded against each fingerprint, for those that have one.

    An object can be stored before any dataset row points at it, so a fingerprint with no row
    is expected, not an error. Batched by ``fetch_in_batches`` because the `in.(…)` filter
    travels in the URL and a SHA-256 is 64 characters of it.
    """
    from .._postgrest import fetch_in_batches, queried

    if not fingerprints:
        return {}
    rows = queried(
        "datasets",
        lambda: fetch_in_batches(
            lambda batch: client.table("scrna_datasets")
            .select("id, name, source_checksum")
            .in_("source_checksum", batch)
            .is_("deleted_at", "null"),
            fingerprints,
        ),
    ) or []
    found: dict[str, dict[str, Any]] = {}
    for row in rows:
        found.setdefault(row.get("source_checksum") or "", row)
    return found


def _row(record: dict[str, Any]) -> list[str]:
    return [
        record["dataset"] or "—",
        record["fingerprint"][:FINGERPRINT_SHOWN] + "…",
        _human(record["bytes"]),
        f"{record['bytes']:,}",
        record["uploaded"],
    ]


def _human(count: int) -> str:
    """A byte count at a glance; the exact number has its own column."""
    size = float(count)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:,.0f} {unit}" if unit == "B" else f"{size:,.1f} {unit}"
        size /= 1024
    return f"{size:,.1f} GB"
