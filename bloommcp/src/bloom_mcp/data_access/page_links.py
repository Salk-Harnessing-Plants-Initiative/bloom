"""Bloom's web pages for tables Claude reads, shown in list_tables so answers can link back.

Each pattern is a path under SITE_URL whose {placeholders} are columns of that table, each
matching the route's [param] in PAGE_PARAMS. With SITE_URL unset no link note is shown.
"""

from __future__ import annotations

import logging
import os
from typing import Any
import re
from urllib.parse import quote

_PLACEHOLDER = re.compile(r"\{(\w+)\}")

PAGE_LINKS: dict[str, str] = {
    "cyl_experiments": "/app/phenotypes/{species_id}/{id}",
    "cyl_scans_extended": (
        "/app/phenotypes/{species_id}/{experiment_id}/{wave_id}/{accession_id}/{scan_id}"
    ),
    "gravi_experiments": "/app/plate-phenotypes/{species_id}/{id}",
    "gravi_scans_extended": (
        "/app/plate-phenotypes/{species_id}/{experiment_id}/wave/{wave_number}/{plate_id}"
    ),
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
    "gravi_scans_extended": {
        "species_id": "speciesId",
        "experiment_id": "experimentId",
        "wave_number": "waveNumber",
        "plate_id": "plateId",
    },
    "scrna_datasets": {"species_id": "speciesId", "id": "datasetId"},
    "rnaseq_runs": {"id": "runId"},
}


# The traits page opened on one wave and plant age; the user picks the trait there.
TRAITS_PAGE = (
    "/app/traits/{species_id}/{experiment_id}?wave={wave_number}&age={plant_age_days}"
)
# Tables and functions whose trait figures link to TRAITS_PAGE.
TRAIT_FIGURE_SOURCES = frozenset({"cyl_scan_traits_latest", "get_scan_traits"})


def link_note(name: str) -> str | None:
    """How to link a row of table `name`, or its trait figures, to Bloom's pages."""
    site = (os.environ.get("SITE_URL") or "").strip().rstrip("/")
    if not site:
        return None
    notes = []
    if pattern := PAGE_LINKS.get(name):
        notes.append(
            f"Bloom page for a row: {site}{pattern}, with each {{column}} filled from the "
            "row (read those columns too). Give this link when you mention the row; give "
            "none when any of those columns is empty."
        )
    if name in TRAIT_FIGURE_SOURCES:
        notes.append(
            f"Link each wave-and-age set of trait figures to Bloom's traits page: "
            f"{site}{TRAITS_PAGE}, with species_id and experiment_id from cyl_experiments; "
            "say the user picks the trait there."
        )
    return " ".join(notes) or None


def page_columns(name: str) -> list[str]:
    """The columns a row of `name` needs for its Bloom page link."""
    return _PLACEHOLDER.findall(PAGE_LINKS[name])


def page_url(name: str, row: dict[str, Any]) -> str | None:
    """The Bloom page for one row of `name`; None without SITE_URL or a column."""
    site = (os.environ.get("SITE_URL") or "").strip().rstrip("/")
    values = {c: row.get(c) for c in page_columns(name)}
    if not site or any(v is None for v in values.values()):
        return None
    path = PAGE_LINKS[name].format(
        **{c: quote(str(v), safe="") for c, v in values.items()}
    )
    return site + path


def scan_pages(reader: Any, view: str, scan_ids: list) -> dict[Any, str]:
    """Bloom page per scan_id, read from `view` in one select; empty if that fails."""
    from .table_ports import Filter, Sort

    if not scan_ids:
        return {}
    columns = list(dict.fromkeys(["scan_id", *page_columns(view)]))
    try:
        rows = reader.select(
            view,
            columns,
            [Filter("scan_id", "in", scan_ids)],
            [Sort("scan_id")],
            len(scan_ids),
            0,
        )
    except Exception as exc:  # noqa: BLE001 — a page link is a convenience; never fail a read
        logging.getLogger(__name__).warning(
            "scan pages unavailable: %s", type(exc).__name__
        )
        return {}
    pages = {r.get("scan_id"): page_url(view, r) for r in rows}
    return {k: v for k, v in pages.items() if v}
