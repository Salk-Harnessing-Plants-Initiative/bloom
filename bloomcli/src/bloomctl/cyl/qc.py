"""`bloomctl cyl qc` — cylinder quality-control commands (list-sets).

`upload` (write) is a follow-up; only the read command is ported here.
"""

from __future__ import annotations

import json
from typing import Any

import click

from ..credentials import DEFAULT_PROFILE
from ._output import print_table

# Table columns for `qc list-sets`, in display order.
QC_SET_COLUMNS = ["QC Set", "QC Set ID", "Species", "Experiment", "Experiment ID", "QC Codes"]


@click.group(name="qc")
def qc() -> None:
    """Cylinder QC (quality-control) commands."""


def _experiment(qc_set: dict[str, Any]) -> dict[str, Any]:
    return qc_set.get("cyl_experiments") or {}


def qc_set_sort_key(qc_set: dict[str, Any]) -> tuple[str, str]:
    """Sort by species common name, then QC-set name."""
    species = (_experiment(qc_set).get("species") or {}).get("common_name") or ""
    return (species, qc_set.get("name") or "")


def build_qc_set_row(qc_set: dict[str, Any]) -> list[str]:
    """Shape a cyl_qc_sets row (with experiment/species + codes) into a display row."""
    exp = _experiment(qc_set)
    species = (exp.get("species") or {}).get("common_name") or ""
    set_id = qc_set.get("id")
    exp_id = exp.get("id")
    codes = qc_set.get("cyl_qc_codes") or []
    return [
        qc_set.get("name") or "",
        "" if set_id is None else str(set_id),
        species,
        exp.get("name") or "",
        "" if exp_id is None else str(exp_id),
        str(len(codes)),
    ]


def build_qc_set_record(qc_set: dict[str, Any]) -> dict[str, Any]:
    """Machine-readable QC-set record (mirrors the table columns). Includes the set's
    own id — the write path (`cyl qc upload`) keys on set_id, so it must be listable."""
    exp = _experiment(qc_set)
    return {
        "id": qc_set.get("id"),
        "name": qc_set.get("name"),
        "species": (exp.get("species") or {}).get("common_name"),
        "experiment": exp.get("name"),
        "experiment_id": exp.get("id"),
        "qc_code_count": len(qc_set.get("cyl_qc_codes") or []),
    }


# --- supabase I/O ---


def fetch_qc_sets(client: Any) -> list[dict[str, Any]]:
    """All QC sets with their experiment/species relation and QC-code ids (for the count)."""
    return (
        client.table("cyl_qc_sets")
        .select("*, cyl_experiments(*, species(*)), cyl_qc_codes(id)")
        .execute()
        .data
        or []
    )


@qc.command(name="list-sets")
@click.option("--json", "as_json", is_flag=True, help="Emit QC sets as a JSON array.")
@click.option(
    "-p",
    "--profile",
    default=DEFAULT_PROFILE,
    show_default=True,
    help="Credentials profile to use.",
)
def list_sets(as_json: bool, profile: str) -> None:
    """List sets of cylinder QC (quality-control) data."""
    from postgrest import APIError

    from ..cli import _authed_client

    client = _authed_client(profile)
    try:
        raw = fetch_qc_sets(client)
    except APIError as exc:
        raise click.ClickException(getattr(exc, "message", None) or str(exc)) from exc
    data = sorted(raw, key=qc_set_sort_key)

    if as_json:
        click.echo(json.dumps([build_qc_set_record(s) for s in data]))
        return

    rows = [build_qc_set_row(s) for s in data]
    print_table("QC sets", QC_SET_COLUMNS, rows, empty="No QC sets found.")
