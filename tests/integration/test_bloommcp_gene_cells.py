"""Live check of bloommcp's scrna_gene_cells: as bloom_agent it reads a dataset's cells
through scrna_cell_arrays and a gene's counts file from the scrna bucket, returns each
expressing cell with its own cell type, genotype, replicate and UMAP position, and sends the
UMAP as a PNG when asked.

Unit counterparts: bloommcp/tests/sections/bloom_data/test_gene_cells.py and
test_gene_cells_plot.py.
"""

from __future__ import annotations

import uuid

import pytest

from tests.integration.test_bloommcp_data_tools import _run_in_bloommcp
from tests.integration.test_bloommcp_read_file import _connect, _storage

pytestmark = pytest.mark.integration

# Cell number, cluster, genotype, replicate; x and y are the cell number and its negative.
CELLS = [
    (0, "C", "Col-0", "rep1"),
    (1, "C", "pFACT", "rep2"),
    (2, "X", "pFACT", "rep3"),
    (3, "X", "Col-0", "rep4"),
    (4, "C", None, None),
    (5, "X", "pFACT", "rep6"),
]
COUNTS = b'{"1": 4.0, "2": 2.0, "3": 1.0, "5": 3.0}'


@pytest.fixture(scope="module")
def dataset():
    tag = uuid.uuid4().hex[:8]
    path = f"counts/ci_gene_cells_{tag}_/AT4G28110.json"
    conn = _connect()
    ids: dict = {}
    try:
        assert _storage("POST", "scrna", path, COUNTS, "application/json") == 200
        ids["path"] = path
        with conn.cursor() as cur:
            # Names of their own: common_name and (genus, species) are unique.
            cur.execute(
                "INSERT INTO public.species (common_name, genus, species) "
                "VALUES (%s, 'Ci', %s) RETURNING id",
                (f"ci-cells-cress-{tag}", f"cellis{tag}"),
            )
            ids["species"] = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO public.scrna_datasets (name, species_id) VALUES (%s, %s) "
                "RETURNING id",
                (f"ci-gene-cells-{tag}", ids["species"]),
            )
            dataset = ids["dataset"] = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO public.scrna_clusters (dataset_id, cluster_id, ordinal, name, "
                "color) VALUES (%s, 'C', 0, 'Cortex', '#1f77b4'), (%s, 'X', 1, NULL, NULL)",
                (dataset, dataset),
            )
            genotypes = {}
            for name in ("Col-0", "pFACT"):
                cur.execute(
                    "INSERT INTO public.scrna_genotypes (dataset_id, name) "
                    "VALUES (%s, %s) RETURNING id",
                    (dataset, name),
                )
                genotypes[name] = cur.fetchone()[0]
            for number, cluster, genotype, replicate in CELLS:
                cur.execute(
                    "INSERT INTO public.scrna_cells (dataset_id, cell_number, cluster_id, "
                    "x, y, genotype_id, replicate) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (
                        dataset,
                        number,
                        cluster,
                        number,
                        -number,
                        genotypes.get(genotype),
                        replicate,
                    ),
                )
            cur.execute(
                "INSERT INTO public.scrna_genes (dataset_id, gene_number, gene_name) "
                "VALUES (%s, 0, 'AT4G28110') RETURNING id",
                (dataset,),
            )
            gene = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO public.scrna_counts (dataset_id, gene_id, counts_object_path) "
                "VALUES (%s, %s, %s)",
                (dataset, gene, path),
            )
        yield ids
    finally:
        _clean_up(conn, ids)


def _clean_up(conn, ids: dict) -> None:
    """Every delete is tried, so one failure doesn't leave the rest in the shared database."""
    deletes = [
        (f"DELETE FROM public.{table} WHERE dataset_id = %s", "dataset")  # noqa: S608
        for table in (
            "scrna_counts",
            "scrna_genes",
            "scrna_cells",
            "scrna_genotypes",
            "scrna_clusters",
        )
    ] + [
        ("DELETE FROM public.scrna_datasets WHERE id = %s", "dataset"),
        ("DELETE FROM public.species WHERE id = %s", "species"),
    ]
    failed = []
    with conn.cursor() as cur:
        for sql, key in deletes:
            if key not in ids:
                continue
            try:
                cur.execute(sql, (ids[key],))
            except Exception as exc:  # noqa: BLE001
                failed.append(f"{sql}: {exc}")
    conn.close()
    if "path" in ids:
        _storage("DELETE", "scrna", ids["path"])
    assert not failed, failed


_SCRIPT = """
import base64, io, json
from fastmcp.tools import ToolResult
from PIL import Image
from bloom_mcp.sections.bloom_data.gene_cells import scrna_gene_cells

def run(params):
    out = scrna_gene_cells(params)
    if not isinstance(out, ToolResult):
        return {{"result": out.model_dump(mode="json"), "images": []}}
    images = []
    for block in out.content:
        if block.type == "image":
            data = base64.b64decode(block.data)
            image = Image.open(io.BytesIO(data))
            images.append([data[:8].hex(), image.format, list(image.size)])
    return {{"result": out.structured_content, "images": images}}

ds = {dataset}
print(json.dumps({{
    "plain": run({{"dataset_id": ds, "gene": "AT4G28110"}}),
    "umap": run({{"dataset_id": ds, "gene": "AT4G28110", "plot": "umap"}}),
    "by_type": run({{"dataset_id": ds, "color_by": "cell_type"}}),
}}))
"""


@pytest.fixture(scope="module")
def results(dataset) -> dict:
    return _run_in_bloommcp(_SCRIPT.format(dataset=dataset["dataset"]))


def test_each_row_carries_its_own_cells_values(results):
    result = results["plain"]["result"]
    rows = [
        (
            c["cell"],
            c["value"],
            c["cell_type"],
            c["genotype"],
            c["replicate"],
            c["x"],
            c["y"],
        )
        for c in result["cells"]
    ]
    assert rows == [
        (1, 4.0, "Cortex", "pFACT", "rep2", 1.0, -1.0),
        (5, 3.0, "X", "pFACT", "rep6", 5.0, -5.0),
        (2, 2.0, "X", "pFACT", "rep3", 2.0, -2.0),
        (3, 1.0, "X", "Col-0", "rep4", 3.0, -3.0),
    ]
    assert (result["cells_in_scope"], result["cells_expressing"]) == (6, 4)
    assert result["units"] == "log-normalized"
    assert results["plain"]["images"] == []


PNG_SIGNATURE = "89504e470d0a1a0a"


def test_the_umap_comes_back_as_a_png(results):
    assert results["umap"]["images"] == [[PNG_SIGNATURE, "PNG", [1200, 900]]]
    assert len(results["umap"]["result"]["cells"]) == 4


def test_the_umap_by_cell_type_needs_no_gene(results):
    by_type = results["by_type"]
    assert by_type["images"] == [[PNG_SIGNATURE, "PNG", [1200, 900]]]
    assert by_type["result"]["cells"] == []
    assert by_type["result"]["cells_expressing"] is None
