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


# Rows fetched per PostgREST request. The query pages with `.range()` until a short page,
# so the full set is returned regardless of the server's row cap — never silently truncated.
_PAGE_SIZE = 1000


def _fetch_all_pages(build_query: Any, page_size: int = _PAGE_SIZE) -> list[dict[str, Any]]:
    """Fetch every row by paging with ``.range()`` until a page comes back short.

    ``build_query`` returns a fresh, **ordered** query each call — a stable ``ORDER BY`` is
    required so successive pages don't overlap or skip rows. Avoids relying on PostgREST's
    default row cap silently truncating a large result.
    """
    rows: list[dict[str, Any]] = []
    start = 0
    while True:
        page = build_query().range(start, start + page_size - 1).execute().data or []
        rows.extend(page)
        if len(page) < page_size:
            return rows
        start += page_size


def fetch_qc_sets(client: Any, *, include_deleted: bool = False) -> list[dict[str, Any]]:
    """QC sets with species + QC-code ids (for the count), ordered by id.

    By default, inner-joins ``cyl_experiments`` and filters ``deleted_at IS NULL`` so a set
    attached to a soft-deleted experiment is excluded — otherwise ``bloom_admin`` (whose RLS
    reads ``USING (true)``) would see the tombstoned experiment's name that ``experiments list``
    hides, and other roles would get an orphan row. Pass ``include_deleted=True`` to drop that
    filter and list those sets too. The inner join also excludes any set whose ``experiment_id``
    is null; in practice every QC set is created against an experiment, so by default this only
    ever drops soft-deleted-experiment sets.

    Ordered by id — required for correct pagination and a deterministic base fetch (the display
    sort is applied client-side). Paged to exhaustion (``_fetch_all_pages``) so a large set list
    is never silently capped.
    """

    def _query() -> Any:
        q = client.table("cyl_qc_sets").select(
            "*, cyl_experiments!inner(*, species(*)), cyl_qc_codes(id)"
        )
        if not include_deleted:
            q = q.is_("cyl_experiments.deleted_at", "null")
        return q.order("id")

    return _fetch_all_pages(_query)


@qc.command(name="list-sets")
@click.option(
    "--output",
    "output_fmt",
    type=click.Choice(MACHINE_FORMATS),
    default=None,
    help="Emit machine-readable output instead of the table.",
)
@click.option(
    "--include-deleted",
    "include_deleted",
    is_flag=True,
    help="Also list QC sets whose experiment has been soft-deleted (hidden by default).",
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
def list_sets(output_fmt: str | None, include_deleted: bool, as_json: bool, profile: str) -> None:
    """List sets of cylinder QC (quality-control) data.

    QC sets whose experiment has been soft-deleted are hidden by default (only visible to
    bloom_admin, since RLS hides soft-deleted experiments from everyone else). Pass
    --include-deleted to list them too.
    """
    from postgrest import APIError

    from ..cli import _authed_client

    # --json is an alias for --output json; reject a conflicting pair.
    if as_json:
        if output_fmt not in (None, "json"):
            raise click.UsageError("Use either --json or --output, not both.")
        output_fmt = "json"

    client = _authed_client(profile)
    try:
        raw = fetch_qc_sets(client, include_deleted=include_deleted)
    except APIError as exc:
        raise click.ClickException(getattr(exc, "message", None) or str(exc)) from exc
    rows_data = sorted(raw, key=qc_set_sort_key)  # deterministic run-to-run
    if output_fmt:
        records = [build_qc_set_record(s) for s in rows_data]
        click.echo(render(records, QC_SET_FIELDS, output_fmt))
        return

    rows = [build_qc_set_row(s) for s in rows_data]
    print_table("QC sets", QC_SET_COLUMNS, rows, empty="No QC sets found.")
