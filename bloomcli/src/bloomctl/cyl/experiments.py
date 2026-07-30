"""`bloomctl cyl experiments` — cylinder experiment commands (list)."""

from __future__ import annotations

from typing import Any

import click

from ..credentials import DEFAULT_PROFILE
from ._output import MACHINE_FORMATS, print_table, render

# Table columns for `experiments list`, in display order.
EXPERIMENT_COLUMNS = ["Species", "Experiment", "Experiment ID"]
# Record fields for machine formats (json/csv/tsv) — must match build_experiment_record.
RECORD_FIELDS = ["species", "experiment", "experiment_id"]


@click.group(name="experiments")
def experiments() -> None:
    """Cylinder experiment commands."""


def experiment_sort_key(exp: dict[str, Any]) -> tuple[str, str, int]:
    """Sort by species common name, then experiment name, then id (id breaks ties so
    output is deterministic run-to-run)."""
    species = (exp.get("species") or {}).get("common_name") or ""
    return (species, exp.get("name") or "", exp.get("id") or 0)


def build_experiment_row(exp: dict[str, Any]) -> list[str]:
    """Shape a cyl_experiments row (with joined species) into a display row."""
    species = (exp.get("species") or {}).get("common_name") or ""
    eid = exp.get("id")
    return [species, exp.get("name") or "", "" if eid is None else str(eid)]


def build_experiment_record(exp: dict[str, Any]) -> dict[str, Any]:
    """Machine-readable experiment record (mirrors the table columns)."""
    return {
        "species": (exp.get("species") or {}).get("common_name"),
        "experiment": exp.get("name"),
        "experiment_id": exp.get("id"),
    }


# Default ceiling on how many experiments to fetch — an explicit cap so the query is
# never unbounded (cyl experiments number in the dozens; this is headroom, not a real cut).
DEFAULT_LIMIT = 1000


# --- supabase I/O ---


def fetch_species_with_experiments(client: Any) -> list[tuple[int, str]]:
    """Distinct (species_id, common_name) for species with >=1 non-deleted experiment.

    Sourced from the experiments themselves (joined to species) so the selector menu only
    offers species that actually have experiments — no dead choices. De-duplicated and
    sorted by common name for a stable menu.
    """
    rows = (
        client.table("cyl_experiments")
        .select("species_id, species(common_name)")
        .is_("deleted_at", "null")
        .execute()
        .data
        or []
    )
    by_id: dict[int, str] = {}
    for row in rows:
        sid = row.get("species_id")
        name = (row.get("species") or {}).get("common_name")
        if sid is not None and name and sid not in by_id:
            by_id[sid] = name
    return sorted(by_id.items(), key=lambda kv: kv[1])


def select_species_interactively(species: list[tuple[int, str]]) -> int | None:
    """Prompt with a numbered menu (0 = All species) and return the chosen species_id, or None.

    The menu and prompt are written to stderr so machine-format output on stdout stays clean.
    ``click.prompt`` validates the number and re-prompts on a bad entry; with no input to read
    (non-interactive) it aborts rather than returning a wrong choice.
    """
    click.echo("Select a species:", err=True)
    click.echo("  0) All species", err=True)
    for i, (_sid, name) in enumerate(species, start=1):
        click.echo(f"  {i}) {name}", err=True)
    choice = click.prompt("Species", type=click.IntRange(0, len(species)), err=True)
    return None if choice == 0 else species[choice - 1][0]


def fetch_experiments(
    client: Any, *, species_id: int | None = None, limit: int = DEFAULT_LIMIT
) -> list[dict[str, Any]]:
    """Live cyl experiments (soft-deleted excluded) with their joined species relation.

    Filters ``deleted_at IS NULL`` server-side rather than relying on RLS: only the
    bloom_user policy hides soft-deletes, while bloom_writer/bloom_admin read with
    ``USING (true)`` and would otherwise see tombstoned experiments. ``species_id`` (already
    resolved from a name) narrows to one species; ``limit`` caps the fetch.
    """
    query = client.table("cyl_experiments").select("*, species(*)").is_("deleted_at", "null")
    if species_id is not None:
        query = query.eq("species_id", species_id)
    return query.order("id").limit(limit).execute().data or []


@experiments.command(name="list")
@click.option(
    "--species",
    "pick_species",
    is_flag=True,
    help="Pick a species from an interactive menu to filter by (needs a terminal).",
)
@click.option(
    "--output",
    "output_fmt",
    type=click.Choice(MACHINE_FORMATS),
    default=None,
    help="Emit machine-readable output (csv/json) instead of the table.",
)
@click.option(
    "--limit",
    type=click.IntRange(min=1, max=DEFAULT_LIMIT),
    default=DEFAULT_LIMIT,
    show_default=True,
    help=f"Maximum number of experiments to fetch. Capped to {DEFAULT_LIMIT}.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Alias for --output json.",
)
@click.option(
    "-p",
    "--profile",
    default=DEFAULT_PROFILE,
    show_default=True,
    help="Credentials profile to use.",
)
def list_experiments(
    pick_species: bool, output_fmt: str | None, limit: int, as_json: bool, profile: str
) -> None:
    """List cylinder experiments. Pass --species to pick a species from a menu; use
    --output csv/json to grab an id for `cyl download --experiment-id`."""
    from postgrest import APIError

    from ..cli import _authed_client

    # --json is an alias for --output json; reject a conflicting pair.
    if as_json:
        if output_fmt not in (None, "json"):
            raise click.UsageError("Use either --json or --output, not both.")
        output_fmt = "json"

    client = _authed_client(profile)
    try:
        species_id = None
        if pick_species:
            choices = fetch_species_with_experiments(client)
            if not choices:
                raise click.ClickException("No species with cylinder experiments found.")
            species_id = select_species_interactively(choices)
        raw = fetch_experiments(client, species_id=species_id, limit=limit)
    except APIError as exc:
        raise click.ClickException(getattr(exc, "message", None) or str(exc)) from exc
    rows_data = sorted(raw, key=experiment_sort_key)

    if output_fmt:
        records = [build_experiment_record(e) for e in rows_data]
        click.echo(render(records, RECORD_FIELDS, output_fmt))
        return

    rows = [build_experiment_row(e) for e in rows_data]
    print_table("Experiments", EXPERIMENT_COLUMNS, rows, empty="No experiments found.")
