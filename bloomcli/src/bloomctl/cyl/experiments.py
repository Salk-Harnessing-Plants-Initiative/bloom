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


def experiment_sort_key(exp: dict[str, Any]) -> tuple[str, str]:
    """Sort by species common name, then experiment name (matches legacy ordering)."""
    species = (exp.get("species") or {}).get("common_name") or ""
    return (species, exp.get("name") or "")


def build_experiment_row(exp: dict[str, Any]) -> list[str]:
    """Shape a cyl_experiments row (with joined species) into a display row."""
    species = (exp.get("species") or {}).get("common_name") or ""
    return [species, exp.get("name") or "", str(exp.get("id", ""))]


def build_experiment_record(exp: dict[str, Any]) -> dict[str, Any]:
    """Machine-readable experiment record (mirrors the table columns)."""
    return {
        "species": (exp.get("species") or {}).get("common_name"),
        "experiment": exp.get("name"),
        "experiment_id": exp.get("id"),
    }


# --- supabase / storage I/O ---


def fetch_experiments(client: Any) -> list[dict[str, Any]]:
    """All cyl experiments with their joined species relation."""
    return client.table("cyl_experiments").select("*, species(*)").execute().data or []


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
    from ..cli import _authed_client

    client = _authed_client(profile)
    rows_data = sorted(fetch_experiments(client), key=experiment_sort_key)

    if as_json:
        click.echo(json.dumps([build_experiment_record(e) for e in rows_data]))
        return

    rows = [build_experiment_row(e) for e in rows_data]
    print_table("Experiments", EXPERIMENT_COLUMNS, rows, empty="No experiments found.")
