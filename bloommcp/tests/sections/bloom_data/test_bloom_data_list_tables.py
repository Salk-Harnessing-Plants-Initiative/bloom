"""list_tables: the readable tables, and one table's visible columns."""

from __future__ import annotations

import pytest

from bloom_mcp.contract.errors import BloomMCPError
from bloom_mcp.data_access import table_access
from bloom_mcp.sections.bloom_data.list_tables import list_tables


def test_lists_only_allow_listed_tables(reader):
    result = list_tables({})
    assert result.tables == [
        "cyl_scan_traits_source",
        "cyl_scans_extended",
        "gene_candidates",
        "genes",
        "people",
        "scrna_cluster_stats",
        "species",
    ]
    assert result.columns is None
    assert result.notes == {
        t: table_access.TABLE_NOTES[t]
        for t in ("cyl_scan_traits_source", "cyl_scans_extended")
    }


def test_filters_table_names(reader):
    assert list_tables({"contains": "SPEC"}).tables == ["species"]


def test_describes_one_table_without_hidden_columns(reader):
    result = list_tables({"table": "people"})
    assert result.tables == ["people"]
    assert result.columns == ["id", "name"]


def test_describing_an_excluded_table_is_refused(reader):
    with pytest.raises(BloomMCPError) as err:
        list_tables({"table": "checkpoints"})
    assert err.value.code == "invalid_input"


def test_describing_a_table_with_a_note_returns_it(reader):
    result = list_tables({"table": "cyl_scan_traits_source"})
    assert "is_latest" in result.notes["cyl_scan_traits_source"]


def test_a_table_without_a_note_has_none(reader):
    assert list_tables({"table": "species"}).notes == {}


def test_the_raw_traits_table_is_refused(reader):
    with pytest.raises(BloomMCPError) as err:
        list_tables({"table": "cyl_scan_traits"})
    assert err.value.code == "invalid_input"
