"""`bloomctl genome list`: every reference genome, its species and its versions."""

from __future__ import annotations

from typing import Any

import click

from .._output import MACHINE_FORMATS, print_table, render, resolve_output_format
from ..credentials import DEFAULT_PROFILE
from ..scrna import _session

COLUMNS = ["Genome", "Species", "Version", "Status", "Assembly", "Annotation", "FASTA", "GTF",
           "Ready"]
# Fields for --output json/csv, one record per version; machine output carries exact bytes.
RECORD_FIELDS = ["genome", "species", "version", "status", "assembly", "annotation",
                 "fasta_bytes", "gtf_bytes", "ready_at", "withdrawn_reason"]
VERSION_COLUMNS = ("genome_id, version, status, assembly, annotation, fasta_bytes, gtf_bytes, "
                   "ready_at, withdrawn_reason")


@click.command(name="list")
@click.option("--output", "output_fmt", type=click.Choice(MACHINE_FORMATS), default=None,
              help="Print machine-readable output, one record per version, instead of a table.")
@click.option("--json", "as_json", is_flag=True, help="Alias for --output json.")
@click.option("-p", "--profile", default=DEFAULT_PROFILE, show_default=True,
              help="Credentials profile to use.")
def list_genomes(output_fmt: str | None, as_json: bool, profile: str) -> None:
    """List the reference genomes Bloom holds, with each version and its status.

    A version is ready once its upload finished; uploading, abandoned (the upload failed) and
    withdrawn (pulled by an admin) versions are listed too, but never used for a run.
    """
    fmt = resolve_output_format(output_fmt, as_json)
    client = _session.connect(profile).client
    records = _records(client)
    if fmt:
        click.echo(render(records, RECORD_FIELDS, fmt))
        return
    print_table(f"Reference genomes ({len({r['genome'] for r in records})})", COLUMNS,
                [_row(r) for r in records], empty="Bloom holds no reference genomes yet.")


def _records(client: Any) -> list[dict[str, Any]]:
    """One record per version, by genome name then version; a genome without one gets one."""
    from .._postgrest import queried

    def read(what: str, table: str, columns: str) -> list[dict[str, Any]]:
        return queried(what, lambda: client.table(table).select(columns).execute().data) or []

    species = {r["id"]: r["common_name"] for r in read("species", "species", "id, common_name")}
    versions: dict[int, list[dict[str, Any]]] = {}
    for v in read("genome versions", "genome_reference_versions", VERSION_COLUMNS):
        versions.setdefault(v["genome_id"], []).append(v)
    records = []
    for genome in sorted(read("genomes", "genome_references", "id, name, species_id"),
                         key=lambda g: g["name"]):
        base = {"genome": genome["name"], "species": species.get(genome["species_id"])}
        rows = sorted(versions.get(genome["id"], []), key=lambda v: v["version"]) or [{}]
        records += [{**base, **{k: v.get(k) for k in RECORD_FIELDS[2:]}} for v in rows]
    return records


def _mb(size: int | None) -> str:
    return "" if size is None else f"{size / 1_000_000:.1f} MB"


def _row(r: dict[str, Any]) -> list[str]:
    status = r["status"] or "no versions"
    if r["withdrawn_reason"]:
        status = f"{status}: {r['withdrawn_reason']}"
    return [r["genome"], r["species"] or "", "" if r["version"] is None else f"v{r['version']}",
            status, r["assembly"] or "", r["annotation"] or "", _mb(r["fasta_bytes"]),
            _mb(r["gtf_bytes"]), (r["ready_at"] or "")[:10]]
