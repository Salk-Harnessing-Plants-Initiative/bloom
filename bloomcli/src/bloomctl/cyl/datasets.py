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
from ._output import MACHINE_FORMATS, print_table, render, resolve_output_format
from ._select import select_from_menu


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
# Machine-readable field names (json/csv), matching build_dataset_record below.
DATASET_FIELDS = [
    "name",
    "timepoints",
    "species",
    "experiment",
    "qc_set",
    "trait_source",
    "created",
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


def fetch_datasets(
    client: Any, experiment_id: int | None = None
) -> list[dict[str, Any]]:  # supabase I/O
    """Query cyl_datasets joined to its experiment/species, QC set, and trait source.

    When ``experiment_id`` is given, restrict to datasets for that experiment.
    """
    query = client.table("cyl_datasets").select(
        "*, cyl_experiments(*, species(*)), cyl_qc_sets(*), cyl_trait_sources(*)"
    )
    if experiment_id is not None:
        query = query.eq("experiment_id", experiment_id)
    return query.execute().data or []


def fetch_experiments_with_datasets(client: Any) -> list[tuple[int, str]]:  # supabase I/O
    """(experiment_id, "name (species)") for experiments that have datasets, for the menu.

    cyl_datasets carries only experiment_id, so the human labels are joined from cyl_experiments
    (soft-deleted excluded). Sorted by label for a stable menu.
    """
    rows = client.table("cyl_datasets").select("experiment_id").execute().data or []
    ids = sorted({r["experiment_id"] for r in rows if r.get("experiment_id") is not None})
    if not ids:
        return []
    exps = (
        client.table("cyl_experiments")
        .select("id, name, species(common_name)")
        .in_("id", ids)
        .is_("deleted_at", "null")
        .execute()
        .data
        or []
    )
    items = [
        (e["id"], f"{e.get('name') or ''} ({(e.get('species') or {}).get('common_name') or '?'})")
        for e in exps
    ]
    # id as a tiebreak so two experiments with the same "name (species)" label order stably.
    return sorted(items, key=lambda it: (it[1].casefold(), it[0]))


@datasets.command(name="list")
@click.option(
    "--experiment-id",
    "--experiment_id",
    "experiment_id",
    type=int,
    default=None,
    help="Only list datasets for this experiment (id). Scriptable.",
)
@click.option(
    "--experiment",
    "pick_experiment",
    is_flag=True,
    help="Pick an experiment from an interactive menu to filter by (needs a terminal).",
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
def list_datasets(
    experiment_id: int | None,
    pick_experiment: bool,
    output_fmt: str | None,
    as_json: bool,
    profile: str,
) -> None:
    """List cylinder trait datasets.

    Lists all datasets by default. Pass --experiment-id N for one experiment (scriptable), or
    --experiment to pick one from a menu (needs a terminal).
    """
    from postgrest import APIError

    from ..cli import _authed_client

    if pick_experiment and experiment_id is not None:
        raise click.UsageError("Use either --experiment-id or --experiment, not both.")

    output_fmt = resolve_output_format(output_fmt, as_json)  # --json aliases --output json

    client = _authed_client(profile)
    try:
        if pick_experiment:  # menu of experiments that have datasets (0 = All)
            choices = fetch_experiments_with_datasets(client)
            if not choices:
                raise click.ClickException("No experiments with datasets found.")
            experiment_id = select_from_menu(
                choices,
                title="an experiment",
                prompt_label="Experiment",
                all_label="All experiments",
            )
        found = fetch_datasets(client, experiment_id=experiment_id)
    except APIError as exc:
        raise click.ClickException(getattr(exc, "message", None) or str(exc)) from exc

    if output_fmt:
        click.echo(render([build_dataset_record(d) for d in found], DATASET_FIELDS, output_fmt))
        return

    rows = [build_dataset_row(d) for d in found]
    print_table("Datasets", DATASET_COLUMNS, rows, empty="No datasets found.")


# ############################## create ##############################


def fetch_experiment(client: Any, experiment_id: int) -> dict[str, Any] | None:  # supabase I/O
    """Return the cyl_experiments row for `experiment_id`, or None if it does not exist."""
    rows = (
        client.table("cyl_experiments").select("*").eq("id", experiment_id).limit(1).execute().data
        or []
    )
    return rows[0] if rows else None


def resolve_trait_source(client: Any, name: str) -> int | None:  # supabase I/O
    """Return the cyl_trait_sources id for `name`, or None if it does not resolve."""
    rows = (
        client.table("cyl_trait_sources").select("id").eq("name", name).limit(1).execute().data
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

    Requires a profile with write access (bloom_writer / bloom_admin) — intended for
    automated pipelines (e.g. the trait-extraction write-back) or users granted write
    access; a read-only bloom_user login can `datasets list` but gets a permission error.
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


# ################################ get ###############################


def fetch_dataset_by_name(client: Any, name: str) -> dict[str, Any] | None:  # supabase I/O
    """Fetch one dataset by name with its experiment/QC/trait-source relations, or None."""
    rows = (
        client.table("cyl_datasets")
        .select("*, cyl_experiments(*, species(*)), cyl_qc_sets(*), cyl_trait_sources(*)")
        .eq("name", name)
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0] if rows else None


def fetch_dataset_traits(client: Any, dataset_id: Any) -> list[str]:  # supabase I/O
    """Unique, sorted trait names in a dataset, via the ``cyl_dataset_trait_names`` view.

    The view resolves cyl_dataset_traits → cyl_scan_traits → cyl_traits server-side and
    returns the distinct (small) trait-name set per dataset, so this single query stays
    well under the API row cap.
    """
    rows = (
        client.table("cyl_dataset_trait_names")
        .select("trait_name")
        .eq("dataset_id", dataset_id)
        .execute()
        .data
        or []
    )
    return sorted({r["trait_name"] for r in rows if isinstance(r, dict) and r.get("trait_name")})


@datasets.command(name="get")
@click.argument("name")
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the dataset (with its traits) as a JSON object.",
)
@click.option(
    "-p",
    "--profile",
    default=DEFAULT_PROFILE,
    show_default=True,
    help="Credentials profile to use.",
)
def get_dataset(name: str, as_json: bool, profile: str) -> None:
    """Show a dataset's details and the unique traits it contains."""
    from ..cli import _authed_client

    client = _authed_client(profile)
    dataset = fetch_dataset_by_name(client, name)
    if dataset is None:
        raise click.ClickException(
            f"Dataset {name!r} not found — run `cyl datasets list` to see available datasets."
        )

    traits = fetch_dataset_traits(client, dataset["id"])

    if as_json:
        record = build_dataset_record(dataset)
        record["traits"] = traits
        click.echo(json.dumps(record))
        return

    print_table("Dataset", DATASET_COLUMNS, [build_dataset_row(dataset)], empty="")
    click.echo(f"\nTraits ({len(traits)} unique):")
    for trait in traits:
        click.echo(f"  - {trait}")
    if not traits:
        click.echo("  (none)")
