"""scrna_de's reply pieces: genes, markers and comparisons built from the DE reads."""

from __future__ import annotations

import time
from typing import Any

from bloom_mcp.data_access import scrna_de_reads as de
from bloom_mcp.data_access.gene_reads import Reads

from ._page import _cut
from .scrna_de_reply import Comparison, DeGene, MarkerGene, Markers, Side

OUT_OF_TIME = "Not read: the call ran out of time."
NOT_TESTED = "Not tested (too few cells or genes), as the DE tab shows it."
PAST_LIMIT = "Genes not read: past this call's comparison limit."
_GENE_KEYS = (
    "log2fc",
    "fdr",
    "found_in_one_group_only",
    "likely_found_in_one_group_only",
)


class Call:
    """One call's deadline, comparison budget and notes."""

    def __init__(self, seconds: float, max_comparisons: int) -> None:
        self.deadline = time.monotonic() + seconds
        self.max_comparisons = max_comparisons
        self.comparisons_read = 0
        self.notes: list[str] = []

    def late(self) -> bool:
        return time.monotonic() >= self.deadline

    def note(self, text: str) -> None:
        if text not in self.notes:
            self.notes.append(text)


def text(value: Any) -> Any:
    """Stored text cut as query_table cuts it, so one value can't fill the reply."""
    return _cut(value)[0] if isinstance(value, str) else value


def _gene(entry: dict[str, Any]) -> dict[str, Any]:
    return {"gene": text(entry["gene"]), **{k: entry[k] for k in _GENE_KEYS}}


def marker(entry: dict[str, Any]) -> MarkerGene:
    return MarkerGene(
        **_gene(entry),
        symbol=text(entry["symbol"]),
        source=text(entry["source"]),
        pct_in_cell_type=entry["pct_1"],
        pct_elsewhere=entry["pct_2"],
    )


def markers(
    reads: Reads, call: Call, dataset_id: int, clusters: list[str], run_ids: list[int]
) -> dict[str, Markers | None]:
    """Each cell type's panel markers; None for those the call had no time to read."""
    if call.late():
        call.note(OUT_OF_TIME + " Ask for fewer cell types.")
        return {c: None for c in clusters}
    found = de.panel_markers(reads, dataset_id, clusters, run_ids, call.late)
    result: dict[str, Markers | None] = {}
    for cluster, m in found.items():
        if m["from"] == "not_read":
            call.note(OUT_OF_TIME + " Ask for fewer cell types.")
            result[cluster] = None
            continue
        n = m["n_significant"]
        result[cluster] = Markers(
            source=m["from"],
            significant=None if n is None else int(n),
            genes=[marker(g) for g in m["top"]],
        )
    return result


def group_names(row: dict[str, Any]) -> tuple[str, str]:
    """The comparison's two groups, or the website's stand-ins (groupNames)."""
    if row.get("group1") and row.get("group2"):
        return text(row["group1"]), text(row["group2"])
    return text(row.get("cluster_id")) or "this cell type", "the rest"


def _not_evidence(row: dict[str, Any]) -> str:
    """The DE tab's note when no gene passes (notEvidenceNote)."""
    lead = "No gene passes the cuts. This is not evidence of no difference: "
    g1, g2 = row.get("group1"), row.get("group2")
    n1, n2, n = row.get("n_group1"), row.get("n_group2"), row.get("n_genes_tested")
    if not g1 or not g2 or n1 is None or n2 is None or not n:
        return (
            lead
            + "only large changes survive the adjustment for testing every gene at once."
        )
    genes = f"{n:,} {'gene' if n == 1 else 'genes'}"
    return (
        lead
        + f"with {n1:,} {text(g1)} and {n2:,} {text(g2)} cells, only large changes "
        f"survive the adjustment for testing {genes} at once."
    )


def _sides(
    reads: Reads, call: Call, row: dict[str, Any], top: int
) -> tuple[list[Side], str | None]:
    """The comparison's genes, by the group each is higher in, or why there are none."""
    if not de.is_tested(row):
        return [], NOT_TESTED
    if call.late():
        call.note(OUT_OF_TIME + " Ask for fewer cell types or one contrast.")
        return [], OUT_OF_TIME
    if call.comparisons_read >= call.max_comparisons:
        call.note(
            f"Genes are read for at most {call.max_comparisons} comparisons per call; the "
            "others are not read. Ask for one contrast or fewer cell types."
        )
        return [], PAST_LIMIT
    call.comparisons_read += 1
    found = de.de_genes(reads, row, top) or {}
    higher = dict(zip(de.DIRECTIONS, group_names(row), strict=True))
    sides = [
        Side(
            higher_in=higher[direction],
            passing=passing,
            genes=[
                DeGene(
                    **_gene(g),
                    pct_in_group=g["pct_1"] if direction == "up" else g["pct_2"],
                    pct_in_other=g["pct_2"] if direction == "up" else g["pct_1"],
                )
                for g in genes
            ],
        )
        for direction, (genes, passing) in found.items()
    ]
    if all(s.passing == 0 for s in sides):
        return sides, _not_evidence(row)
    return sides, None


def comparison(
    reads: Reads, call: Call, row: dict[str, Any], methods: dict[int, str], top: int
) -> Comparison:
    sides, note = _sides(reads, call, row, top)
    return Comparison(
        contrast=text(row.get("contrast")),
        group1=text(row.get("group1")),
        group2=text(row.get("group2")),
        cells_group1=row.get("n_group1"),
        cells_group2=row.get("n_group2"),
        method=text(methods.get(row["run_id"])),
        tested=de.is_tested(row),
        sides=sides,
        note=note,
    )
