"""Live check of bloommcp's scrna_de: as bloom_agent through real PostgREST, it reads a
dataset's markers (stored and from the one-vs-rest comparison) and genotype DE, with
real FDR ties at 0, an infinite fold change and exact counts.

Unit counterpart: bloommcp/tests/sections/bloom_data/test_scrna_de.py.
"""

from __future__ import annotations

import json
import uuid

import pytest

from tests.integration.test_bloommcp_data_tools import _run_in_bloommcp
from tests.integration.test_bloommcp_read_file import _connect

pytestmark = pytest.mark.integration

GENES = ["atMYB41", "ATMYB41", "ATZ1", "BIG", "DOWNX", "G1", "G2", "G3", "G4", "G5"]
STORED = {"top": [{"gene": "ATZ1", "log2fc": 3.0, "q": 0.001}], "n_significant": 1}
# (comparison, gene, log2fc, fdr)
RESULTS = [
    # Cortex's one-vs-rest: G1, G2, G3 are panel markers; G4 has log2FC 0, G5 FDR 0.05.
    ("rest", "G1", 0.2, 0.001),
    ("rest", "G2", 2.0, 0.01),
    ("rest", "G3", 0.3, 0.01),
    ("rest", "G4", 0.0, 0.0001),
    ("rest", "G5", 1.0, 0.05),
    # Whole-dataset genotype: four up genes and one down gene tie at FDR 0.
    ("genotype", "ATZ1", "Infinity", 0.0),
    ("genotype", "BIG", 25.0, 0.0),
    ("genotype", "ATMYB41", 1.0, 0.0),
    ("genotype", "atMYB41", 1.0, 0.0),
    ("genotype", "DOWNX", -1.0, 0.0),
]


def _comparison(cur, ids, cluster, contrast, group1, group2, tested, n_genes):
    cur.execute(
        "INSERT INTO public.scrna_de (dataset_id, run_id, cluster_id, contrast, group1, "
        "group2, n_group1, n_group2, n_genes_tested, group_kind, method, params_hash, "
        "tested) VALUES (%s, %s, %s, %s, %s, %s, 10, 10, %s, %s, 'wilcoxon', 'h1', %s) "
        "RETURNING id",
        (ids["dataset"], ids["run"], cluster, contrast, group1, group2, n_genes,
         "cluster" if group2 == "rest" else "genotype", tested),
    )  # fmt: skip
    return cur.fetchone()[0]


@pytest.fixture(scope="module")
def dataset():
    conn = _connect()
    ids: dict = {}
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO public.species (common_name, genus, species) "
                "VALUES ('ci cress', 'Ci', 'differentialis') RETURNING id"
            )
            ids["species"] = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO public.scrna_datasets (name, species_id) VALUES (%s, %s) "
                "RETURNING id",
                (f"ci-de-{uuid.uuid4().hex[:8]}", ids["species"]),
            )
            ids["dataset"] = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO public.scrna_clusters (dataset_id, cluster_id, ordinal, name) "
                "VALUES (%s, 'C', 0, 'Cortex'), (%s, 'X', 1, 'Xylem')",
                (ids["dataset"], ids["dataset"]),
            )
            cur.execute(
                "INSERT INTO public.scrna_cluster_stats (dataset_id, cluster_id, "
                "cell_count, pct, markers) VALUES (%s, 'X', 2, 0.5, %s)",
                (ids["dataset"], json.dumps(STORED)),
            )
            genes = {}
            for number, name in enumerate(GENES):
                cur.execute(
                    "INSERT INTO public.scrna_genes (dataset_id, gene_number, gene_name) "
                    "VALUES (%s, %s, %s) RETURNING id",
                    (ids["dataset"], number, name),
                )
                genes[name] = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO public.scrna_de_runs (dataset_id, source, status, method, "
                "params_hash, completed_at) VALUES (%s, 'batch', 'complete', 'wilcoxon', "
                "'h1', now()) RETURNING id",
                (ids["dataset"],),
            )
            ids["run"] = cur.fetchone()[0]
            de = {
                "rest": _comparison(cur, ids, "C", "C_vs_rest", "C", "rest", True, 5),
                "genotype": _comparison(
                    cur, ids, None, "myb41_vs_Col-0", "myb41", "Col-0", True, 5
                ),
            }
            _comparison(cur, ids, "C", "myb41_vs_Col-0", "myb41", "Col-0", False, 0)
            for comparison, name, log2fc, fdr in RESULTS:
                cur.execute(
                    "INSERT INTO public.scrna_de_genes (de_id, dataset_id, gene_id, log2fc, "
                    "pvalue, fdr, pct_1, pct_2) VALUES (%s, %s, %s, %s, %s, %s, 0.8, 0.1)",
                    (de[comparison], ids["dataset"], genes[name], log2fc, fdr / 10, fdr),
                )
        yield ids
    finally:
        with conn.cursor() as cur:
            if "dataset" in ids:
                for table in (
                    "scrna_de",
                    "scrna_de_runs",
                    "scrna_cluster_stats",
                    "scrna_genes",
                    "scrna_clusters",
                ):
                    cur.execute(
                        f"DELETE FROM public.{table} WHERE dataset_id = %s",  # noqa: S608
                        (ids["dataset"],),
                    )
                cur.execute(
                    "DELETE FROM public.scrna_datasets WHERE id = %s", (ids["dataset"],)
                )
            if "species" in ids:
                cur.execute(
                    "DELETE FROM public.species WHERE id = %s", (ids["species"],)
                )
        conn.close()


