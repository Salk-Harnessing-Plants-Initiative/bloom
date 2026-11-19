"""The gene lookup tests' fake Bloom: one Arabidopsis gene (MYB41), its line-name
neighbour MYB74, a soybean ortholog and an scRNA dataset with two analysis runs."""

from __future__ import annotations

from bloom_mcp.data_access.fake_table_reader import FakeTableReader
from bloom_mcp.sections.bloom_data.gene_lookup import gene_lookup
from bloom_mcp.tools import _ports

MYB41 = "AT4G28110.Araport11.447"


MYB74 = "AT4G05100.Araport11.447"


SOY = "Gmax.W82a5.HPI3.Chr19.g456830"


MYB410 = "AT4G28111.Araport11.447"


def _de(id_, run, kind, group1, group2, n1, n2, cluster=None, tested=True):
    return {
        "id": id_, "run_id": run, "dataset_id": 14, "group_kind": kind,
        "cluster_id": cluster, "group1": group1, "group2": group2,
        "n_group1": n1, "n_group2": n2, "tested": tested,
    }  # fmt: skip


def _hit(de_id, log2fc, fdr):
    return {"de_id": de_id, "gene_id": 100, "log2fc": log2fc, "fdr": fdr,
            "pct_1": 0.8, "pct_2": 0.1, "dataset_id": 14}  # fmt: skip


TABLES = {
    "genes": [
        {"gene_id": MYB41, "symbol": "MYB41", "standard_name": "ATC2-myb41-3",
         "short_description": "myb domain protein 41", "ortho_group": "OG0001664",
         "reference_id": 1},
        {"gene_id": MYB74, "symbol": "MYB74", "standard_name": "ATC2-myb41-1",
         "short_description": None, "ortho_group": "OG0001664", "reference_id": 1},
        {"gene_id": SOY, "symbol": None, "standard_name": None,
         "short_description": None, "ortho_group": "OG0001664", "reference_id": 2},
        {"gene_id": MYB410, "symbol": "MYB410", "standard_name": None,
         "short_description": None, "ortho_group": None, "reference_id": 1},
    ],
    "assemblies": [
        {"id": 1, "prefix": "Athaliana.Col-0", "species_id": 1},
        {"id": 2, "prefix": "Gmax.W82a5", "species_id": 2},
    ],
    "gene_candidates": [
        {"gene": MYB41, "status": "in-translation", "category": "Suberin",
         "evidence_description": "Suberin in roots", "experiment_plans_and_progress": None,
         "publication_status": False, "disclosed_to_otd": True,
         "translation_approval_date": None, "deleted_at": None},
        {"gene": MYB74, "status": "suspected", "category": None,
         "evidence_description": None, "experiment_plans_and_progress": None,
         "publication_status": None, "disclosed_to_otd": None,
         "translation_approval_date": None, "deleted_at": "2026-01-01T00:00:00Z"},
    ],
    "gene_candidate_scientists": [{"id": 1, "gene_candidate_id": MYB41, "scientist_id": 1}],
    "people": [{"id": 1, "name": "Ada", "email": "ada@salk.edu"}],
    "experiment_progress_logs": [
        {"id": 1, "gene": MYB41, "timestamp": "2026-02-01T00:00:00Z",
         "message": "Lines sent", "user_email": "ada@salk.edu"},
        {"id": 2, "gene": MYB41, "timestamp": "2026-03-01T00:00:00Z",
         "message": "T2 seeds, ask ada@salk.edu", "user_email": "ada@salk.edu"},
    ],
    "translation_candidates": [{"id": 1, "gene_candidate": MYB41, "translation_candidate": SOY}],
    "translation_lines": [
        {"id": 1, "gene_id": MYB41, "name": "myb41-oe", "accession_id": 5,
         "modification_description": "overexpression"},
    ],
    "accessions": [{"id": 5, "name": "Col-0"}],
    "scrna_datasets": [
        {"id": 14, "name": "MYB41 roots", "species_id": 1, "kind": "full", "deleted_at": None},
        {"id": 16, "name": "Atlas", "species_id": 1, "kind": "reference", "deleted_at": None},
    ],
    "scrna_genes": [
        {"id": 100, "dataset_id": 14, "gene_name": "AT4G28110", "gene_number": 1},
        {"id": 200, "dataset_id": 15, "gene_name": "AT4G28110", "gene_number": 1},
        {"id": 300, "dataset_id": 16, "gene_name": "AT1G01010", "gene_number": 1},
    ],
    "scrna_de_runs": [
        {"id": 1, "dataset_id": 14, "status": "complete", "completed_at": "2026-01-01"},
        {"id": 2, "dataset_id": 14, "status": "complete", "completed_at": "2026-02-01"},
        {"id": 3, "dataset_id": 14, "status": "failed", "completed_at": None},
    ],
    "scrna_de": [
        _de(10, 1, "cluster", "c3", "rest", 50, 500, cluster="c3"),
        _de(11, 2, "cluster", "c4", "rest", 60, 490, cluster="c4"),
        _de(12, 1, "cluster", "c4", "rest", 60, 490, cluster="c4"),
        _de(13, 3, "cluster", "c3", "rest", 50, 500, cluster="c3"),
        _de(14, 2, "genotype", "pFACT", "Col-0", 300, 300),
        _de(15, 2, "cluster", "c5", "rest", 40, 510, cluster="c5"),
        _de(16, 2, "cluster", "c4", "rest", 10, 100, cluster="c4"),
        _de(17, 1, "genotype", "old", "Col-0", 300, 300),
    ],
    "scrna_de_genes": [
        _hit(10, 2.0, 0.001), _hit(11, 1.5, 0.01), _hit(12, 3.0, 1e-5), _hit(13, 4.0, 1e-6),
        _hit(14, 1.2, 0.001), _hit(15, -2.0, 0.001), _hit(16, 9.0, 1e-9),
        _hit(17, 5.0, 1e-9),
    ],
    "scrna_clusters": [
        {"id": 1, "dataset_id": 14, "cluster_id": "c3", "name": "Endodermis", "source": "nuclei"},
        {"id": 2, "dataset_id": 14, "cluster_id": "c4", "name": "Cortex", "source": None},
        {"id": 3, "dataset_id": 14, "cluster_id": "c5", "name": "Xylem", "source": None},
    ],
    "scrna_cluster_stats": [],
    "scrna_genotypes": [
        {"id": 1, "dataset_id": 14, "name": "pFACT", "construct": "pFACT::MYB41"},
        {"id": 2, "dataset_id": 14, "name": "Col-0", "construct": None},
    ],
}  # fmt: skip


def lookup(gene: str):
    return gene_lookup({"gene": gene})


def _set_hit(db, de_id, **values):
    next(r for r in db.tables["scrna_de_genes"] if r["de_id"] == de_id).update(values)


def lookup_in(fake: FakeTableReader, gene: str):
    """lookup against `fake` instead of the genes_db fixture."""
    previous = _ports.table_reader()
    _ports.configure(table_reader=fake)
    try:
        return lookup(gene)
    finally:
        _ports.configure(table_reader=previous)
