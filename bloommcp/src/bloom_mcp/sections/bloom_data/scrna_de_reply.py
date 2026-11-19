"""scrna_de's reply, and trimming it to fit one reply."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

FIT_NOTE = (
    "Some gene lists were left out to fit one reply (their counts are kept); ask for "
    "fewer cell types, one contrast or a smaller top."
)


class _Gene(BaseModel):
    gene: str | None
    log2fc: float | None = Field(
        description="None when infinite (see found_in_one_group_only)."
    )
    fdr: float
    found_in_one_group_only: bool = Field(
        description="An infinite log2FC: measured in one group only."
    )
    likely_found_in_one_group_only: bool = Field(
        description="|log2FC| above 20: almost certainly measured in one group only."
    )


class MarkerGene(_Gene):
    symbol: str | None
    pct_in_cell_type: float | None = Field(
        description="Share of the cell type's cells expressing it."
    )
    pct_elsewhere: float | None = Field(
        description="Share of the other cells expressing it."
    )
    source: str | None = Field(description="What found it, when stored.")


class DeGene(_Gene):
    pct_in_group: float | None = Field(
        description="Share of cells expressing it in the group it is higher in."
    )
    pct_in_other: float | None = Field(
        description="Share of cells expressing it in the other group."
    )


class Markers(BaseModel):
    source: Literal["stored", "comparison"] = Field(
        description="stored: the dataset's saved markers; comparison: the top of the cell "
        "type's one-vs-rest comparison, as the panel shows without saved ones."
    )
    significant: int | None = Field(
        description="Significant markers in all; None when unknown."
    )
    genes: list[MarkerGene] = Field(description="The panel's list, in its order.")


class Side(BaseModel):
    higher_in: str = Field(description="The group these genes are higher in.")
    passing: int | None = Field(description="Genes passing the cuts in all.")
    genes: list[DeGene] = Field(description="The top genes, ranked as the DE tab.")


class Comparison(BaseModel):
    contrast: str | None
    group1: str | None
    group2: str | None
    cells_group1: int | None
    cells_group2: int | None
    method: str | None = Field(description="The DE run's method.")
    tested: bool
    sides: list[Side]
    note: str | None = Field(
        description="Why there are no genes, or what none passing means."
    )


class CellType(BaseModel):
    cluster_id: str
    name: str | None
    markers: Markers | None
    genotype: list[Comparison] = Field(
        description="Genotype comparisons in this cell type."
    )
    note: str | None = Field(
        description="Why markers or genotype comparisons are missing."
    )


class ScrnaDeResult(BaseModel):
    dataset_id: int
    dataset_name: str
    de_tab_url: str | None = Field(description="The dataset's DE tab.")
    cell_types_url: str | None = Field(
        description="The dataset's cell types; clicking one opens its marker panel."
    )
    cell_types: list[CellType]
    whole_dataset: list[Comparison] = Field(
        description="Genotype comparisons over every cell, when no cell type is named."
    )
    contrasts: list[str] = Field(
        description="The genotype contrasts these cell types have, for the contrast input."
    )
    unknown_cell_types: list[str] = Field(description="Names that match no cell type.")
    left_out_cell_types: list[str] = Field(
        description="Cluster ids not in this reply: past the cell type limit or left out "
        "to fit one reply. Ask for them by name."
    )
    incomplete: bool = Field(description="Some read hit Bloom's row limit.")
    notes: list[str]


def fit(result: ScrnaDeResult, limit: int) -> ScrnaDeResult:
    """`result` cut to `limit` characters: gene lists emptied (cell types' genotype genes
    from the last back, then the whole dataset's, then markers), then cell types left out
    from the last back, then whole-dataset comparisons."""
    if _size(result) <= limit:
        return result
    result.notes.append(FIT_NOTE)
    cell_sides = [s for t in result.cell_types for c in t.genotype for s in c.sides]
    whole_sides = [s for c in result.whole_dataset for s in c.sides]
    markers = [t.markers for t in result.cell_types if t.markers]
    for holder in [*reversed(cell_sides), *reversed(whole_sides), *reversed(markers)]:
        if _size(result) <= limit:
            return result
        holder.genes = []
    # Dropped from the last back, so each goes in front: page order is kept.
    while result.cell_types and _size(result) > limit:
        result.left_out_cell_types.insert(0, result.cell_types.pop().cluster_id)
    while result.whole_dataset and _size(result) > limit:
        result.whole_dataset.pop()
    return result


def _size(result: ScrnaDeResult) -> int:
    return len(result.model_dump_json())
