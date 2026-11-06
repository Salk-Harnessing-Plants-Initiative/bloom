"""Links to Bloom's web pages, built from SITE_URL; None when SITE_URL is unset."""

from __future__ import annotations

import os
from urllib.parse import quote


def _link(*parts: object) -> str | None:
    site = (os.environ.get("SITE_URL") or "").rstrip("/")
    if not site:
        return None
    return site + "/app/" + "/".join(quote(str(p), safe="") for p in parts)


def cyl_experiment(species_id: int, experiment_id: int) -> str | None:
    return _link("phenotypes", species_id, experiment_id)


def cyl_scan(
    species_id: int, experiment_id: int, wave_id: int, accession_id: int, scan_id: int
) -> str | None:
    return _link(
        "phenotypes", species_id, experiment_id, wave_id, accession_id, scan_id
    )


def plate_experiment(species_id: int, experiment_id: int) -> str | None:
    return _link("plate-phenotypes", species_id, experiment_id)


def plate_wave(species_id: int, experiment_id: int, wave_number: int) -> str | None:
    return _link("plate-phenotypes", species_id, experiment_id, "wave", wave_number)


def plate(
    species_id: int, experiment_id: int, wave_number: int, plate_id: str
) -> str | None:
    return _link(
        "plate-phenotypes", species_id, experiment_id, "wave", wave_number, plate_id
    )


def expression_dataset(species_id: int, dataset_id: int) -> str | None:
    return _link("expression", species_id, dataset_id)


def rnaseq_run(run_id: str) -> str | None:
    return _link("rnaseq-runs", run_id)


def traits_experiment(species_id: int, experiment_id: int) -> str | None:
    return _link("traits", species_id, experiment_id)
