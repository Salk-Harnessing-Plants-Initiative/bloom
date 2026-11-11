"""Sorts and contains filters stay on indexed columns, so no read scans a large table."""

from __future__ import annotations

import pytest

from bloom_mcp.contract.errors import BloomMCPError
from bloom_mcp.data_access import table_access, table_indexes
from bloom_mcp.sections.bloom_data.count_rows import count_rows
from bloom_mcp.sections.bloom_data.list_tables import list_tables
from bloom_mcp.sections.bloom_data.query_table import query_table


def _refused(call, params) -> str:
    with pytest.raises(BloomMCPError) as err:
        call(params)
    assert err.value.code == "invalid_input"
    return f"{err.value.message} {err.value.remedy}"


def test_a_sort_on_an_unindexed_column_is_refused_with_the_allowed_ones(reader):
    text = _refused(query_table, {"table": "species", "order_by": "deleted_at"})
    assert "common_name, genus, id" in text


def test_a_sort_on_an_indexed_column_runs(reader):
    rows = query_table({"table": "species", "order_by": "common_name", "limit": 2}).rows
    assert [r["common_name"] for r in rows] == ["plant 1", "plant 10"]


def test_a_view_sorts_only_by_its_paging_key(reader):
    text = _refused(
        query_table, {"table": "cyl_scans_extended", "order_by": "experiment_id"}
    )
    assert "only be sorted by scan_id" in text
    rows = query_table({"table": "cyl_scans_extended", "order_by": "scan_id"}).rows
    assert [r["scan_id"] for r in rows] == [1, 2, 3, 4, 5]


def test_contains_on_a_large_table_needs_a_trigram_column(reader):
    text = _refused(
        query_table,
        {
            "table": "genes",
            "filters": [{"column": "name", "op": "contains", "value": "x"}],
        },
    )
    assert "gene_id, standard_name, symbol" in text
    found = query_table(
        {
            "table": "genes",
            "filters": [{"column": "gene_id", "op": "contains", "value": "00001"}],
        }
    ).rows
    assert [r["gene_id"] for r in found] == ["AT00001"]


def test_contains_on_a_large_view_is_refused(reader):
    text = _refused(
        query_table,
        {
            "table": "cyl_scan_traits_source",
            "filters": [{"column": "source_id", "op": "contains", "value": "1"}],
        },
    )
    assert "no column" in text


def test_count_rows_refuses_the_same_contains(reader):
    _refused(
        count_rows,
        {
            "table": "genes",
            "filters": [{"column": "name", "op": "contains", "value": "x"}],
        },
    )


def test_contains_on_a_small_table_may_use_any_column(reader):
    rows = query_table(
        {
            "table": "species",
            "filters": [
                {"column": "common_name", "op": "contains", "value": "plant 12"}
            ],
        }
    ).rows
    assert {r["id"] for r in rows} == {12, 120}


def test_list_tables_says_what_a_table_may_sort_and_search_by(reader):
    genes = list_tables({"table": "genes"})
    assert genes.sortable == ["gene_id", "short_id"]
    assert genes.contains_columns == ["gene_id", "standard_name", "symbol"]
    species = list_tables({"table": "species"})
    assert species.contains_columns is None


def test_every_listed_table_is_readable():
    for table in [
        *table_indexes.SORTABLE_COLUMNS,
        *table_indexes.LARGE_TABLES,
        *table_indexes.CONTAINS_COLUMNS,
    ]:
        assert table_access.is_readable(table), table


def test_trigram_columns_are_listed_only_for_large_tables():
    assert set(table_indexes.CONTAINS_COLUMNS) <= table_indexes.LARGE_TABLES


@pytest.mark.parametrize("table", sorted(table_access.READABLE_TABLES))
def test_the_default_order_starts_on_a_sortable_column(table):
    assert table_access.paging_key(table)[0] in table_indexes.sortable_columns(table)
