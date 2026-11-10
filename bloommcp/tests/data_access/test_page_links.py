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


def test_every_link_names_the_route_param_of_each_placeholder():
    for table, pattern in page_links.PAGE_LINKS.items():
        assert set(_PLACEHOLDER.findall(pattern)) == set(page_links.PAGE_PARAMS[table])


def test_a_link_note_says_to_skip_empty_columns(monkeypatch):
    monkeypatch.setenv("SITE_URL", " https://bloom.example/ ")
    note = page_links.link_note("rnaseq_runs")
    assert note is not None
    assert "https://bloom.example/app/rnaseq-runs/{id}" in note
    assert "give none when any of those columns is empty" in note


@pytest.mark.parametrize("table", sorted(page_links.PAGE_LINKS))
def test_link_columns_are_readable_and_not_hidden(table):
    assert table_access.is_readable(table)
    columns = _PLACEHOLDER.findall(page_links.PAGE_LINKS[table])
    assert table_access.visible_columns(table, columns) == columns


@pytest.mark.skipif(not _ROUTES.is_dir(), reason="the web app isn't checked out here")
@pytest.mark.parametrize("table", sorted(page_links.PAGE_LINKS))
def test_every_link_is_a_page_on_the_website(table):
    folder = _ROUTES
    for part in page_links.PAGE_LINKS[table].strip("/").split("/"):
        placeholder = _PLACEHOLDER.fullmatch(part)
        if placeholder:
            param = page_links.PAGE_PARAMS[table][placeholder.group(1)]
            folder = folder / f"[{param}]"
            assert folder.is_dir(), f"{{{placeholder.group(1)}}} doesn't fill {folder}"
        else:
            folder = folder / part
            assert folder.is_dir(), f"no route folder {folder}"
    assert (folder / "page.tsx").is_file(), f"no page at {folder}"


def test_trait_figures_link_to_the_traits_page_on_their_wave_and_age(monkeypatch):
    monkeypatch.setenv("SITE_URL", "https://bloom.example")
    for name in ("cyl_scan_traits_latest", "get_scan_traits"):
        note = page_links.link_note(name)
        assert note is not None
        assert (
            "https://bloom.example/app/traits/{species_id}/{experiment_id}"
            "?wave={wave_number}&age={plant_age_days}" in note
        )
        assert "picks the trait there" in note
    assert "app/traits" not in (page_links.link_note("cyl_experiments") or "")


@pytest.mark.skipif(not _ROUTES.is_dir(), reason="the web app isn't checked out here")
def test_the_traits_page_reads_wave_and_age_from_the_link():
    page = _ROUTES / "app" / "traits" / "[speciesId]" / "[experimentId]"
    assert (page / "page.tsx").is_file()
    params = (page / "parse-search-params.ts").read_text()
    assert "searchParams.wave" in params and "searchParams.age" in params


def test_trait_figure_link_columns_are_readable():
    assert table_access.visible_columns("cyl_experiments", ["species_id", "id"]) == [
        "species_id",
        "id",
    ]
    assert table_access.visible_columns(
        "cyl_scans_extended", ["wave_number", "plant_age_days"]
    ) == ["wave_number", "plant_age_days"]
