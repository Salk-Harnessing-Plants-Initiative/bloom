"""Bloom's web pages for tables Claude reads, shown in list_tables so answers can link back.

Each pattern is a path under SITE_URL whose {placeholders} are columns of that table; with
SITE_URL unset (local dev) no link note is shown.
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


def link_note(table: str) -> str | None:
    """The note telling Claude how to link a row of `table` to its Bloom page."""
    pattern = PAGE_LINKS.get(table)
    site = (os.environ.get("SITE_URL") or "").rstrip("/")
    if not pattern or not site:
        return None
    return (
        f"Bloom page for a row: {site}{pattern}, with each {{column}} filled from the "
        "row (read those columns too). Give this link when you mention the row."
    )
