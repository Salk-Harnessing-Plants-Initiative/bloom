"""scrna_gene_cells: one gene's expressing cells in one scRNA dataset, highest first, in pages."""

from __future__ import annotations

import json

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from scrna_expression_data import SITE

from bloom_mcp.contract.errors import BloomMCPError
from bloom_mcp.data_access import expression_counts
from bloom_mcp.sections.bloom_data import _scrna_dataset
from bloom_mcp.sections.bloom_data import gene_cells as tool
from bloom_mcp.sections.bloom_data import gene_expression as ge

GENE_FILE = ("scrna", "counts/MYB41_root_3_/AT4G28110.json")


def _run(**kw):
    return tool.scrna_gene_cells({"dataset_id": 3, "gene": "AT4G28110", **kw})


def _cells(db, n, ordinal=0):
    """n cells of one cell type, each with its own position, genotype and replicate."""
    reader, _ = db
    rows = [
        {
            "x": float(i),
            "y": float(-i),
            "cluster_ordinal": ordinal,
            "genotype": f"g{i}",
            "replicate": f"r{i}",
            "facets": None,
        }
        for i in range(n)
    ]
    reader.function_rows["scrna_cell_arrays"] = (
        reader.function_rows["scrna_cell_arrays"][0],
        rows,
    )


def _values(db, values):
    _, files = db
    files.files[GENE_FILE] = json.dumps(
        {str(i): v for i, v in enumerate(values) if v != 0}
    ).encode()


def _ids(result):
    return [(c.cell, c.value) for c in result.cells]


def test_each_row_carries_its_own_cells_values(expression_db):
    result = _run()
    assert [
        (c.cell, c.value, c.cell_type, c.genotype, c.replicate, c.x, c.y)
        for c in result.cells
    ] == [
        (1, 4.0, "Cortex", "pFACT", "rep2", 1.0, 0.0),
        (2, 2.0, "X", "pFACT", "rep1", 0.0, 1.0),
        (3, 1.0, "X", "Col-0", "rep2", 1.0, 1.0),
    ]
    assert result.cells[0].facets == {"transgene_pos": "True"}
    assert (result.cells_in_scope, result.cells_expressing) == (5, 3)
    assert (result.gene_name, result.next_offset, result.returned) == (
        "AT4G28110",
        None,
        3,
    )


def test_positions_line_up_with_many_distinct_cells(expression_db):
    _cells(expression_db, 50)
    _values(expression_db, [i % 7 for i in range(50)])
    for c in _run(limit=2000).cells:
        assert (c.x, c.y, c.genotype, c.replicate) == (
            float(c.cell),
            float(-c.cell),
            f"g{c.cell}",
            f"r{c.cell}",
        )
        assert c.value == c.cell % 7


def test_zero_and_negative_values_are_not_expressing(expression_db):
    _values(expression_db, [0, -1.5, 3.0, 0, 2.0])
    result = _run()
    assert _ids(result) == [(2, 3.0), (4, 2.0)]
    assert result.cells_expressing == 2


def test_highest_first_and_ties_by_position(expression_db):
    _values(expression_db, [1.0, 5.0, 1.0, 5.0, 2.0])
    assert [c.cell for c in _run().cells] == [1, 3, 4, 0, 2]


def test_the_default_page_is_500_cells(expression_db):
    _cells(expression_db, 700)
    _values(expression_db, [1.0 + i for i in range(700)])
    result = _run()
    assert (len(result.cells), result.next_offset, result.cells_expressing) == (
        500,
        500,
        700,
    )


def _walk(limit):
    seen, offset = [], 0
    while offset is not None:
        page = _run(offset=offset, limit=limit)
        seen += _ids(page)
        offset = page.next_offset
    return seen


def test_pages_cover_every_expressing_cell_once(expression_db):
    _cells(expression_db, 9)
    _values(expression_db, [3, 3, 0, 2, 2, 1, 1, 9, 0])
    assert _walk(2) == _ids(_run(limit=2000))
    assert len(_walk(2)) == 7


@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    values=st.lists(st.integers(min_value=-2, max_value=4), min_size=1, max_size=40),
    limit=st.integers(min_value=1, max_value=7),
)
def test_paging_matches_one_sorted_list(expression_db, values, limit):
    _cells(expression_db, len(values))
    _values(expression_db, values)
    expected = sorted(
        ((i, float(v)) for i, v in enumerate(values) if v > 0),
        key=lambda c: (-c[1], c[0]),
    )
    assert _walk(limit) == expected


def _size(result):
    return len(json.dumps(result.model_dump(), default=str))


