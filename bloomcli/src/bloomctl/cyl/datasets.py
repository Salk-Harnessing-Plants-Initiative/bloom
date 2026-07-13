"""`bloomctl cyl datasets`: list + create cylinder trait datasets.

A dataset is a collection of traits for a set of plants in a cylinder experiment.
Pure helpers (row shaping) are separated from the supabase I/O so the contract is
unit-testable without a live server.
"""

from __future__ import annotations

import json
from typing import Any

import click

from ..credentials import DEFAULT_PROFILE
from ._output import print_table


@click.group(name="datasets")
def datasets() -> None:
    """Cylinder trait-dataset commands."""


def _nested(row: dict[str, Any], *keys: str) -> Any:
    """Safe nested lookup: return the value at row[k1][k2]... or None if any hop is missing."""
    cur: Any = row
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


# ############################### list ###############################

DATASET_COLUMNS = [
    "Name",
    "Timepoints",
    "Species",
    "Experiment",
    "QC Set",
    "Trait Source",
    "Created",
]


def _fmt_timepoints(value: Any) -> str:
    """Render the dataset's timepoints (a list, scalar, or null) as a cell string."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    return str(value)


def build_dataset_record(dataset: dict[str, Any]) -> dict[str, Any]:
    """Shape a joined cyl_datasets row into a machine-readable record (the `--json` object)."""
    return {
        "name": dataset.get("name"),
        "timepoints": dataset.get("timepoints"),
        "species": _nested(dataset, "cyl_experiments", "species", "common_name"),
        "experiment": _nested(dataset, "cyl_experiments", "name"),
        "qc_set": _nested(dataset, "cyl_qc_sets", "name"),
        "trait_source": _nested(dataset, "cyl_trait_sources", "name"),
        "created": str(dataset.get("created_at") or "")[:10],
    }


def build_dataset_row(dataset: dict[str, Any]) -> list[str]:
    """Shape a joined cyl_datasets row into the ordered display cells (blank for null relations)."""
    r = build_dataset_record(dataset)
    return [
        r["name"] or "",
        _fmt_timepoints(r["timepoints"]),
        r["species"] or "",
        r["experiment"] or "",
        r["qc_set"] or "",
        r["trait_source"] or "",
        r["created"],
    ]


def fetch_datasets(client: Any, experiment_id: int | None = None) -> list[dict[str, Any]]:  # supabase I/O
    """Query cyl_datasets joined to its experiment/species, QC set, and trait source.

    When ``experiment_id`` is given, restrict to datasets for that experiment.
    """
    query = client.table("cyl_datasets").select(
        "*, cyl_experiments(*, species(*)), cyl_qc_sets(*), cyl_trait_sources(*)"
    )
    if experiment_id is not None:
        query = query.eq("experiment_id", experiment_id)
    return query.execute().data or []


@datasets.command(name="list")
@click.option(
    "--experiment-id",
    "--experiment_id",
    "experiment_id",
    type=int,
    default=None,
    help="Only list datasets for this experiment.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the datasets as a JSON array instead of a table.",
)
@click.option(
    "-p",
    "--profile",
    default=DEFAULT_PROFILE,
    show_default=True,
    help="Credentials profile to use.",
)
def list_datasets(experiment_id: int | None, as_json: bool, profile: str) -> None:
    """List cylinder trait datasets."""
    from ..cli import _authed_client

    client = _authed_client(profile)
    found = fetch_datasets(client, experiment_id=experiment_id)

    if as_json:
        click.echo(json.dumps([build_dataset_record(d) for d in found]))
        return

    rows = [build_dataset_row(d) for d in found]
    print_table("Datasets", DATASET_COLUMNS, rows, empty="No datasets found.")


# ############################## create ##############################


def fetch_experiment(client: Any, experiment_id: int) -> dict[str, Any] | None:  # supabase I/O
    """Return the cyl_experiments row for `experiment_id`, or None if it does not exist."""
    rows = (
        client.table("cyl_experiments")
        .select("*")
        .eq("id", experiment_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0] if rows else None


def resolve_trait_source(client: Any, name: str) -> int | None:  # supabase I/O
    """Return the cyl_trait_sources id for `name`, or None if it does not resolve."""
    rows = (
        client.table("cyl_trait_sources")
        .select("id")
        .eq("name", name)
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0]["id"] if rows else None


def create_cyl_dataset(client: Any, params: dict[str, Any]) -> Any:  # supabase I/O
    """Call the create_cyl_dataset RPC (RETURNS void); lets postgrest.APIError propagate."""
    return client.rpc("create_cyl_dataset", params).execute().data


def _parse_timepoints(values: tuple[str, ...]) -> list[int] | None:
    """Parse repeatable and/or comma-delimited `--timepoints` values into ints (None if empty)."""
    out: list[int] = []
    for value in values:
        for part in str(value).split(","):
            part = part.strip()
            if not part:
                continue
            try:
                out.append(int(part))
            except ValueError as exc:
                raise click.BadParameter(f"timepoints must be integers, got {part!r}") from exc
    return out or None


@datasets.command(name="create")
@click.argument("name")
@click.argument("experiment_id", type=int)
@click.argument("trait_source_name")
@click.option(
    "--qc-set-name",
    "--qc_set_name",
    "qc_set_name",
    default=None,
    help="Name of a QC set to exclude from the dataset.",
)
@click.option(
    "--timepoints",
    multiple=True,
    help="Timepoints to include (repeatable or comma-delimited, e.g. --timepoints 1,3,5).",
)
@click.option(
    "-p",
    "--profile",
    default=DEFAULT_PROFILE,
    show_default=True,
    help="Credentials profile to use.",
)
def create_dataset(
    name: str,
    experiment_id: int,
    trait_source_name: str,
    qc_set_name: str | None,
    timepoints: tuple[str, ...],
    profile: str,
) -> None:
    """Create a new dataset for a cylinder experiment.

    A dataset is a collection of traits for a set of plants in a cylinder experiment.
    """
    from postgrest import APIError

    from ..cli import _authed_client

    parsed_timepoints = _parse_timepoints(timepoints)
    client = _authed_client(profile)

    # Resolve + validate before the write so failures are actionable and make no RPC call.
    if fetch_experiment(client, experiment_id) is None:
        raise click.ClickException(
            f"Experiment {experiment_id} not found — look up valid experiment ids "
            "(cyl experiments list)."
        )
    trait_source_id = resolve_trait_source(client, trait_source_name)
    if trait_source_id is None:
        raise click.ClickException(
            f"Trait source {trait_source_name!r} not found — look up valid trait source "
            "names (cyl traits list-sources)."
        )

    # Legacy param shape: qc_set_name is the object {"name": ...}, timepoints a list or null.
    params = {
        "name": name,
        "experiment_id": experiment_id,
        "trait_source_id": trait_source_id,
        "qc_set_name": {"name": qc_set_name},
        "timepoints": parsed_timepoints,
    }
    try:
        create_cyl_dataset(client, params)
    except APIError as exc:
        raise click.ClickException(getattr(exc, "message", None) or str(exc)) from exc

    click.echo(f"Created dataset {name!r} for experiment {experiment_id}.")
