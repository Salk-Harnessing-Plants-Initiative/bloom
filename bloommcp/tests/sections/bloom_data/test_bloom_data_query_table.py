"""query_table: one allow-listed table, filtered, capped and paged."""

from __future__ import annotations

import pytest

from bloom_mcp.contract.errors import BloomMCPError
from bloom_mcp.data_access.table_access import EMAIL_HIDDEN
from bloom_mcp.sections.bloom_data.query_table import (
    MAX_CELL_CHARS,
    TRUNCATED,
    query_table,
)


def test_default_page_is_50_rows_with_a_next_offset(reader):
    result = query_table({"table": "species"})
    assert result.returned == 50
    assert result.has_more is True
    assert result.next_offset == 50
    assert result.rows[0]["id"] == 1


def test_last_page_has_no_next_offset(reader):
    result = query_table({"table": "species", "offset": 100, "limit": 50})
    assert result.returned == 20
    assert result.has_more is False
    assert result.next_offset is None


def test_limit_is_capped_at_500(reader):
    with pytest.raises(BloomMCPError) as err:
        query_table({"table": "species", "limit": 501})
    assert err.value.code == "invalid_input"


def test_filters_and_order(reader):
    result = query_table(
        {
            "table": "species",
            "columns": ["id"],
            "filters": [{"column": "id", "op": "lte", "value": 3}],
            "order_by": "id",
            "descending": True,
        }
    )
    assert result.rows == [{"id": 3}, {"id": 2}, {"id": 1}]


def test_contains_filter_matches_case_insensitively(reader):
    result = query_table(
        {
            "table": "species",
            "filters": [
                {"column": "common_name", "op": "contains", "value": "PLANT 11"}
            ],
        }
    )
    assert {row["id"] for row in result.rows} == {
        11,
        110,
        111,
        112,
        113,
        114,
        115,
        116,
        117,
        118,
        119,
    }


def test_email_is_never_returned(reader):
    result = query_table({"table": "people"})
    assert all("email" not in row for row in result.rows)
    assert "email" not in result.columns


@pytest.mark.parametrize(
    "params",
    [
        {"table": "people", "columns": ["email"]},
        {
            "table": "people",
            "filters": [{"column": "email", "op": "eq", "value": "ada@salk.edu"}],
        },
        {"table": "people", "order_by": "email"},
    ],
    ids=["select", "filter", "order"],
)
def test_a_hidden_column_cannot_be_selected_filtered_or_ordered(reader, params):
    with pytest.raises(BloomMCPError) as err:
        query_table(params)
    assert err.value.code == "tool_error"
    assert "email" in err.value.message
    assert reader.calls == []


def test_an_excluded_table_is_refused_before_any_read(reader):
    with pytest.raises(BloomMCPError) as err:
        query_table({"table": "checkpoints"})
    assert err.value.code == "tool_error"
    assert "list_tables" in err.value.remedy
    assert reader.calls == []


def test_an_unknown_column_is_refused(reader):
    with pytest.raises(BloomMCPError) as err:
        query_table({"table": "species", "columns": ["nope"]})
    assert "nope" in err.value.message


def test_a_timeout_asks_to_narrow_the_query(reader):
    reader.slow_tables.add("cyl_scan_traits_source")
    with pytest.raises(BloomMCPError) as err:
        query_table({"table": "cyl_scan_traits_source"})
    assert err.value.code == "tool_error"
    assert "filter" in err.value.message.lower()


def test_long_values_are_cut(reader):
    first = next(r for r in reader.tables["species"] if r["id"] == 1)
    first["common_name"] = "x" * (MAX_CELL_CHARS + 50)
    result = query_table({"table": "species", "limit": 1})
    assert result.rows[0]["common_name"] == "x" * MAX_CELL_CHARS + TRUNCATED
    assert result.truncated_values is True
    assert query_table({"table": "species", "offset": 1}).truncated_values is False


def test_emails_in_gene_candidate_logs_are_hidden(reader):
    row = query_table({"table": "gene_candidates"}).rows[0]
    assert row["status"] == "validated"
    assert row["status_logs"] == [
        {"userid": EMAIL_HIDDEN, "status": "suspected", "date": "2026-01-02"},
        {"userid": EMAIL_HIDDEN, "status": "validated", "date": "2026-02-03"},
    ]
    assert row["legacy_progress_logs_jsonb"] == {
        "note": f"sent to {EMAIL_HIDDEN}",
        "n": 1,
    }


def test_the_conftest_rows_are_left_unchanged(reader):
    query_table({"table": "gene_candidates"})
    assert (
        reader.tables["gene_candidates"][0]["status_logs"][0]["userid"]
        == "ada@salk.edu"
    )


@pytest.mark.parametrize("column", ["status_logs", "legacy_progress_logs_jsonb"])
def test_log_columns_cannot_be_filtered_or_sorted(reader, column):
    for call in (
        {"filters": [{"column": column, "op": "contains", "value": "@salk"}]},
        {"order_by": column},
    ):
        with pytest.raises(BloomMCPError) as err:
            query_table({"table": "gene_candidates", **call})
        assert err.value.code == "tool_error"
        assert column in err.value.message


def _ids(rows, key):
    return [r[key] for r in rows]


def test_a_table_without_id_pages_by_its_primary_key_without_overlap(reader):
    first = query_table({"table": "genes", "columns": ["gene_id"], "limit": 10})
    second = query_table(
        {
            "table": "genes",
            "columns": ["gene_id"],
            "limit": 10,
            "offset": first.next_offset,
        }
    )
    assert _ids(first.rows, "gene_id") == [f"AT{i:05d}" for i in range(1, 11)]
    assert _ids(second.rows, "gene_id") == [f"AT{i:05d}" for i in range(11, 21)]


def test_a_composite_key_sorts_by_every_key_column_in_index_order(reader):
    rows = query_table({"table": "scrna_cluster_stats"}).rows
    assert [(r["dataset_id"], r["cluster_id"]) for r in rows] == [
        (1, 1),
        (1, 2),
        (1, 3),
        (2, 1),
        (2, 2),
        (2, 3),
    ]


def test_a_view_sorts_by_its_declared_key(reader):
    rows = query_table({"table": "cyl_scans_extended"}).rows
    assert _ids(rows, "scan_id") == [1, 2, 3, 4, 5]


def test_descending_without_order_by_reverses_the_key(reader):
    rows = query_table(
        {"table": "genes", "columns": ["gene_id"], "descending": True}
    ).rows
    assert rows[0]["gene_id"] == "AT00030"


def test_a_chosen_sort_column_is_tie_broken_by_the_key(reader):
    rows = query_table(
        {"table": "cyl_scans_extended", "order_by": "experiment_id", "descending": True}
    ).rows
    assert [(r["experiment_id"], r["scan_id"]) for r in rows] == [
        (1, 1),
        (1, 3),
        (1, 5),
        (0, 2),
        (0, 4),
    ]


def test_the_peek_row_is_not_returned_when_exactly_limit_rows_remain(reader):
    result = query_table({"table": "species", "offset": 70, "limit": 50})
    assert result.returned == 50
    assert result.has_more is False
