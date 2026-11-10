"""The gene lookup's reads: the gene, its HPI notes, orthologs and translation work.

Each read goes through the table reader as bloom_agent, with only the columns in COLUMNS,
so the allow-list, hidden columns and deleted-row rules hold as for query_table. The
expression reads are in gene_expression.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import table_access
from .table_ports import BloomDataReader, Filter, Sort

# The website's cuts: a marker is up in its cluster below MARKER_FDR_CUT (cluster-markers.ts);
# a DE gene passes DE_FDR_CUT and |log2fc| > DE_LOG2FC_CUT (de-types.ts); past
# OFF_SCALE_LOG2FC the gene is missing from one group (the volcano plot's off-scale mark).
MARKER_FDR_CUT = 0.05
DE_FDR_CUT = 0.05
DE_LOG2FC_CUT = 0.5
OFF_SCALE_LOG2FC = 20

# Most genes one name may match; most orthologs listed per orthogroup; newest progress shown.
MAX_GENES = 20
MAX_ORTHOLOGS = 200
MAX_PROGRESS = 10
# Most rows one read returns before the lookup says its answer is incomplete.
MAX_ROWS = 1000
# PostgREST's in list stays short enough for the gateway's URL limit.
IN_CHUNK = 200

# An Arabidopsis id with its annotation suffix: AT4G28110.Araport11.447 -> AT4G28110.
_AGI = re.compile(r"^(AT[1-5CM]G\d{5})(\..+)?$", re.IGNORECASE)
_BARE_AGI = re.compile(r"^AT[1-5CM]G\d{5}$", re.IGNORECASE)
# A transcript number on the end of an id: AT4G28110.1, Glyma.01G000100.2.
_TRANSCRIPT = re.compile(r"\.\d+$")

COLUMNS: dict[str, tuple[str, ...]] = {
    "genes": (
        "gene_id",
        "symbol",
        "standard_name",
        "short_description",
        "ortho_group",
        "reference_id",
    ),
    "assemblies": ("id", "prefix", "species_id"),
    "gene_candidates": (
        "gene",
        "status",
        "category",
        "evidence_description",
        "experiment_plans_and_progress",
        "publication_status",
        "disclosed_to_otd",
        "translation_approval_date",
    ),
    "gene_candidate_scientists": ("id", "gene_candidate_id", "scientist_id"),
    "people": ("id", "name"),
    "experiment_progress_logs": ("id", "gene", "timestamp", "message"),
    "translation_candidates": ("id", "gene_candidate", "translation_candidate"),
    "translation_lines": (
        "id",
        "gene_id",
        "name",
        "accession_id",
        "modification_description",
    ),
    "accessions": ("id", "name"),
    "scrna_datasets": ("id", "name", "species_id", "kind"),
    "scrna_genes": ("id", "dataset_id", "gene_name"),
    "scrna_de_runs": ("id", "dataset_id", "completed_at"),
    "scrna_de": (
        "id",
        "run_id",
        "dataset_id",
        "group_kind",
        "cluster_id",
        "group1",
        "group2",
        "n_group1",
        "n_group2",
    ),
    "scrna_de_genes": ("id", "de_id", "gene_id", "log2fc", "fdr", "pct_1", "pct_2"),
    "scrna_clusters": ("id", "dataset_id", "cluster_id", "name", "source"),
    "scrna_genotypes": ("id", "dataset_id", "name", "construct"),
}


@dataclass
class Reads:
    """One lookup's reads, and whether any came back cut at MAX_ROWS."""

    reader: BloomDataReader
    incomplete: set[str] = field(default_factory=set)

    def page(
        self,
        table: str,
        filters: list[Filter],
        order: list[Sort] | None = None,
        limit: int = MAX_ROWS,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Up to `limit` rows, and whether more matched."""
        order = order or [Sort(c) for c in table_access.paging_key(table)]
        found = self.reader.select(
            table, list(COLUMNS[table]), filters, order, limit + 1, 0
        )
        return found[:limit], len(found) > limit

    def rows(self, table: str, filters: list[Filter]) -> list[dict[str, Any]]:
        """Every matching row, up to MAX_ROWS; past that the answer is marked incomplete."""
        found, more = self.page(table, filters)
        if more:
            self.incomplete.add(table)
        return found

    def rows_in(
        self,
        table: str,
        column: str,
        values: list[Any],
        filters: tuple[Filter, ...] = (),
    ) -> list[dict[str, Any]]:
        """Rows whose `column` is one of `values`, read in chunks the gateway accepts."""
        unique = list(dict.fromkeys(v for v in values if v is not None))
        found: list[dict[str, Any]] = []
        for start in range(0, len(unique), IN_CHUNK):
            chunk = unique[start : start + IN_CHUNK]
            found += self.rows(table, [Filter(column, "in", chunk), *filters])
        return found


def agi(gene_id: str) -> str | None:
    """The plain AGI id scRNA datasets use for an Arabidopsis gene id, else None."""
    match = _AGI.match(gene_id)
    return match.group(1).upper() if match else None


def stem(name: str) -> str:
    """`name` without a transcript number on the end: AT4G28110.1 -> AT4G28110."""
    return _TRANSCRIPT.sub("", name)


def bare_agi(name: str) -> str | None:
    """The AGI id `name` is, once its transcript number is dropped, else None."""
    trimmed = stem(name)
    return trimmed.upper() if _BARE_AGI.match(trimmed) else None


def find_genes(reads: Reads, name: str) -> list[dict[str, Any]]:
    """Genes whose id or symbol is `name` or its stem, ignoring case; an AGI id also finds
    its annotated id (AT4G28110 -> AT4G28110.Araport11.447). Whole values only, so MYB41
    never matches MYB410.

    Not standard_name: it holds HPI line names (ATC2-myb41-1 is on MYB74).
    """
    searches = [
        Filter(column, "iequals", value)
        for value in dict.fromkeys([name, stem(name)])
        for column in ("gene_id", "symbol")
    ]
    if agi_id := bare_agi(name):
        searches.append(Filter("gene_id", "istarts_with", agi_id + "."))
    found: dict[str, dict[str, Any]] = {}
    for search in searches:
        for row in reads.rows("genes", [search]):
            found.setdefault(row["gene_id"], row)
    return sorted(found.values(), key=lambda r: r["gene_id"])[:MAX_GENES]


def assembly_prefixes(reads: Reads, genes: list[dict[str, Any]]) -> dict[int, str]:
    rows = reads.rows_in("assemblies", "id", [g.get("reference_id") for g in genes])
    return {r["id"]: r["prefix"] for r in rows if r.get("prefix")}


def hpi_notes(reads: Reads, gene_ids: list[str]) -> list[dict[str, Any]]:
    """Live gene candidates, each with its scientists' names and newest progress."""
    candidates = reads.rows_in(
        "gene_candidates", "gene", gene_ids, (Filter("deleted_at", "is_null", True),)
    )
    if not candidates:
        return []
    genes = [c["gene"] for c in candidates]
    links = reads.rows_in("gene_candidate_scientists", "gene_candidate_id", genes)
    people = reads.rows_in("people", "id", [link["scientist_id"] for link in links])
    names = {p["id"]: p["name"] for p in people}
    notes = []
    for candidate in candidates:
        progress, _ = reads.page(
            "experiment_progress_logs",
            [Filter("gene", "eq", candidate["gene"])],
            [Sort("timestamp", descending=True), Sort("id")],
            limit=MAX_PROGRESS,
        )
        scientists = sorted(
            {
                names[link["scientist_id"]]
                for link in links
                if link["gene_candidate_id"] == candidate["gene"]
                and names.get(link["scientist_id"])
            }
        )
        for entry in progress:
            entry["message"] = table_access.redact_emails(entry.get("message"))
        notes.append({**candidate, "scientists": scientists, "progress": progress})
    return notes


def orthogroups(
    reads: Reads, groups: list[str], prefixes: dict[int, str]
) -> list[dict[str, Any]]:
    """Each orthogroup's genes by assembly, as the Genes page lists them."""
    result = []
    for group in dict.fromkeys(groups):
        members, more = reads.page(
            "genes", [Filter("ortho_group", "eq", group)], limit=MAX_ORTHOLOGS
        )
        prefixes.update(assembly_prefixes(reads, members))
        by_assembly: dict[str, list[str]] = {}
        for gene in members:
            assembly = prefixes.get(gene.get("reference_id"), "unknown assembly")
            by_assembly.setdefault(assembly, []).append(gene["gene_id"])
        result.append(
            {
                "ortho_group": group,
                "by_assembly": by_assembly,
                "total": len(members),
                "complete": not more,
            }
        )
    return result


def translation(
    reads: Reads, gene_ids: list[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The genes' crop translation candidates (with their gene rows) and modified lines."""
    pairs = reads.rows_in("translation_candidates", "gene_candidate", gene_ids)
    targets = reads.rows_in(
        "genes", "gene_id", [p["translation_candidate"] for p in pairs]
    )
    by_id = {g["gene_id"]: g for g in targets}
    candidates = [
        {**p, "gene": by_id.get(p["translation_candidate"])}
        for p in pairs
        if p.get("translation_candidate")
    ]
    lines = reads.rows_in("translation_lines", "gene_id", gene_ids)
    accessions = reads.rows_in("accessions", "id", [ln["accession_id"] for ln in lines])
    names = {a["id"]: a["name"] for a in accessions}
    for line in lines:
        line["accession"] = names.get(line.get("accession_id"))
    return candidates, lines
