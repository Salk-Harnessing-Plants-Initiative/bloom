"""query_table gives each row of a table with a Bloom page its page_url."""

from __future__ import annotations

import pytest

from bloom_mcp.sections.bloom_data.query_table import query_table

SITE = "https://bloom.example"
EXPERIMENTS = [
    {"id": 7, "name": "T3 HORST MYB Nile Red CYL", "species_id": 16},
    {"id": 8, "name": "no species yet", "species_id": None},
]


@pytest.fixture
def experiments(reader, monkeypatch):
    monkeypatch.setenv("SITE_URL", SITE + "/")
    reader.tables["cyl_experiments"] = [dict(r) for r in EXPERIMENTS]
    return reader


def test_each_row_carries_its_page(experiments):
    result = query_table(
        {"table": "cyl_experiments", "columns": ["id", "name", "species_id"]}
    )
    assert result.rows[0]["page_url"] == f"{SITE}/app/phenotypes/16/7"


def test_link_columns_are_read_but_not_returned_when_not_asked_for(experiments):
    result = query_table({"table": "cyl_experiments", "columns": ["name"]})
    assert result.columns == ["name"]
    assert result.rows[0] == {
        "name": "T3 HORST MYB Nile Red CYL",
        "page_url": f"{SITE}/app/phenotypes/16/7",
    }


def test_a_row_with_an_empty_link_column_has_no_page(experiments):
    result = query_table({"table": "cyl_experiments", "columns": ["name"]})
    assert result.rows[1] == {"name": "no species yet"}


def test_no_page_without_a_site(experiments, monkeypatch):
    monkeypatch.delenv("SITE_URL")
    result = query_table({"table": "cyl_experiments", "columns": ["name"]})
    assert all("page_url" not in row for row in result.rows)


def test_tables_without_a_page_are_unchanged(reader, monkeypatch):
    monkeypatch.setenv("SITE_URL", SITE)
    result = query_table({"table": "species", "columns": ["id"], "limit": 2})
    assert result.rows == [{"id": 1}, {"id": 2}]