def test_a_page_is_cut_short_at_the_reply_limit(expression_db, monkeypatch):
    _cells(expression_db, 40)
    _values(expression_db, [1.0 + i for i in range(40)])
    full = _run(limit=40)
    monkeypatch.setattr(tool, "MAX_REPLY_CHARS", _size(full) // 2)
    page = _run(limit=40)
    assert 0 < len(page.cells) < 40
    assert page.next_offset == len(page.cells)
    assert _size(page) <= tool.MAX_REPLY_CHARS
    assert any("reply" in note for note in page.notes)
    assert _walk(40) == _ids(full)


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 2001},
        {"offset": -1},
        {"gene": "  "},
        {"dataset_id": True},
        {"cell_types": ["c"] * 11},
        {"cell_types": ["x" * 201]},
        {"sample": "a"},
    ],
)
def test_bad_requests_are_refused_before_any_read(expression_db, params):
    reader, files = expression_db
    with pytest.raises(BloomMCPError):
        _run(**params)
    assert reader.calls == [] and files.calls == []


def test_totals_match_the_expression_tools(expression_db):
    result = _run(cell_types=["x"])
    [gene] = ge.gene_expression({"dataset_id": 3, "genes": ["AT4G28110"]}).genes
    [x] = [g for g in gene.groups if g.cell_type == "X"]
    assert (result.cells_in_scope, result.cells_expressing) == (x.cells, x.expressing)


def test_an_offset_past_the_end_gives_an_empty_page_and_the_totals(expression_db):
    result = _run(offset=10)
    assert (result.cells, result.next_offset, result.cells_expressing) == ([], None, 3)


def test_a_gene_expressed_nowhere_gives_the_totals(expression_db):
    _values(expression_db, [0, 0, 0, 0, 0])
    result = _run()
    assert (result.cells, result.cells_in_scope, result.cells_expressing) == ([], 5, 0)
    assert result.next_offset is None


def test_cell_types_by_id_or_name_in_any_case(expression_db):
    assert [c.cell for c in _run(cell_types=["cortex"]).cells] == [1]
    assert [c.cell for c in _run(cell_types=[" c ", "X"]).cells] == [1, 2, 3]


def test_an_unknown_cell_type_is_noted(expression_db):
    result = _run(cell_types=["X", "Phloem"])
    assert [c.cell for c in result.cells] == [2, 3]
    assert any("Phloem" in n for n in result.notes)


def test_every_cell_type_unknown_lists_the_datasets(expression_db):
    result = _run(cell_types=["Phloem"])
    assert (result.cells, result.cells_in_scope) == ([], 0)
    [note] = [n for n in result.notes if "Phloem" in n]
    assert "Cortex" in note and "X" in note


def test_a_cell_with_no_cell_type(expression_db):
    _values(expression_db, [0, 0, 0, 0, 7.0])
    [cell] = _run().cells
    assert (cell.cell, cell.cell_type, cell.genotype, cell.replicate) == (
        4,
        "No cell type",
        None,
        None,
    )


def test_a_symbol_with_two_genes_reads_the_first_and_names_the_other(expression_db):
    result = _run(gene="PAIR")
    assert result.gene_name == "AT2G02020"
    assert any("AT3G03030" in n for n in result.notes)


@pytest.mark.parametrize(
    "gene,note",
    [
        ("NOPE1", "Not a gene in this dataset."),
        ("AT2G02020", "This dataset stores no expression for this gene."),
    ],
)
def test_a_gene_with_no_values_says_why(expression_db, gene, note):
    result = _run(gene=gene)
    assert result.cells == [] and note in result.notes
    assert result.cells_expressing is None


@pytest.mark.parametrize(
    "dataset_id,match",
    [(4, "reference atlas"), (5, "No scRNA dataset"), (99, "No scRNA dataset")],
)
def test_unusable_datasets_are_refused(expression_db, dataset_id, match):
    with pytest.raises(BloomMCPError, match=match):
        _run(dataset_id=dataset_id)


def test_too_many_cells_is_refused(expression_db, monkeypatch):
    monkeypatch.setattr(_scrna_dataset, "MAX_CELLS", 4)
    with pytest.raises(BloomMCPError, match="more than this tool reads"):
        _run()


def test_a_counts_file_too_large_says_so(expression_db, monkeypatch):
    monkeypatch.setattr(expression_counts, "MAX_COUNTS_BYTES", 1)
    result = _run()
    assert result.cells == [] and any("MB" in n for n in result.notes)
    assert result.cells_expressing is None


@pytest.mark.parametrize("raw", [b'{"1": NaN}', b'{"1": Infinity}'])
def test_a_counts_file_with_non_finite_values_says_so(expression_db, raw):
    _, files = expression_db
    files.files[GENE_FILE] = raw
    result = _run()
    assert result.cells == [] and any("non-finite" in n for n in result.notes)
    assert result.cells_expressing is None


def test_out_of_time_before_the_counts_file(expression_db, monkeypatch):
    _, files = expression_db
    monkeypatch.setattr(tool, "CALL_SECONDS", -1)
    result = _run()
    assert result.cells == [] and any("out of time" in n for n in result.notes)
    assert result.cells_expressing is None
    assert not any(call[0] == "download" for call in files.calls)


def test_the_link_opens_the_umap_on_the_gene(expression_db):
    assert _run().page_url == f"{SITE}/app/expression/16/3?tab=umap&gene=AT4G28110"


def test_limits_are_as_documented():
    assert (tool.DEFAULT_LIMIT, tool.MAX_LIMIT, tool.CALL_SECONDS) == (500, 2000, 40)
