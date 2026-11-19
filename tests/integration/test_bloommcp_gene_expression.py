"""Live check of bloommcp's gene_expression: as bloom_agent it reads a dataset's cells through
scrna_cell_arrays and each gene's counts file from the scrna bucket (both stored shapes), and
returns per-cell-type numbers with the plot's link.

Unit counterpart: bloommcp/tests/sections/bloom_data/test_gene_expression.py.
"""

from __future__ import annotations

import uuid

import pytest

from tests.integration.test_bloommcp_data_tools import _run_in_bloommcp
from tests.integration.test_bloommcp_read_file import _connect, _storage

pytestmark = pytest.mark.integration

# Two cell types of two cells each: cell 0-1 "Cortex", cell 2-3 cluster "X" (no name).
CELLS = [(0, "C"), (1, "C"), (2, "X"), (3, "X")]


@pytest.fixture(scope="module")
def dataset():
    folder = f"counts/ci_expression_{uuid.uuid4().hex[:8]}_"
    files = {
        f"{folder}/AT4G28110.json": b'{"1": 4.0, "2": 2.0, "3": 1.0}',
        f"{folder}/AT1G01010.json": b'[{"0": 1.0}]',
    }
    uploaded = []
    conn = _connect()
    ids: dict = {}
    try:
        for path, body in files.items():
            status = _storage("POST", "scrna", path, body, "application/json")
            assert status == 200, f"upload to scrna failed: {status}"
            uploaded.append(path)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO public.species (common_name, genus, species) "
                "VALUES ('ci cress', 'Ci', 'expressionis') RETURNING id"
            )
            ids["species"] = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO public.scrna_datasets (name, species_id) VALUES (%s, %s) RETURNING id",
                (f"ci-expression-{uuid.uuid4().hex[:8]}", ids["species"]),
            )
            ids["dataset"] = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO public.scrna_clusters (dataset_id, cluster_id, ordinal, name) "
                "VALUES (%s, 'C', 0, 'Cortex'), (%s, 'X', 1, NULL)",
                (ids["dataset"], ids["dataset"]),
            )
            for number, cluster in CELLS:
                cur.execute(
                    "INSERT INTO public.scrna_cells (dataset_id, cell_number, cluster_id, x, y) "
                    "VALUES (%s, %s, %s, 0, 0)",
                    (ids["dataset"], number, cluster),
                )
            for number, name in enumerate(["AT4G28110", "AT1G01010"]):
                cur.execute(
                    "INSERT INTO public.scrna_genes (dataset_id, gene_number, gene_name) "
                    "VALUES (%s, %s, %s) RETURNING id",
                    (ids["dataset"], number, name),
                )
                gene = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO public.scrna_counts (dataset_id, gene_id, counts_object_path) "
                    "VALUES (%s, %s, %s)",
                    (ids["dataset"], gene, f"{folder}/{name}.json"),
                )
        yield ids
    finally:
        with conn.cursor() as cur:
            if "dataset" in ids:
                for table in (
                    "scrna_counts",
                    "scrna_genes",
                    "scrna_cells",
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
        for path in uploaded:
            _storage("DELETE", "scrna", path)


_SCRIPT = """
import json
from bloom_mcp.sections.bloom_data.gene_expression import gene_expression
out = gene_expression({{"dataset_id": {dataset}, "genes": ["AT4G28110", "AT1G01010", "NOPE1"]}})
print(json.dumps(out.model_dump(mode="json")))
"""


@pytest.fixture(scope="module")
def result(dataset) -> dict:
    return _run_in_bloommcp(_SCRIPT.format(dataset=dataset["dataset"]))


def _groups(result: dict, gene: str) -> list[tuple]:
    [found] = [g for g in result["genes"] if g["gene_name"] == gene]
    return [
        (r["cell_type"], r["cells"], r["expressing"], r["mean"])
        for r in found["groups"]
    ]


def test_numbers_per_cell_type_from_an_object_file(result):
    assert result["cells"] == 4
    assert _groups(result, "AT4G28110") == [("Cortex", 2, 1, 2.0), ("X", 2, 2, 1.5)]


def test_an_older_list_file_is_read(result):
    assert _groups(result, "AT1G01010") == [("Cortex", 2, 1, 0.5), ("X", 2, 0, 0.0)]


def test_an_unknown_gene_says_so(result):
    [gene] = [g for g in result["genes"] if g["requested"] == "NOPE1"]
    assert gene["note"] == "Not a gene in this dataset." and gene["groups"] == []


def test_the_link_opens_the_plot_on_the_genes(result, dataset):
    if result["page_url"] is None:
        pytest.skip("SITE_URL is not set in the bloommcp container")
    assert result["page_url"].endswith(
        f"/app/expression/{dataset['species']}/{dataset['dataset']}"
        "?tab=genes&gene=AT4G28110,AT1G01010"
    )
