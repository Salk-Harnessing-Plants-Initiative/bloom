"""`bloomctl cyl qc` — cylinder quality-control commands (list-sets).

`upload` (write) is a follow-up; only the read command is ported here.
"""

from __future__ import annotations

from typing import Any

import click

from ..credentials import DEFAULT_PROFILE
from ._output import MACHINE_FORMATS, print_table, render

# Table headers for `qc list-sets`, in display order. Wording is inherited from
# the legacy CLI so users moving across recognise the output.
QC_SET_COLUMNS = [
    "QC Set Name",
    "Species",
    "Experiment Name",
    "Experiment ID",
    "Number of QC Codes",
]

# Machine-readable field names, same five fields in the same order.
QC_SET_FIELDS = ["name", "species", "experiment", "experiment_id", "qc_code_count"]


@click.group(name="qc")
def qc() -> None:
    """Cylinder QC (quality-control) commands."""


def _experiment(qc_set: dict[str, Any]) -> dict[str, Any]:
    return qc_set.get("cyl_experiments") or {}


def build_qc_set_row(qc_set: dict[str, Any]) -> list[str]:
    """Shape a cyl_qc_sets row (with experiment/species + codes) into a display row."""
    exp = _experiment(qc_set)
    species = (exp.get("species") or {}).get("common_name") or ""
    exp_id = exp.get("id")
    codes = qc_set.get("cyl_qc_codes") or []
    return [
        qc_set.get("name") or "",
        species,
        exp.get("name") or "",
        "" if exp_id is None else str(exp_id),
        str(len(codes)),
    ]


def build_qc_set_record(qc_set: dict[str, Any]) -> dict[str, Any]:
    """Machine-readable QC-set record — the same five fields as the table."""
    exp = _experiment(qc_set)
    return {
        "name": qc_set.get("name"),
        "species": (exp.get("species") or {}).get("common_name"),
        "experiment": exp.get("name"),
        "experiment_id": exp.get("id"),
        "qc_code_count": len(qc_set.get("cyl_qc_codes") or []),
    }


def qc_set_sort_key(qc_set: dict[str, Any]) -> tuple[str, str, str, int]:
    """Sort by species, then experiment name, then set name, then set id (id breaks ties so
    output is deterministic run-to-run) — matching the other cyl list commands."""
    exp = _experiment(qc_set)
    species = (exp.get("species") or {}).get("common_name") or ""
    sid = qc_set.get("id")
    return (
        species,
        exp.get("name") or "",
        qc_set.get("name") or "",
        sid if sid is not None else -1,
    )


# --- supabase I/O ---


def fetch_qc_sets(client: Any) -> list[dict[str, Any]]:
    """QC sets for live experiments, with species + QC-code ids (for the count).

    Inner-joins ``cyl_experiments`` and filters ``deleted_at IS NULL`` so a set attached to a
    soft-deleted experiment is excluded — otherwise ``bloom_admin`` (whose RLS reads
    ``USING (true)``) would see the tombstoned experiment's name that ``experiments list`` hides,
    and other roles would get an orphan row. Ordered by id for a deterministic base fetch (the
    display sort is applied client-side).
    """
    return (
        client.table("cyl_qc_sets")
        .select("*, cyl_experiments!inner(*, species(*)), cyl_qc_codes(id)")
        .is_("cyl_experiments.deleted_at", "null")
        .order("id")
        .execute()
        .data
        or []
    )


@qc.command(name="list-sets")
@click.option(
    "--output",
    "output_fmt",
    type=click.Choice(MACHINE_FORMATS),
    default=None,
    help="Emit machine-readable output instead of the table.",
)
@click.option(
    "-p",
    "--profile",
    default=DEFAULT_PROFILE,
    show_default=True,
    help="Credentials profile to use.",
)
def list_sets(output_fmt: str | None, profile: str) -> None:
    """List sets of cylinder QC (quality-control) data."""
    from postgrest import APIError

    from ..cli import _authed_client

    client = _authed_client(profile)
    try:
        raw = fetch_qc_sets(client)
    except APIError as exc:
        raise click.ClickException(getattr(exc, "message", None) or str(exc)) from exc
    rows_data = sorted(raw, key=qc_set_sort_key)  # deterministic run-to-run
    if output_fmt:
        records = [build_qc_set_record(s) for s in rows_data]
        click.echo(render(records, QC_SET_FIELDS, output_fmt))
        return

    rows = [build_qc_set_row(s) for s in rows_data]
    print_table("QC sets", QC_SET_COLUMNS, rows, empty="No QC sets found.")
