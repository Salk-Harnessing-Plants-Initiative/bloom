"""`bloomctl cyl accessions` — cylinder accession commands (list, sample-counts).

Both read server-side views (see the `cyl_experiment_accessions` /
`cyl_accession_sample_counts` migration), which do the DISTINCT / GROUP BY server-side
so the CLI reads a small result and never hits the PostgREST row cap.
"""

from __future__ import annotations

from typing import Any

import click

from .._postgrest import fetch_in_batches
from ..credentials import DEFAULT_PROFILE
from ._output import MACHINE_FORMATS, print_table, render, resolve_output_format
from ._select import resolve_by_name, select_from_menu

ACCESSION_COLUMNS = ["Accession", "Accession ID"]
SAMPLE_COUNT_COLUMNS = ["Species", "Accession", "Plants"]
# Machine-readable field names (json/csv), matching the record builders below.
ACCESSION_FIELDS = ["accession_id", "accession_name"]
SAMPLE_COUNT_FIELDS = ["accession_id", "species", "accession", "plant_count"]


@click.group(name="accessions")
def accessions() -> None:
    """Cylinder accession commands."""


def accession_sort_key(rec: dict[str, Any]) -> tuple[str, int]:
    """Sort accessions by name, then id (id breaks ties so output is
    deterministic run-to-run). A null id sorts as -1 (distinct from a real id 0)."""
    aid = rec.get("accession_id")
    return (rec.get("accession_name") or "", aid if aid is not None else -1)


def build_accession_row(rec: dict[str, Any]) -> list[str]:
    """Shape a cyl_experiment_accessions row into a display row."""
    aid = rec.get("accession_id")
    return [rec.get("accession_name") or "", "" if aid is None else str(aid)]


def build_accession_record(rec: dict[str, Any]) -> dict[str, Any]:
    """Machine-readable accession record."""
    return {"accession_id": rec.get("accession_id"), "accession_name": rec.get("accession_name")}


def sample_count_sort_key(rec: dict[str, Any]) -> tuple[str, str, int]:
    """Sort sample counts by species, then accession name, then id (id breaks
    ties so output is deterministic run-to-run). A null id sorts as -1."""
    aid = rec.get("accession_id")
    return (
        rec.get("species_name") or "",
        rec.get("accession_name") or "",
        aid if aid is not None else -1,
    )


def build_sample_count_row(rec: dict[str, Any]) -> list[str]:
    """Shape a cyl_accession_sample_counts row into a display row."""
    return [
        rec.get("species_name") or "",
        rec.get("accession_name") or "",
        "" if rec.get("plant_count") is None else str(rec.get("plant_count")),
    ]


def build_sample_count_record(rec: dict[str, Any]) -> dict[str, Any]:
    """Machine-readable sample-count record. Includes accession_id (the stable primary
    key) so this output can be joined to `accessions list --json` on the id, without
    depending on the human-editable name."""
    return {
        "accession_id": rec.get("accession_id"),
        "species": rec.get("species_name"),
        "accession": rec.get("accession_name"),
        "plant_count": rec.get("plant_count"),
    }


# --- supabase I/O ---


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


def fetch_species_with_accessions(client: Any) -> list[str]:
    """Distinct species common names that have accessions, for the selector menu.

    Sourced from the sample-counts view so the menu only offers species that actually have
    accessions (no dead choices). De-duplicated, nulls dropped, sorted for a stable menu.
    """
    rows = client.table("cyl_accession_sample_counts").select("species_name").execute().data or []
    return sorted({r["species_name"] for r in rows if r.get("species_name")}, key=str.casefold)


def fetch_experiments_with_accessions(client: Any) -> list[tuple[int, str]]:
    """(experiment_id, "name (species)") for experiments that have accessions, for the menu.

    cyl_experiment_accessions carries only ids, so the human labels are joined from
    cyl_experiments (soft-deleted excluded). Sorted by label for a stable menu.
    """
    rows = client.table("cyl_experiment_accessions").select("experiment_id").execute().data or []
    ids = sorted({r["experiment_id"] for r in rows if r.get("experiment_id") is not None})
    if not ids:
        return []
    exps = fetch_in_batches(
        lambda batch: client.table("cyl_experiments")
        .select("id, name, species(common_name)")
        .in_("id", batch)
        .is_("deleted_at", "null"),
        ids,
    )
    items = [
        (e["id"], f"{e.get('name') or ''} ({(e.get('species') or {}).get('common_name') or '?'})")
        for e in exps
    ]
    # id as a tiebreak so two experiments with the same "name (species)" label order stably.
    return sorted(items, key=lambda it: (it[1].casefold(), it[0]))


