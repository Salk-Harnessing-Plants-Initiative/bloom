"""The DE reads tests' fake Bloom: dataset 14 with 12 cell types (two named Cortex), two
complete runs and a failed one, and gene rows built for ties, NaN and infinity."""

from __future__ import annotations

import copy

from bloom_mcp.data_access.fake_table_reader import FakeTableReader
from bloom_mcp.data_access.gene_reads import Reads


def _cluster(n, name):
    return {"id": n, "dataset_id": 14, "cluster_id": f"c{n}", "name": name,
            "source": None, "ordinal": 12 - n}  # fmt: skip


def _de(id_, run, kind, group1, group2, cluster, n1=50, n2=500, contrast=None,tested=True, n_tested=2000):  # fmt: skip
    return {"id": id_, "run_id": run, "dataset_id": 14, "group_kind": kind,
            "cluster_id": cluster, "contrast": contrast, "group1": group1,
            "group2": group2, "n_group1": n1, "n_group2": n2, "tested": tested,
            "n_genes_tested": n_tested}  # fmt: skip


def _gene(id_, de_id, gene_id, log2fc, fdr):
    return {"id": id_, "de_id": de_id, "dataset_id": 14, "gene_id": gene_id,
            "log2fc": log2fc, "pvalue": fdr / 10, "fdr": fdr, "pct_1": 0.8,
            "pct_2": 0.1}  # fmt: skip


NAMES = ["Cortex", "Endodermis", "Xylem", "Cortex", "Phloem", "Epidermis", "Root cap",
         "Pericycle", "Columella", "Trichoblast", "Atrichoblast", None]  # fmt: skip
# Ordinal is 12 - n, so the website's order is c12, c11, ... c1.
CLUSTERS = [_cluster(n, NAMES[n - 1]) for n in range(1, 13)]

# Gene names chosen so name order differs from id order: id 1 is ZETA.
GENE_NAMES = {1: "ZETA", 2: "ALPHA", 3: "MID", 4: "BETA", 5: "NANGENE", 6: "INFGENE",
              7: "BIGGENE", 8: "EDGE", 9: "DOWN1", 10: "DOWN2", 11: "WEAK",
              12: "LATE", 13: "atMYB41", 14: "ATZ1", 15: "ATMYB41"}  # fmt: skip

