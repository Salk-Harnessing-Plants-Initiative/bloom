"""`bloomctl plate experiments` — plate (GraviScan) experiment commands (list)."""

from __future__ import annotations

from typing import Any

import click

from .._output import MACHINE_FORMATS, print_table, render, resolve_output_format
from ..credentials import DEFAULT_PROFILE
from ..cyl._select import resolve_by_name, select_from_menu

# Table columns for `experiments list`, in display order.
EXPERIMENT_COLUMNS = ["Species", "Experiment", "Rig", "Experiment ID"]
# Record fields for machine formats (json/csv) — must match build_experiment_record.
RECORD_FIELDS = ["species", "experiment", "rig", "experiment_id"]

# Explicit cap so the query is never unbounded; also the --limit maximum.
DEFAULT_LIMIT = 1000


@click.group(name="experiments")
def experiments() -> None:
    """Plate experiment commands."""


def _species_name(exp: dict[str, Any]) -> str:
    return (exp.get("species") or {}).get("common_name") or ""


def experiment_sort_key(exp: dict[str, Any]) -> tuple[str, str, str, int]:
    """Species, then name, then rig (one name can exist on several rigs), then id."""
    return (
        _species_name(exp),
        exp.get("name") or "",
        exp.get("system_name") or "",
        exp.get("id") or 0,
    )


def build_experiment_row(exp: dict[str, Any]) -> list[str]:
    """Shape a gravi_experiments row (with joined species) into a display row."""
    eid = exp.get("id")
    return [
        _species_name(exp),
        exp.get("name") or "",
        exp.get("system_name") or "",
        "" if eid is None else str(eid),
    ]


def build_experiment_record(exp: dict[str, Any]) -> dict[str, Any]:
    """Machine-readable experiment record (mirrors the table columns)."""
    return {
        "species": (exp.get("species") or {}).get("common_name"),
        "experiment": exp.get("name"),
        "rig": exp.get("system_name"),
        "experiment_id": exp.get("id"),
    }


# --- supabase I/O ---


def fetch_species_with_experiments(client: Any) -> list[tuple[int, str]]:
    """Distinct (species_id, common_name) for species with at least one plate experiment."""
    rows = (
        client.table("gravi_experiments")
        .select("species_id, species(common_name)")
        .limit(DEFAULT_LIMIT)
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
    return sorted(by_id.items(), key=lambda kv: (kv[1], kv[0]))


def fetch_experiments(
    client: Any, *, species_id: int | None = None, limit: int = DEFAULT_LIMIT
) -> list[dict[str, Any]]:
    """Plate experiments with their joined species; ``species_id`` narrows to one species.

    gravi_experiments has no soft-delete column, so every row is live.
    """
    query = client.table("gravi_experiments").select("*, species(*)")
    if species_id is not None:
        query = query.eq("species_id", species_id)
    return query.order("id").limit(limit).execute().data or []


@experiments.command(name="list")
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
@click.option(
    "--limit",
    type=click.IntRange(min=1, max=DEFAULT_LIMIT),
    default=DEFAULT_LIMIT,
    show_default=True,
    help=f"Maximum number of experiments to fetch. Capped to {DEFAULT_LIMIT}.",
)
@click.option("--json", "as_json", is_flag=True, help="Alias for --output json.")
@click.option(
    "-p",
    "--profile",
    default=DEFAULT_PROFILE,
    show_default=True,
    help="Credentials profile to use.",
)
def list_experiments(
    species_name: str | None,
    pick_species: bool,
    output_fmt: str | None,
    limit: int,
    as_json: bool,
    profile: str,
) -> None:
    """List plate (GraviScan) experiments. Filter with --species NAME or --species-menu; use
    --output csv/json to grab an id for `plate download --experiment-id`."""
    from postgrest import APIError

    from ..cli import _authed_client

    if species_name is not None and pick_species:
        raise click.UsageError("Use either --species NAME or --species-menu, not both.")

    output_fmt = resolve_output_format(output_fmt, as_json)

    client = _authed_client(profile)
    try:
        species_id = None
        if pick_species:
            choices = fetch_species_with_experiments(client)
            if not choices:
                raise click.ClickException("No species with plate experiments found.")
            species_id = select_from_menu(
                choices, title="a species", prompt_label="Species", all_label="All species"
            )
        elif species_name is not None:
            species_id = resolve_by_name(fetch_species_with_experiments(client), species_name)
            if species_id is None:
                raise click.ClickException(
                    f"No species named {species_name!r} with plate experiments."
                )
        raw = fetch_experiments(client, species_id=species_id, limit=limit)
    except APIError as exc:
        raise click.ClickException(getattr(exc, "message", None) or str(exc)) from exc
    if len(raw) == limit:
        # Ordered by id, so the newest experiments are the ones dropped; stderr keeps stdout clean.
        click.echo(
            f"Warning: results capped at --limit {limit}; newer experiments may be omitted. "
            "Narrow with --species or raise --limit.",
            err=True,
        )
    rows_data = sorted(raw, key=experiment_sort_key)

    if output_fmt:
        records = [build_experiment_record(e) for e in rows_data]
        click.echo(render(records, RECORD_FIELDS, output_fmt))
        return

    rows = [build_experiment_row(e) for e in rows_data]
    print_table("Plate experiments", EXPERIMENT_COLUMNS, rows, empty="No plate experiments found.")
