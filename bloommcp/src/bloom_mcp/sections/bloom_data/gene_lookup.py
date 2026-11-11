"""gene_lookup — what Bloom knows about one gene, in one call."""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field

from bloom_mcp.contract import as_mcp_tool
from bloom_mcp.data_access import gene_expression, gene_reads
from bloom_mcp.data_access.page_links import PAGE_LINKS
from bloom_mcp.tools import _ports

from ._common import fix_request, logs_call, reports_lookup_errors
from ._page import MAX_CELL_CHARS, MAX_REPLY_CHARS, TRUNCATED

GENES_PAGE = "/app/genes"
ORTHOBROWSER_PAGE = "/app/orthofinder?search={search}"


class GeneLookupParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gene: str = Field(
        max_length=200,
        description="A gene symbol (MYB41), an AGI id (AT4G28110) or a full Bloom gene id.",
    )


class GeneMatch(BaseModel):
    gene_id: str
    symbol: str | None
    standard_name: str | None
    short_description: str | None
    ortho_group: str | None
    assembly: str | None


class Progress(BaseModel):
    timestamp: str | None
    message: str | None


class HpiNote(BaseModel):
    gene: str
    status: str | None
    category: str | None
    evidence_description: str | None
    experiment_plans_and_progress: str | None
    publication_status: bool | None
    disclosed_to_otd: bool | None
    translation_approval_date: str | None
    scientists: list[str]
    progress: list[Progress] = Field(description="Newest first.")


class Orthogroup(BaseModel):
    ortho_group: str
    by_assembly: dict[str, list[str]]
    total: int
    complete: bool = Field(
        description="False when the orthogroup has more genes than listed."
    )


class TranslationCandidate(BaseModel):
    gene_candidate: str
    translation_candidate: str
    symbol: str | None
    ortho_group: str | None


class TranslationLine(BaseModel):
    gene_id: str
    name: str | None
    accession: str | None
    modification: str | None


_LOG2FC = "None when infinite: the gene is absent from one group (absent_in_one_group)."


class Marker(BaseModel):
    cluster_id: str
    cell_type: str | None
    label_source: str | None
    log2fc: float | None = Field(description=_LOG2FC)
    absent_in_one_group: bool
    fdr: float
    pct_in_cluster: float | None = Field(
        description="Share of the cluster's cells, 0–1."
    )
    pct_elsewhere: float | None = Field(description="Share of the other cells, 0–1.")
    on_cluster_panel: bool = Field(
        description="Whether the cluster's panel on the website lists the gene: it shows "
        "only the top 25 markers."
    )


class GenotypeComparison(BaseModel):
    cluster_id: str | None = Field(
        description="The cluster compared within; null for the whole dataset."
    )
    cell_type: str | None
    group1: str | None
    group2: str | None
    log2fc: float | None = Field(description=_LOG2FC)
    fdr: float
    direction: str = Field(description="up or down in group1.")
    absent_in_one_group: bool


class DatasetHit(BaseModel):
    dataset_id: int
    name: str
    kind: str | None = Field(description="full, or reference for a published atlas.")
    gene_name: str | None = Field(
        description="The gene's name in the dataset; null if not measured."
    )
    link: str | None
    constructs: list[str] = Field(description="Genotype constructs naming the gene.")
    markers: list[Marker]
    genotype_comparisons: list[GenotypeComparison]


class Links(BaseModel):
    genes_page: str | None
    orthobrowser: str | None


class GeneLookupResult(BaseModel):
    query: str
    genes: list[GeneMatch]
    hpi_notes: list[HpiNote]
    orthologs: list[Orthogroup]
    translation_candidates: list[TranslationCandidate]
    translation_lines: list[TranslationLine]
    expression: list[DatasetHit]
    links: Links
    notes: list[str]


def _site() -> str:
    return (os.environ.get("SITE_URL") or "").strip().rstrip("/")


def _dataset_link(site: str, dataset: dict[str, Any]) -> str | None:
    if not site or dataset.get("species_id") is None:
        return None
    return site + PAGE_LINKS["scrna_datasets"].format(**dataset)


def _links(site: str, genes: list[dict[str, Any]], query: str) -> Links:
    if not site:
        return Links(genes_page=None, orthobrowser=None)
    first = genes[0] if genes else {}
    term = first.get("symbol") or first.get("gene_id") or query
    search = quote(term.lower(), safe="")
    return Links(
        genes_page=site + GENES_PAGE,
        orthobrowser=site + ORTHOBROWSER_PAGE.format(search=search),
    )


def _notes(
    reads: gene_reads.Reads, genes: list[dict[str, Any]], expression: list
) -> list[str]:
    notes = []
    if not genes:
        notes.append(
            "No gene in Bloom's gene table has this id or symbol; results come from the "
            "scRNA datasets only."
        )
    if not genes and not expression:
        notes.append("Bloom has nothing on this gene.")
    if reads.incomplete:
        notes.append(
            "Some lists are incomplete: too many rows matched in "
            + ", ".join(sorted(reads.incomplete))
            + "."
        )
    return notes


# The answer's lists, halved longest first when trimming progress isn't enough.
_LISTS = (
    "genes",
    "hpi_notes",
    "orthologs",
    "translation_candidates",
    "translation_lines",
    "expression",
)
# Room left for the note that says what was shortened.
_NOTE_ROOM = 1000


