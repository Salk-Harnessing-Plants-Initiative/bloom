"""Bloom's web pages for tables Claude reads, shown in list_tables so answers can link back.

Each pattern is a path under SITE_URL whose {placeholders} are columns of that table, each
matching the route's [param] in PAGE_PARAMS. With SITE_URL unset no link note is shown.
"""

from __future__ import annotations

import os

PAGE_LINKS: dict[str, str] = {
    "cyl_experiments": "/app/phenotypes/{species_id}/{id}",
    "cyl_scans_extended": (
        "/app/phenotypes/{species_id}/{experiment_id}/{wave_id}/{accession_id}/{scan_id}"
    ),
    "gravi_experiments": "/app/plate-phenotypes/{species_id}/{id}",
    "scrna_datasets": "/app/expression/{species_id}/{id}",
    "rnaseq_runs": "/app/rnaseq-runs/{id}",
}

# Each placeholder's route param, so a reordered pattern fails the route test.
PAGE_PARAMS: dict[str, dict[str, str]] = {
    "cyl_experiments": {"species_id": "speciesId", "id": "experimentId"},
    "cyl_scans_extended": {
        "species_id": "speciesId",
        "experiment_id": "experimentId",
        "wave_id": "waveId",
        "accession_id": "accessionId",
        "scan_id": "scanId",
    },
    "gravi_experiments": {"species_id": "speciesId", "id": "experimentId"},
    "scrna_datasets": {"species_id": "speciesId", "id": "datasetId"},
    "rnaseq_runs": {"id": "runId"},
}


def link_note(table: str) -> str | None:
    """The note telling Claude how to link a row of `table` to its Bloom page."""
    pattern = PAGE_LINKS.get(table)
    site = (os.environ.get("SITE_URL") or "").strip().rstrip("/")
    if not pattern or not site:
        return None
    return (
        f"Bloom page for a row: {site}{pattern}, with each {{column}} filled from the "
        "row (read those columns too). Give this link when you mention the row; give none "
        "when any of those columns is empty."
    )