_SCRIPT = """
import json
from bloom_mcp.sections.bloom_data.scrna_de import scrna_de
calls = {{
    "markers": {{"cell_types": ["cortex", "XYLEM"]}},
    "top2": {{"kind": "genotype", "top": 2}},
    "top4": {{"kind": "genotype", "top": 4}},
}}
out = {{k: scrna_de({{"dataset_id": {dataset}, **v}}).model_dump(mode="json")
        for k, v in calls.items()}}
print(json.dumps(out))
"""


@pytest.fixture(scope="module")
def result(dataset) -> dict:
    return _run_in_bloommcp(_SCRIPT.format(dataset=dataset["dataset"]))


def _cell(reply, cluster_id):
    return next(c for c in reply["cell_types"] if c["cluster_id"] == cluster_id)


def _side(comparison, group):
    return next(s for s in comparison["sides"] if s["higher_in"] == group)


def test_markers_come_from_the_comparison_or_the_stored_list(result):
    cortex = _cell(result["markers"], "C")["markers"]
    assert cortex["source"] == "comparison"
    assert [g["gene"] for g in cortex["genes"]] == ["G1", "G2", "G3"]
    assert cortex["significant"] == 3
    xylem = _cell(result["markers"], "X")["markers"]
    assert (xylem["source"], xylem["significant"]) == ("stored", 1)
    assert [g["gene"] for g in xylem["genes"]] == ["ATZ1"]


def test_ties_at_fdr_0_are_cut_in_the_websites_name_order(result):
    [whole] = result["top2"]["whole_dataset"]
    up = _side(whole, "myb41")
    assert [g["gene"] for g in up["genes"]] == ["atMYB41", "ATMYB41"]
    assert up["passing"] == 4
    down = _side(whole, "Col-0")
    assert ([g["gene"] for g in down["genes"]], down["passing"]) == (["DOWNX"], 1)


def test_an_infinite_fold_change_is_found_in_one_group_only(result):
    [whole] = result["top4"]["whole_dataset"]
    genes = {g["gene"]: g for g in _side(whole, "myb41")["genes"]}
    assert genes["ATZ1"]["log2fc"] is None and genes["ATZ1"]["found_in_one_group_only"]
    assert genes["BIG"]["log2fc"] == 25.0
    assert genes["BIG"]["likely_found_in_one_group_only"]


def test_an_untested_comparison_says_so(result):
    [comparison] = _cell(result["top2"], "C")["genotype"]
    assert not comparison["tested"] and comparison["sides"] == []
    assert "not tested" in comparison["note"].lower()
