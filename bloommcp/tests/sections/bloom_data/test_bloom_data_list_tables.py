"""list_tables: the readable tables, and one table's visible columns."""

from __future__ import annotations

import pytest

from bloom_mcp.contract.errors import BloomMCPError
from bloom_mcp.sections.bloom_data.list_tables import list_tables


def test_lists_only_allow_listed_tables(reader):
    result = list_tables({})
    assert result.tables == [
        "cyl_scan_traits",
        "cyl_scans_extended",
        "gene_candidates",
        "genes",
        "people",
        "scrna_cluster_stats",
        "species",
    ]
    assert result.columns is None


def test_filters_table_names(reader):
    assert list_tables({"contains": "SPEC"}).tables == ["species"]


def test_describes_one_table_without_hidden_columns(reader):
    result = list_tables({"table": "people"})
    assert result.tables == ["people"]
    assert result.columns == ["id", "name"]


def test_describing_an_excluded_table_is_refused(reader):
    with pytest.raises(BloomMCPError) as err:
        list_tables({"table": "checkpoints"})
    assert err.value.code == "tool_error"
