"""bloom-dataset pick|id --name N --species S --run-id R: the load-dataset step's dataset lookups.

pick prints the name to load run R's dataset under: the name, or its versioned form
(N_v2, N_v3, ...), that already holds this run's dataset, else the first one free. id prints
the id of the dataset run R loaded under that name. Signs in as bloomctl does (~/.bloom).
Exits 6 for a species or dataset that isn't there.
"""

from __future__ import annotations

import argparse
import sys

EXIT_NOT_FOUND = 6
# Where bloomctl --run-id records the run on a dataset (bloomctl.scrna._checks.RUN_ID_KEY).
RUN_ID_KEY = "rnaseq_run_id"
# A bound on versions tried, far above any real run.
MAX_VERSIONS = 1000


def _key(name: str) -> str:
    """Names compare as bloomctl compares them: trimmed, ignoring case."""
    return name.strip().casefold()


def _versions(name: str) -> list[str]:
    """The name and its versioned forms, in the order bloomctl's next_name makes them."""
    from bloomctl.scrna._writer import next_name

    names = [name.strip()]
    while len(names) < MAX_VERSIONS:
        names.append(next_name(names[-1]))
    return names


def _runs(row: dict) -> object:
    return (row.get("metadata") or {}).get(RUN_ID_KEY)


def choose_name(rows: list[dict], name: str, run_id: int) -> str:
    """The name for run_id's dataset among a species' live datasets (rows)."""
    taken = {_key(r.get("name") or ""): r for r in rows}
    versions = _versions(name)
    mine = [v for v in versions if _runs(taken.get(_key(v), {})) == run_id]
    if mine:
        return mine[0]
    for version in versions:
        if _key(version) not in taken:
            return version
    raise LookupError(f"no free name among {MAX_VERSIONS} versions of {name!r}")


def dataset_id(rows: list[dict], name: str, run_id: int) -> int:
    """The id of the dataset run_id loaded under name."""
    for row in rows:
        if _key(row.get("name") or "") == _key(name) and _runs(row) == run_id:
            return row["id"]
    raise LookupError(f"no dataset {name!r} loaded for run {run_id}")


def _rows(species: str) -> list[dict]:
    """The species' live datasets, read as bloomctl's default login."""
    from bloomctl.credentials import DEFAULT_PROFILE
    from bloomctl.scrna import _records, _session

    client = _session.connect(DEFAULT_PROFILE).client
    species_id, _ = _records.species(client, species)
    return (
        client.table("scrna_datasets")
        .select("id, name, metadata")
        .eq("species_id", species_id)
        .is_("deleted_at", "null")
        .execute()
        .data
        or []
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bloom-dataset")
    parser.add_argument("action", choices=["pick", "id"])
    parser.add_argument("--name", required=True)
    parser.add_argument("--species", required=True)
    parser.add_argument("--run-id", type=int, required=True)
    args = parser.parse_args(argv)
    from click import ClickException

    try:
        rows = _rows(args.species)
        if args.action == "pick":
            print(choose_name(rows, args.name, args.run_id))
        else:
            print(dataset_id(rows, args.name, args.run_id))
    except (LookupError, ClickException) as exc:  # ClickException: no such species
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_NOT_FOUND
    return 0


if __name__ == "__main__":
    sys.exit(main())
