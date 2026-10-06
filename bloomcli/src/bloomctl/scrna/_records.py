"""What Bloom already holds that the upload checks against: the species, and how a file's
expression values were made when the file does not say."""

from __future__ import annotations

from typing import Any

import click

from . import _format, _object
from ._text import listed, visible

MB = 1024 * 1024

DEFAULT_EXPRESSION_UNITS = "log1p normalised counts"

# The colour-bar label for each uns['normalization'] transform.
UNITS = {
    "log1p": "log1p normalised counts",
    "log2p": "log2(x+1) normalised counts",
    "none": "normalised counts",
}


def species(client: Any, typed: str) -> tuple[int, str]:
    """The species' id and stored name from its common name, ignoring case and spaces."""
    from .._postgrest import queried
    from ..cyl._select import resolve_by_name

    rows = queried(
        "the species", lambda: client.table("species").select("id, common_name").execute().data
    ) or []
    found = resolve_by_name([((r["id"], r["common_name"]), r["common_name"]) for r in rows], typed)
    if found is None:
        names = sorted(r["common_name"] for r in rows if r.get("common_name"))
        raise click.ClickException(
            f"no species named {visible(typed)!r}; Bloom has: {listed(names) or 'none'}. "
            "Nothing was sent."
        )
    return found


def stored_file_checks(
    client: Any, name: str, staged: _object.Staged, summary: _format.Summary
) -> tuple[dict | None, dict | None]:
    """The checks that need the gzipped form: the normalization record and the size.

    Returns how the file's values were made and, when the file does not say, the dataset
    that records it.
    """
    recorded_on = None
    normalization = summary.normalization
    if normalization is None:
        recorded_on = normalization_on_record(client, staged.fingerprint, summary.layers)
        if recorded_on is None:
            raise click.ClickException(
                f"{name} has no uns['normalization'] saying how X was made. Add one with "
                "transform (log1p, log2p or none), scaling (library_size, none or other), and "
                "target_sum for library_size or a description for other. Nothing was sent."
            )
        normalization = recorded_on["metadata"]["normalization"]
    if staged.size > _object.MAX_OBJECT_BYTES:
        raise click.ClickException(
            f"{name} gzips to {staged.size / MB:,.0f} MB; the limit is "
            f"{_object.MAX_OBJECT_BYTES // MB} MB. Nothing was sent."
        )
    return normalization, recorded_on


def normalization_on_record(client: Any, fingerprint: str, layers) -> dict[str, Any] | None:
    """The dataset loaded from this exact file that records how its X was made, if any."""
    from .._postgrest import queried

    rows = queried(
        "the datasets loaded from this file",
        lambda: client.table("scrna_datasets")
        .select("id, name, metadata")
        .eq("source_checksum", fingerprint)
        .is_("deleted_at", "null")
        .execute()
        .data,
    ) or []
    for row in rows:
        block = (row.get("metadata") or {}).get("normalization")
        if isinstance(block, dict) and _format.normalization_problem(block, layers=layers) is None:
            return row
    return None


def units_for(normalization: dict | None) -> str:
    """The colour-bar label for values made this way."""
    if not normalization:
        return DEFAULT_EXPRESSION_UNITS
    if normalization.get("transform") == "none" and normalization.get("scaling") == "none":
        return "counts"
    return UNITS.get(normalization.get("transform"), DEFAULT_EXPRESSION_UNITS)