TABLES = {
    "scrna_datasets": [
        {
            "id": 14,
            "name": "MYB41 roots",
            "species_id": 1,
            "kind": "full",
            "deleted_at": None,
        },
        {
            "id": 15,
            "name": "Old roots",
            "species_id": 1,
            "kind": "full",
            "deleted_at": "2026-05-01T00:00:00Z",
        },
        {
            "id": 16,
            "name": "Root atlas",
            "species_id": 1,
            "kind": "reference",
            "deleted_at": None,
        },
    ],  # fmt: skip
    "scrna_clusters": CLUSTERS,
    "scrna_genes": [
        {"id": i, "dataset_id": 14, "gene_name": n} for i, n in GENE_NAMES.items()
    ],
    "scrna_de_runs": [
        {
            "id": 1,
            "dataset_id": 14,
            "status": "complete",
            "completed_at": "2026-01-01",
            "method": "wilcoxon",
        },
        {
            "id": 2,
            "dataset_id": 14,
            "status": "complete",
            "completed_at": "2026-02-01",
            "method": "pseudobulk-edger",
        },
        {
            "id": 3,
            "dataset_id": 14,
            "status": "failed",
            "completed_at": None,
            "method": "wilcoxon",
        },
    ],  # fmt: skip
    "scrna_de": [
        # One-vs-rest: c1 in run 1 only; c4 in run 2 twice (100 is larger) and run 1.
        _de(100, 2, "cluster", "c4", "rest", "c4", n1=60, n2=490),
        _de(101, 2, "cluster", "c4", "rest", "c4", n1=10, n2=100),
        _de(102, 1, "cluster", "c4", "rest", "c4"),
        _de(103, 1, "cluster", "c1", "rest", "c1"),
        _de(104, 3, "cluster", "c2", "rest", "c2"),
        _de(105, 2, "cluster", "c2", "rest", "c2", tested=False),
        # c3: tested in run 1, untested in the newer run 2 -> run 1's (106).
        _de(106, 1, "cluster", "c3", "rest", "c3"),
        _de(107, 2, "cluster", "c3", "rest", "c3", tested=False),
        # Filed under c5 but comparing c6: not c5's one-vs-rest.
        _de(108, 2, "cluster", "c6", "rest", "c5"),
        # Genotype: c4 tested in run 1, untested in run 2 (run 2 wins); c1 in run 1.
        _de(
            200,
            1,
            "genotype",
            "myb41",
            "Col-0",
            "c4",
            300,
            280,
            contrast="myb41_vs_Col-0",
        ),
        _de(
            201,
            2,
            "genotype",
            "myb41",
            "Col-0",
            "c4",
            3,
            2,
            contrast="myb41_vs_Col-0",
            tested=False,
            n_tested=0,
        ),
        _de(
            202,
            1,
            "genotype",
            "myb41",
            "Col-0",
            "c1",
            200,
            210,
            contrast="myb41_vs_Col-0",
        ),
        _de(
            203,
            2,
            "genotype",
            "oe",
            "Col-0",
            "c1",
            150,
            210,
            contrast="oe_vs_Col-0",
            n_tested=0,
        ),
        _de(
            204,
            1,
            "genotype",
            "myb41",
            "Col-0",
            None,
            900,
            880,
            contrast="myb41_vs_Col-0",
        ),
    ],  # fmt: skip
    "scrna_de_genes": [
        # c4's one-vs-rest (100): three up genes tie at 0.001; NaN and infinity cases.
        _gene(1, 100, 1, 2.0, 0.001),
        _gene(2, 100, 2, 1.0, 0.001),
        _gene(3, 100, 3, 3.0, 0.001),
        _gene(4, 100, 4, 1.5, 0.0001),
        _gene(5, 100, 5, "NaN", 0.00001),
        _gene(6, 100, 6, "Infinity", 0.002),
        _gene(7, 100, 7, 25.0, 0.003),
        _gene(8, 100, 8, 20.0, 0.004),
        _gene(9, 100, 9, -1.0, 0.01),
        _gene(10, 100, 10, "-Infinity", 0.02),
        _gene(11, 100, 11, 0.4, 0.0001),
        _gene(12, 100, 12, 9.0, 0.06),
        # c1's genotype comparison (202): up MID and ZETA tie with down ALPHA at 0.001;
        # BETA sits on the log2FC cut and LATE on the FDR cut, so neither passes.
        _gene(20, 202, 3, 0.9, 0.001),
        _gene(21, 202, 1, 1.2, 0.001),
        _gene(22, 202, 2, -1.0, 0.001),
        _gene(23, 202, 9, -2.0, 0.002),
        # The whole-dataset comparison (204): mixed-case names tied at 0.001.
        _gene(40, 204, 14, 1.0, 0.001),
        _gene(41, 204, 13, 1.0, 0.001),
        _gene(42, 204, 15, 1.0, 0.001),
        _gene(24, 202, 4, 0.5, 0.001),
        _gene(25, 202, 12, 3.0, 0.05),
        _gene(26, 202, 10, -0.5, 0.001),  # DOWN2 on the down cut
        # c1's one-vs-rest (103), for the panel's fallback: atMYB41, ZETA and MID are
        # markers, atMYB41 first by FDR despite its small log2FC; ALPHA is down, WEAK at
        # log2FC 0, LATE on the FDR cut and BETA past it.
        _gene(30, 103, 3, 0.3, 0.01),
        _gene(31, 103, 1, 2.0, 0.01),
        _gene(32, 103, 2, -1.0, 0.001),
        _gene(33, 103, 4, 1.0, 0.06),
        _gene(34, 103, 13, 0.2, 0.001),
        _gene(35, 103, 11, 0.0, 0.0001),
        _gene(36, 103, 12, 1.0, 0.05),
    ],
    "scrna_cluster_stats": [
        {
            "dataset_id": 14,
            "cluster_id": "c4",
            "markers": {
                "top": [
                    {
                        "gene": "BETA",
                        "log2fc": 1.5,
                        "q": 0.0001,
                        "pct_1": 0.8,
                        "pct_2": 0.1,
                        "symbol": "B1",
                        "source": "wilcoxon",
                    },
                    {"log2fc": 9.0, "q": 0.0},
                    {"gene": "BARE"},
                    {
                        "gene": "HUGE",
                        "log2fc": 27.0,
                        "q": 0.001,
                        "pct_1": True,
                        "symbol": 5,
                        "source": ["x"],
                    },
                ],
                "n_significant": 7.0,
            },
        },
        {"dataset_id": 14, "cluster_id": "c7", "markers": None},
        {
            "dataset_id": 14,
            "cluster_id": "c1",
            "markers": {"top": [], "n_significant": 0},
        },
    ],  # fmt: skip
}


def reads() -> Reads:
    """Reads over a fresh copy of TABLES."""
    return Reads(FakeTableReader(copy.deepcopy(TABLES)))


def comparison(de_id: int) -> dict:
    """The comparison row `de_id`."""
    return next(c for c in TABLES["scrna_de"] if c["id"] == de_id)
