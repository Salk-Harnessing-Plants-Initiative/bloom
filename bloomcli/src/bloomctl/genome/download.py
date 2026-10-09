"""`bloomctl genome download`: a genome version's FASTA and GTF, checked against Bloom's record.

Each file is streamed to a hidden name, its SHA-256 and size are compared with what Bloom stored
when it was uploaded, and only then is it moved into place. With --unzip the checked file is
unzipped the same way. A file already in place with the right content is kept, so a retried
workflow step fetches only what it is missing; a different file there is never overwritten.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import zlib
from pathlib import Path
from typing import Any
from uuid import uuid4

import click
import httpx

from ..credentials import DEFAULT_PROFILE
from ..scrna import _session, _transfer
from ..scrna._text import visible
from . import _files
from .upload import BUCKET

# NAME, or NAME.vN for one version.
NAMED = re.compile(r"^(?P<name>.+?)(?:\.v(?P<version>[1-9][0-9]*))?$")
# Each file: its columns in genome_reference_versions, and its name unzipped.
FILES = (("fasta", "genome.fa"), ("gtf", "genes.gtf"))
VERSION_COLUMNS = ("id, version, status, withdrawn_reason, fasta_path, fasta_sha256, "
                   "fasta_bytes, gtf_path, gtf_sha256, gtf_bytes")
READY = "ready"


@click.command(name="download")
@click.argument("genome")
@click.option("--to", "to", type=click.Path(file_okay=False, path_type=Path), default=Path("."),
              show_default=True, help="The folder to write the files to.")
@click.option("--unzip", is_flag=True,
              help="Write genome.fa and genes.gtf, unzipped once checked, instead of the .gz files.")
@click.option("--version-file", type=click.Path(dir_okay=False, path_type=Path),
              help="Also write the exact version fetched (NAME.vN) to this file.")
@click.option("-p", "--profile", default=DEFAULT_PROFILE, show_default=True,
              help="Credentials profile to use.")
def download(genome: str, to: Path, unzip: bool, version_file: Path | None, profile: str) -> None:
    """Download a genome version's FASTA and GTF, checked against Bloom's record.

    GENOME is a genome's name, for its newest ready version, or NAME.vN for version N. Only a
    ready version (its upload finished) is downloaded. The files are genome.fa.gz and
    genes.gtf.gz, or genome.fa and genes.gtf with --unzip.
    """
    named = NAMED.fullmatch(genome)
    if named is None or not _files.NAME_RULE.match(named["name"]):
        raise click.UsageError(f"GENOME must be a name ({_files.NAME_HELP}), optionally with .vN.")
    name = named["name"]
    conn = _session.connect(profile)
    version = _version(conn.client, name, named["version"])
    label = f"{name} v{version['version']}"
    try:
        to.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise click.ClickException(f"cannot write to {to}: {exc.strerror or exc}.") from exc
    with _transfer.open_client() as http:
        for kind, plain in FILES:
            conn = _fetch(http, conn, profile, label, version, kind, plain, to, unzip)
    click.echo(label)
    if version_file is not None:
        version_file.parent.mkdir(parents=True, exist_ok=True)
        version_file.write_text(f"{name}.v{version['version']}\n")


def _version(client: Any, name: str, wanted: str | None) -> dict[str, Any]:
    """The version to download: the one named, or the newest ready one; refuse any other."""
    from .._postgrest import queried

    genomes = queried(
        "the genome",
        lambda: client.table("genome_references").select("id, name").eq("name", name)
        .execute().data,
    ) or []
    if not genomes:
        raise click.ClickException(
            f"No genome is named {name!r}; `bloomctl genome list` shows what Bloom holds."
        )
    versions = queried(
        "the genome's versions",
        lambda: client.table("genome_reference_versions").select(VERSION_COLUMNS)
        .eq("genome_id", genomes[0]["id"]).execute().data,
    ) or []
    if wanted is None:
        ready = [v for v in versions if v["status"] == READY]
        if not ready:
            raise click.ClickException(
                f"{name} has no ready version yet; `bloomctl genome list` shows its versions."
            )
        return max(ready, key=lambda v: v["version"])
    version = next((v for v in versions if v["version"] == int(wanted)), None)
    if version is None:
        raise click.ClickException(f"{name} has no v{wanted}.")
    if version["status"] != READY:
        raise click.ClickException(f"{_not_ready(name, version)}; only a ready version is "
                                   "downloaded. Nothing was written.")
    return version


def _not_ready(name: str, version: dict[str, Any]) -> str:
    label = f"{name} v{version['version']}"
    if version["status"] == "withdrawn":
        return f"{label} was withdrawn: {version.get('withdrawn_reason') or 'no reason given'}"
    if version["status"] == "abandoned":
        return f"{label}'s upload failed"
    return f"{label} is still uploading"


def _fetch(http, conn, profile: str, label: str, version: dict[str, Any], kind: str, plain: str,
           to: Path, unzip: bool):
    """Put one checked file in place, or keep the right one already there. Returns the session,
    which a long download may have renewed."""
    stored = f"{plain}.gz"
    final = to / (plain if unzip else stored)
    expected = (version[f"{kind}_sha256"], version[f"{kind}_bytes"])
    if final.exists():
        if _already(final, unzip, expected):
            click.echo(f"{final} is already {label}'s {stored}; kept.", err=True)
            return conn
        raise click.ClickException(
            f"{final} already exists and holds a different file; nothing was overwritten."
        )
    tmp = to / f".{stored}.{uuid4().hex}.tmp"
    try:
        conn, got = _fetch_through_expiry(http, conn, profile, version[f"{kind}_path"], tmp)
        if got != expected:
            raise click.ClickException(
                f"{label}'s {stored} doesn't match Bloom's record (SHA-256 {got[0]}, {got[1]} "
                f"bytes; expected {expected[0]}, {expected[1]} bytes). Nothing was saved."
            )
        if unzip:
            _unzip_into_place(tmp, final, expected[0])
        else:
            os.replace(tmp, final)
    except _transfer.NotStored as exc:
        raise click.ClickException(
            f"{label}'s {stored} isn't stored at {BUCKET}/{version[f'{kind}_path']}."
        ) from exc
    except _transfer.SessionExpired as exc:
        raise click.ClickException(f"{exc} Nothing was saved.") from exc
    except (_transfer.TransferError, httpx.HTTPError) as exc:
        raise click.ClickException(
            f"the download stopped: {visible(str(exc) or type(exc).__name__)}. Nothing was saved; "
            "run it again."
        ) from exc
    except OSError as exc:
        raise click.ClickException(
            f"could not write {final}: {exc.strerror or exc}. Nothing was saved."
        ) from exc
    finally:
        tmp.unlink(missing_ok=True)
    click.echo(f"Downloaded {final}", err=True)
    return conn


def _fetch_through_expiry(http, conn, profile: str, path: str, tmp: Path):
    """Fetch the object as stored, signing in again once if the session expires part-way."""
    try:
        return conn, _transfer.download_raw_to(http, conn.endpoint, BUCKET, path, tmp)
    except _transfer.SessionExpired:
        conn = _session.connect(profile)
    return conn, _transfer.download_raw_to(http, conn.endpoint, BUCKET, path, tmp)


def _record_of(final: Path) -> Path:
    """Beside an unzipped file: which checked file it came from, and its own SHA-256."""
    return final.with_name(f".{final.name}.from")


def _already(final: Path, unzip: bool, expected: tuple[str, int]) -> bool:
    """Whether the file in place is this version's: the stored bytes themselves, or, unzipped,
    the file this command made from them."""
    if not unzip:
        return final.stat().st_size == expected[1] and _files.sha256_of(final) == expected[0]
    try:
        record = json.loads(_record_of(final).read_text())
    except (OSError, ValueError):
        return False
    return (record.get("source_sha256") == expected[0]
            and record.get("sha256") == _files.sha256_of(final))


def _unzip_into_place(checked: Path, final: Path, source_sha256: str) -> None:
    """Unzip a checked file through a hidden name, record where it came from, then move it in."""
    out = final.with_name(f".{final.name}.{uuid4().hex}.tmp")
    try:
        digest = hashlib.sha256()
        try:
            with gzip.open(checked, "rb") as src, out.open("wb") as dst:
                for block in iter(lambda: src.read(_files.READ_BYTES), b""):
                    digest.update(block)
                    dst.write(block)
        except (EOFError, zlib.error, gzip.BadGzipFile) as exc:
            raise click.ClickException(
                f"{checked.name.split('.tmp')[0].lstrip('.')} is not a whole gzipped file: {exc}. "
                "Nothing was saved."
            ) from exc
        _record_of(final).write_text(
            json.dumps({"source_sha256": source_sha256, "sha256": digest.hexdigest()}) + "\n"
        )
        os.replace(out, final)
    finally:
        out.unlink(missing_ok=True)
