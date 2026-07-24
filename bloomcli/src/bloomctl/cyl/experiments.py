"""`bloomctl cyl experiments` — cylinder experiment commands (list)."""

from __future__ import annotations

import json
from typing import Any

import click

from ..credentials import DEFAULT_PROFILE
from ._output import print_table

# Table columns for `experiments list`, in display order.
EXPERIMENT_COLUMNS = ["Species", "Experiment", "Experiment ID"]


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


# --- supabase I/O ---


def fetch_experiments(client: Any) -> list[dict[str, Any]]:
    """Live cyl experiments (soft-deleted excluded) with their joined species relation.

    Filters ``deleted_at IS NULL`` server-side rather than relying on RLS: only the
    bloom_user policy hides soft-deletes, while bloom_writer/bloom_admin read with
    ``USING (true)`` and would otherwise see tombstoned experiments.
    """
    return (
        client.table("cyl_experiments")
        .select("*, species(*)")
        .is_("deleted_at", "null")
        .order("id")
        .execute()
        .data
        or []
    )


@experiments.command(name="list")
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit experiments as a JSON array.",
)
@click.option(
    "-p",
    "--profile",
    default=DEFAULT_PROFILE,
    show_default=True,
    help="Credentials profile to use.",
)
def list_experiments(as_json: bool, profile: str) -> None:
    """List cylinder experiments."""
    from postgrest import APIError

    from ..cli import _authed_client

    client = _authed_client(profile)
    try:
        raw = fetch_experiments(client)
    except APIError as exc:
        raise click.ClickException(getattr(exc, "message", str(exc))) from exc
    rows_data = sorted(raw, key=experiment_sort_key)

    if as_json:
        click.echo(json.dumps([build_experiment_record(e) for e in rows_data]))
        return

    rows = [build_experiment_row(e) for e in rows_data]
    print_table("Experiments", EXPERIMENT_COLUMNS, rows, empty="No experiments found.")