@accessions.command(name="list")
@click.option(
    "--experiment-id",
    "--experiment_id",
    "experiment_id",
    type=int,
    default=None,
    help="Accessions used in this experiment (id). Omit to pick from a menu.",
)
@click.option(
    "--output",
    "output_fmt",
    type=click.Choice(MACHINE_FORMATS),
    default=None,
    help="Emit machine-readable output (csv/json) instead of the table.",
)
@click.option("--json", "as_json", is_flag=True, help="Alias for --output json.")
@click.option(
    "-p",
    "--profile",
    default=DEFAULT_PROFILE,
    show_default=True,
    help="Credentials profile to use.",
)
def list_accessions(
    experiment_id: int | None, output_fmt: str | None, as_json: bool, profile: str
) -> None:
    """List the accessions used in a cylinder experiment.

    Pass --experiment-id for a specific experiment (scriptable), or omit it to pick one from a
    menu (needs a terminal).
    """
    from postgrest import APIError

    from ..cli import _authed_client

    output_fmt = resolve_output_format(output_fmt, as_json)  # --json aliases --output json

    client = _authed_client(profile)
    try:
        if experiment_id is None:  # no id → pick an experiment from the menu
            choices = fetch_experiments_with_accessions(client)
            if not choices:
                raise click.ClickException("No experiments with accessions found.")
            experiment_id = select_from_menu(
                choices, title="an experiment", prompt_label="Experiment"
            )
        raw = fetch_experiment_accessions(client, experiment_id)
    except APIError as exc:
        raise click.ClickException(getattr(exc, "message", None) or str(exc)) from exc
    data = sorted(raw, key=accession_sort_key)

    if output_fmt:
        click.echo(render([build_accession_record(r) for r in data], ACCESSION_FIELDS, output_fmt))
        return

    rows = [build_accession_row(r) for r in data]
    print_table(
        "Accessions", ACCESSION_COLUMNS, rows, empty="No accessions found for that experiment."
    )


@accessions.command(name="sample-counts")
@click.option(
    "--species",
    "species_name",
    default=None,
    help="Filter to this species by common name (case-insensitive, scriptable). Omit for all species.",
)
@click.option(
    "--species-menu",
    "--species_menu",
    "pick_species",
    is_flag=True,
    help="Pick the species from an interactive menu instead of typing it (needs a terminal).",
)
@click.option(
    "--output",
    "output_fmt",
    type=click.Choice(MACHINE_FORMATS),
    default=None,
    help="Emit machine-readable output (csv/json) instead of the table.",
)
@click.option("--json", "as_json", is_flag=True, help="Alias for --output json.")
@click.option(
    "-p",
    "--profile",
    default=DEFAULT_PROFILE,
    show_default=True,
    help="Credentials profile to use.",
)
def sample_counts(
    species_name: str | None,
    pick_species: bool,
    output_fmt: str | None,
    as_json: bool,
    profile: str,
) -> None:
    """Show the plant count per accession, per species (one plant = one individual grown).

    Counts are pooled across all experiments in the database (not scoped to one
    experiment, unlike `accessions list`), so this is a total headcount, not a
    per-condition replicate count. An accession grown under more than one species
    appears as multiple rows, so a per-name total must sum across those rows.
    Plants not assigned to an accession are excluded, so summing the counts can be
    lower than the total plant count.

    Filter with --species NAME (scriptable) or --species-menu to pick from a menu;
    omit both for all species.
    """
    from postgrest import APIError

    from ..cli import _authed_client

    if species_name is not None and pick_species:
        raise click.UsageError("Use either --species NAME or --species-menu, not both.")

    output_fmt = resolve_output_format(output_fmt, as_json)  # --json aliases --output json

    client = _authed_client(profile)
    try:
        species = None
        if pick_species:  # menu of species that have accessions (0 = All)
            names = fetch_species_with_accessions(client)
            if not names:
                raise click.ClickException("No species with accessions found.")
            species = select_from_menu(
                [(n, n) for n in names],
                title="a species",
                prompt_label="Species",
                all_label="All species",
            )
        elif species_name is not None:  # typed value → resolve to the stored name (ci + trimmed)
            names = fetch_species_with_accessions(client)
            species = resolve_by_name([(n, n) for n in names], species_name)
            if species is None:
                raise click.ClickException(f"No species named {species_name!r} with accessions.")
        raw = fetch_accession_sample_counts(client, species)
    except APIError as exc:
        raise click.ClickException(getattr(exc, "message", None) or str(exc)) from exc
    data = sorted(raw, key=sample_count_sort_key)

    if output_fmt:
        click.echo(
            render([build_sample_count_record(r) for r in data], SAMPLE_COUNT_FIELDS, output_fmt)
        )
        return

    rows = [build_sample_count_row(r) for r in data]
    empty = (
        f"No sample counts found for species '{species}'." if species else "No sample counts found."
    )
    print_table("Sample counts per accession", SAMPLE_COUNT_COLUMNS, rows, empty=empty)
