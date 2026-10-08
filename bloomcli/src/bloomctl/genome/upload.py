"""`bloomctl genome upload`: store a genome's FASTA and GTF in Bloom as its next version.

The files are checked and gzipped before anything is sent. Bloom then numbers the version,
the two files go to the genome-references bucket, and the version is finished, which checks
both are stored and makes it ready for Cell Ranger runs. If anything fails after the version
is numbered, it is abandoned, so it is never offered for a run.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any

import click

from ..credentials import DEFAULT_PROFILE
from ..scrna import _progress, _session, _transfer
from ..scrna._text import visible
from . import _files

BUCKET = "genome-references"
# Only writer logins may start, finish or abandon a version.
UPLOAD_ROLE = "bloom_writer"


@click.command(name="upload")
@click.argument("name")
@click.option("--fasta", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="The genome sequence, plain or gzipped.")
@click.option("--gtf", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="The gene annotation, plain or gzipped.")
@click.option("--species", help="The species' common name, e.g. Arabidopsis. Needed for a new genome.")
@click.option("--description", help="A new genome's description, e.g. Col-0.")
@click.option("--assembly", help="The assembly, e.g. TAIR10.")
@click.option("--annotation", help="The annotation release, e.g. Araport11.")
@click.option("--source-url", help="Where the files were downloaded from.")
@click.option("--notes", help="Anything else worth recording about this version.")
@click.option("-p", "--profile", default=DEFAULT_PROFILE, show_default=True,
              help="Credentials profile to use.")
@click.option("-y", "--yes", is_flag=True, help="Upload without asking for confirmation.")
def upload(name: str, fasta: Path, gtf: Path, profile: str, yes: bool, **opts: Any) -> None:
    """Upload a genome's FASTA and GTF to Bloom as the genome's next version.

    NAME is the genome, e.g. tair10_araport11. A new genome needs --species. Both files are
    checked and gzipped first, the upload is described, and it goes ahead once confirmed.
    """
    if not _files.NAME_RULE.match(name):
        raise click.UsageError(f"NAME must be {_files.NAME_HELP}.")
    _checked(_files.check_fasta, fasta)
    _checked(_files.check_gtf, gtf)
    if not yes and not _interactive():
        raise click.ClickException(
            "there is no terminal to ask for confirmation in. Pass --yes to go ahead without "
            "asking. Nothing was sent."
        )
    conn = _session.connect(profile)
    if conn.role != UPLOAD_ROLE:
        raise click.ClickException(
            f"this login signs in as {conn.role or 'no role'}; uploading a genome needs a "
            f"writer login ({UPLOAD_ROLE}). Nothing was sent."
        )
    genome = _existing_genome(conn.client, name)
    species_id, species = _species(conn.client, genome, opts["species"], name)

    with tempfile.TemporaryDirectory(prefix="bloomctl-genome-") as workdir:
        with _progress.track(f"Preparing {visible(fasta.name)}"):
            fasta_file = _checked(_files.prepare, fasta, Path(workdir), "genome.fa.gz")
        with _progress.track(f"Preparing {visible(gtf.name)}"):
            gtf_file = _checked(_files.prepare, gtf, Path(workdir), "genes.gtf.gz")
        _show(name, genome, species, fasta, fasta_file, gtf, gtf_file, opts)
        if not yes and not click.confirm(_question(name, genome), default=False, err=True):
            click.echo("Nothing was sent.", err=True)
            raise click.exceptions.Exit(1)
        started = _start(conn.client, name, species_id, opts)
        try:
            with _transfer.open_client() as http:
                conn = _send(http, conn, profile, started["fasta_path"], fasta_file, fasta.name)
                conn = _send(http, conn, profile, started["gtf_path"], gtf_file, gtf.name)
            version = _finish(conn.client, started["version_id"], fasta_file, gtf_file)
        except BaseException:
            if _abandon(conn.client, name, started) != "ready":
                raise
            # The finish went through even though its answer was lost.
            version = started["version"]
    click.echo(f"{name} v{version}: ready")


def _interactive() -> bool:
    return sys.stdin.isatty()


def _checked(call, *args):
    """Run a file check or preparation; a refusal comes before anything is sent."""
    try:
        return call(*args)
    except _files.FileProblem as exc:
        raise click.ClickException(f"{visible(str(exc))}. Nothing was sent.") from exc


def _rpc(what: str, call):
    """Call a database function, naming what failed when the server refuses it."""
    from postgrest import APIError

    from ..errors import explain

    try:
        return call().data
    except APIError as exc:
        raise click.ClickException(f"Could not {what}: {explain(exc)}") from exc


def _existing_genome(client: Any, name: str) -> dict | None:
    from .._postgrest import queried

    rows = queried(
        "the genome",
        lambda: client.table("genome_references").select("id, name, species_id")
        .eq("name", name).execute().data,
    ) or []
    return rows[0] if rows else None


def _species(client: Any, genome: dict | None, typed: str | None, name: str) -> tuple[int | None, str]:
    """The species to send, and its name to show; a new genome needs one."""
    from ..scrna._records import species as species_by_name

    if typed is None:
        if genome is None:
            raise click.UsageError(f"{name} is a new genome; give its species with --species.")
        return None, f"species {genome['species_id']}"
    species_id, species = species_by_name(client, typed)
    if genome is not None and genome["species_id"] != species_id:
        raise click.ClickException(
            f"{name} belongs to species {genome['species_id']}, not {visible(species)}. "
            "Nothing was sent."
        )
    return species_id, species


def _show(name, genome, species, fasta, fasta_file, gtf, gtf_file, opts) -> None:
    """The summary goes to the terminal, beside the question, even when output is redirected."""
    lines = [
        f"Genome      {visible(name)} ({'new' if genome is None else 'adds a version'})",
        f"Species     {visible(species)}",
        f"FASTA       {visible(fasta.name)} → {_mb(fasta_file.size)} gzipped, "
        f"sha256 {fasta_file.sha256[:12]}…",
        f"GTF         {visible(gtf.name)} → {_mb(gtf_file.size)} gzipped, "
        f"sha256 {gtf_file.sha256[:12]}…",
    ]
    for label, key in (("Assembly", "assembly"), ("Annotation", "annotation"),
                       ("Source", "source_url"), ("Notes", "notes")):
        if opts[key]:
            lines.append(f"{label:<11} {visible(opts[key])}")
    for line in lines:
        click.echo(line, err=True)


def _mb(size: int) -> str:
    return f"{size / 1024**2:,.1f} MB"


def _question(name: str, genome: dict | None) -> str:
    if genome is None:
        return f"Create genome {visible(name)} and upload its first version?"
    return f"Upload a new version of {visible(name)}?"


def _start(client: Any, name: str, species_id: int | None, opts: dict[str, Any]) -> dict:
    args = {
        "p_genome": name,
        "p_species_id": species_id,
        "p_description": opts["description"],
        "p_assembly": opts["assembly"],
        "p_annotation": opts["annotation"],
        "p_source_url": opts["source_url"],
        "p_notes": opts["notes"],
    }
    rows = _rpc("start the upload", lambda: client.rpc("start_genome_version", args).execute())
    if not rows:
        raise click.ClickException("Bloom did not return the new version. Nothing was sent.")
    return rows[0]


def _send(http, conn, profile: str, path: str, prepared: _files.Prepared, label: str):
    """Send one file; sign in again once if the session runs out part-way. Returns the session."""
    ep = conn.endpoint
    try:
        url = _transfer.create_upload(http, ep, BUCKET, path, prepared.size)
        with _progress.track(f"Uploading {visible(label)}") as update:
            try:
                _transfer.send(http, ep, url, prepared.path, 0, prepared.size, update)
                return conn
            except _transfer.SessionExpired:
                conn = _session.connect(profile)
                ep = conn.endpoint
                offset = _transfer.upload_offset(http, ep, url, prepared.size)
                if offset is None:
                    raise
                _transfer.send(http, ep, url, prepared.path, offset, prepared.size, update)
                return conn
    except _transfer.TransferError as exc:
        raise click.ClickException(f"{visible(label)} could not be uploaded: {exc}") from exc


def _finish(client: Any, version_id: int, fasta_file, gtf_file) -> int:
    args = {
        "p_version_id": version_id,
        "p_fasta_sha256": fasta_file.sha256,
        "p_fasta_bytes": fasta_file.size,
        "p_gtf_sha256": gtf_file.sha256,
        "p_gtf_bytes": gtf_file.size,
    }
    return _rpc("finish the upload", lambda: client.rpc("finish_genome_version", args).execute())


def _abandon(client: Any, name: str, started: dict) -> str | None:
    """Mark the version abandoned so it is never offered for a run, and say what it now is.

    Returns the version's status: "abandoned", or what Bloom holds when abandoning is refused,
    e.g. "ready" when a finish committed but its answer was lost.
    """
    label = f"{visible(name)} v{started['version']}"
    try:
        client.rpc("abandon_genome_version", {"p_version_id": started["version_id"]}).execute()
    except Exception:  # noqa: BLE001 - the original failure is the one to report
        state = _status_of(client, started["version_id"])
        if state != "ready":
            click.echo(
                f"{label} could not be marked abandoned; it is "
                f"{state or 'in a state Bloom could not report'}, and only a ready version is "
                "offered for runs.", err=True,
            )
        return state
    click.echo(
        f"{label} was abandoned; run the same command again to upload it as a new version.",
        err=True,
    )
    return "abandoned"


def _status_of(client: Any, version_id: int) -> str | None:
    """The version's status in Bloom, or None when it cannot be read."""
    try:
        rows = client.table("genome_reference_versions").select("status").eq(
            "id", version_id).execute().data
    except Exception:  # noqa: BLE001 - only used to explain an earlier failure
        return None
    return rows[0]["status"] if rows else None
