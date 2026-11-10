"""Links from list_tables' notes to Bloom's web pages."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from bloom_mcp.data_access import page_links, table_access, table_notes
from bloom_mcp.sections.bloom_data.list_tables import _notes

_PLACEHOLDER = re.compile(r"\{(\w+)\}")
_ROUTES = Path(__file__).resolve().parents[3] / "web" / "app"


def test_a_link_note_names_the_site_and_the_columns_to_fill(monkeypatch):
    monkeypatch.setenv("SITE_URL", "https://bloom.example/")
    note = page_links.link_note("cyl_experiments")
    assert note is not None
    assert "https://bloom.example/app/phenotypes/{species_id}/{id}" in note


def test_no_link_is_shown_without_a_site(monkeypatch):
    monkeypatch.delenv("SITE_URL", raising=False)
    assert page_links.link_note("cyl_experiments") is None
    assert _notes(["rnaseq_runs"]) == {}
    assert _notes(["cyl_experiments"]) == {
        "cyl_experiments": table_notes.TABLE_NOTES["cyl_experiments"]
    }


def test_the_link_follows_the_tables_own_note(monkeypatch):
    monkeypatch.setenv("SITE_URL", "https://bloom.example")
    notes = _notes(["cyl_experiments", "rnaseq_runs", "species"])
    assert notes["cyl_experiments"].startswith(
        table_notes.TABLE_NOTES["cyl_experiments"]
    )
    assert "https://bloom.example/app/rnaseq-runs/{id}" in notes["rnaseq_runs"]
    assert "species" not in notes


@pytest.mark.parametrize("table", sorted(page_links.PAGE_LINKS))
def test_links_use_readable_visible_columns(table):
    assert table_access.is_readable(table)
    columns = _PLACEHOLDER.findall(page_links.PAGE_LINKS[table])
    assert table_access.visible_columns(table, columns) == columns


@pytest.mark.skipif(not _ROUTES.is_dir(), reason="the web app isn't checked out here")
@pytest.mark.parametrize("table", sorted(page_links.PAGE_LINKS))
def test_every_link_is_a_page_on_the_website(table):
    folder = _ROUTES
    for part in page_links.PAGE_LINKS[table].strip("/").split("/"):
        if _PLACEHOLDER.fullmatch(part):
            dynamic = [
                d for d in folder.iterdir() if d.is_dir() and d.name.startswith("[")
            ]
            assert len(dynamic) == 1, f"{folder} has no single [param] folder"
            folder = dynamic[0]
        else:
            folder = folder / part
            assert folder.is_dir(), f"no route folder {folder}"
    assert (folder / "page.tsx").is_file(), f"no page at {folder}"
