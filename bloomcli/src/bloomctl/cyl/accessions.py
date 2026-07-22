"""`bloomctl cyl accessions` — cylinder accession commands (list, sample-counts).

Both read server-side views (see the `cyl_experiment_accessions` /
`cyl_accession_sample_counts` migration), which do the DISTINCT / GROUP BY server-side
so the CLI reads a small result and never hits the PostgREST row cap.
"""

from __future__ import annotations

import json
from typing import Any

import click

from ..credentials import DEFAULT_PROFILE
from ._output import print_table

ACCESSION_COLUMNS = ["Accession", "Accession ID"]
SAMPLE_COUNT_COLUMNS = ["Species", "Accession", "Plants"]


@click.group(name="accessions")
def accessions() -> None:
    """Cylinder accession commands."""


def accession_sort_key(rec: dict[str, Any]) -> str:
    """Sort accessions by name."""
    return rec.get("accession_name") or ""


def build_accession_row(rec: dict[str, Any]) -> list[str]:
    """Shape a cyl_experiment_accessions row into a display row."""
    aid = rec.get("accession_id")
    return [rec.get("accession_name") or "", "" if aid is None else str(aid)]


def build_accession_record(rec: dict[str, Any]) -> dict[str, Any]:
    """Machine-readable accession record."""
    return {"accession_id": rec.get("accession_id"), "accession_name": rec.get("accession_name")}


def sample_count_sort_key(rec: dict[str, Any]) -> tuple[str, str]:
    """Sort sample counts by species, then accession name."""
    return (rec.get("species_name") or "", rec.get("accession_name") or "")


def build_sample_count_row(rec: dict[str, Any]) -> list[str]:
    """Shape a cyl_accession_sample_counts row into a display row."""
    return [
        rec.get("species_name") or "",
        rec.get("accession_name") or "",
        "" if rec.get("plant_count") is None else str(rec.get("plant_count")),
    ]


def build_sample_count_record(rec: dict[str, Any]) -> dict[str, Any]:
    """Machine-readable sample-count record."""
    return {
        "species": rec.get("species_name"),
        "accession": rec.get("accession_name"),
        "plant_count": rec.get("plant_count"),
    }


# --- supabase / storage I/O ---


def fetch_experiment_accessions(client: Any, experiment_id: int) -> list[dict[str, Any]]:
    """Distinct accessions used in one experiment, via the cyl_experiment_accessions view."""
    return (
        client.table("cyl_experiment_accessions")
        .select("accession_id, accession_name")
        .eq("experiment_id", experiment_id)
        .execute()
        .data
        or []
    )


def fetch_accession_sample_counts(client: Any, species: str | None = None) -> list[dict[str, Any]]:
    """Sample count per accession per species, via the cyl_accession_sample_counts view."""
    query = client.table("cyl_accession_sample_counts").select(
        "species_name, accession_id, accession_name, plant_count"
    )
    if species:
        query = query.eq("species_name", species)
    return query.execute().data or []


@accessions.command(name="list")
@click.option(
    "--experiment-id",
    "--experiment_id",
    type=int,
    required=True,
    help="List the accessions used in this experiment (id).",
)
@click.option("--json", "as_json", is_flag=True, help="Emit accessions as a JSON array.")
@click.option(
    "-p",
    "--profile",
    default=DEFAULT_PROFILE,
    show_default=True,
    help="Credentials profile to use.",
)
def list_accessions(experiment_id: int, as_json: bool, profile: str) -> None:
    """List the accessions used in a cylinder experiment."""
    from ..cli import _authed_client
    from postgrest import APIError

    client = _authed_client(profile)
    try:
        raw = fetch_experiment_accessions(client, experiment_id)
    except APIError as exc:
        raise click.ClickException(getattr(exc, "message", str(exc))) from exc
    data = sorted(raw, key=accession_sort_key)

    if as_json:
        click.echo(json.dumps([build_accession_record(r) for r in data]))
        return

    rows = [build_accession_row(r) for r in data]
    print_table("Accessions", ACCESSION_COLUMNS, rows, empty="No accessions found for that experiment.")


@accessions.command(name="sample-counts")
@click.option("--species", default=None, help="Filter to one species (common name).")
@click.option("--json", "as_json", is_flag=True, help="Emit counts as a JSON array.")
@click.option(
    "-p",
    "--profile",
    default=DEFAULT_PROFILE,
    show_default=True,
    help="Credentials profile to use.",
)
def sample_counts(species: str | None, as_json: bool, profile: str) -> None:
    """Show the plant count per accession, per species (one plant = one biological replicate)."""
    from ..cli import _authed_client
    from postgrest import APIError

    client = _authed_client(profile)
    try:
        raw = fetch_accession_sample_counts(client, species)
    except APIError as exc:
        raise click.ClickException(getattr(exc, "message", str(exc))) from exc
    data = sorted(raw, key=sample_count_sort_key)

    if as_json:
        click.echo(json.dumps([build_sample_count_record(r) for r in data]))
        return

    rows = [build_sample_count_row(r) for r in data]
    print_table("Sample counts per accession", SAMPLE_COUNT_COLUMNS, rows, empty="No sample counts found.")