def _cut_text(value: Any, limit: int) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + TRUNCATED
    if isinstance(value, list):
        return [_cut_text(v, limit) for v in value]
    if isinstance(value, dict):
        return {k: _cut_text(v, limit) for k, v in value.items()}
    return value


def _size(value: Any) -> int:
    return len(json.dumps(value, default=str))


def _fit(result: GeneLookupResult) -> GeneLookupResult:
    """`result` with long text cut as query_table cuts it; if the reply is still too long,
    fewer progress entries, then the longest lists halved until it fits."""
    answer = _cut_text(result.model_dump(), MAX_CELL_CHARS)
    budget = MAX_REPLY_CHARS - _NOTE_ROOM
    progress = None
    for keep, words in ((1, "only the newest progress entry"), (0, "no progress")):
        if _size(answer) <= budget or not any(
            n["progress"] for n in answer["hpi_notes"]
        ):
            break
        for note in answer["hpi_notes"]:
            note["progress"] = note["progress"][:keep]
        progress = words
    halved: set[str] = set()
    while _size(answer) > budget:
        longest = max(_LISTS, key=lambda k: _size(answer[k]))
        if not answer[longest]:
            break
        answer[longest] = answer[longest][: len(answer[longest]) // 2]
        halved.add(longest)
    parts = [f"HPI notes list {progress}"] if progress else []
    if halved:
        parts.append(
            "only the first part of " + ", ".join(sorted(halved)) + " is listed"
        )
    if parts:
        answer["notes"].append(
            "Shortened to fit one reply (long text ends in …[cut]): "
            + "; ".join(parts)
            + ". Read the rest with query_table."
        )
    return GeneLookupResult.model_validate(answer)


@as_mcp_tool(input_model=GeneLookupParams, output_model=GeneLookupResult)
@logs_call
@reports_lookup_errors
def gene_lookup(params: GeneLookupParams) -> GeneLookupResult:
    """Everything Bloom has on one gene: its record, HPI notes, orthologs, translation work,
    and the scRNA datasets that measured it, with the clusters (cell types) it marks.

    Give a symbol (MYB41), an AGI id (AT4G28110) or a full Bloom gene id. A marker cluster
    is one where the gene is significantly up against the rest of the cells in the newest
    analysis. A cluster's panel on the website lists only its top 25 markers; when
    on_cluster_panel is false, say the gene is a marker but ranks outside that top 25, and
    that the full list is in the dataset page's differential expression results. Genotype
    comparisons combine the analyses as that page does. Give the links: dataset pages, the
    Genes page for HPI notes, and the OrthoBrowser for orthologs.

    Lead with what Bloom has. You may add what you know about the gene and look up papers
    on it, especially from Salk's Harnessing Plants Initiative (HPI); label all of that as
    not from Bloom and cite each source.
    """
    query = params.gene.strip()
    if not query:
        raise fix_request(
            "Give a gene symbol or id.", "For example MYB41 or AT4G28110."
        )
    reads = gene_reads.Reads(_ports.table_reader())
    site = _site()

    genes = gene_reads.find_genes(reads, query)
    gene_ids = [g["gene_id"] for g in genes]
    prefixes = gene_reads.assembly_prefixes(reads, genes)
    notes = gene_reads.hpi_notes(reads, gene_ids)
    candidates, lines = gene_reads.translation(reads, gene_ids)
    groups = [g["ortho_group"] for g in genes] + [
        c["gene"]["ortho_group"] for c in candidates if c["gene"]
    ]
    orthologs = gene_reads.orthogroups(reads, [g for g in groups if g], prefixes)

    agi_id = gene_reads.bare_agi(query)
    names = {gene_reads.agi(i) or i for i in gene_ids} or {
        query,
        query.upper(),
        *([agi_id] if agi_id else []),
    }
    terms = {g["symbol"] for g in genes if g.get("symbol")} | {
        n for n in names if gene_reads.agi(n)
    }
    found = gene_expression.datasets(reads, sorted(names), sorted(terms))
    gene_expression.add_results(reads, found)

    return _fit(
        GeneLookupResult(
            query=query,
            genes=[
                GeneMatch(
                    **{k: g.get(k) for k in GeneMatch.model_fields if k != "assembly"},
                    assembly=prefixes.get(g.get("reference_id")),
                )
                for g in genes
            ],
            hpi_notes=[HpiNote(**n) for n in notes],
            orthologs=[Orthogroup(**o) for o in orthologs],
            translation_candidates=[
                TranslationCandidate(
                    gene_candidate=c["gene_candidate"],
                    translation_candidate=c["translation_candidate"],
                    symbol=(c["gene"] or {}).get("symbol"),
                    ortho_group=(c["gene"] or {}).get("ortho_group"),
                )
                for c in candidates
            ],
            translation_lines=[
                TranslationLine(
                    gene_id=ln["gene_id"],
                    name=ln.get("name"),
                    accession=ln.get("accession"),
                    modification=ln.get("modification_description"),
                )
                for ln in lines
            ],
            expression=[
                DatasetHit(
                    dataset_id=d["id"],
                    name=d["name"],
                    kind=d.get("kind"),
                    gene_name=d["gene_name"],
                    link=_dataset_link(site, d),
                    constructs=d["constructs"],
                    markers=[Marker(**m) for m in d["markers"]],
                    genotype_comparisons=[
                        GenotypeComparison(**c) for c in d["genotype_comparisons"]
                    ],
                )
                for d in found
            ],
            links=_links(site, genes, query),
            notes=_notes(reads, genes, found),
        )
    )
