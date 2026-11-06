"""Links back to Bloom's web pages, built from SITE_URL."""

from __future__ import annotations

import pytest

from bloom_mcp import bloom_links

SITE = "https://bloom.example.org"


@pytest.fixture(autouse=True)
def _site(monkeypatch):
    monkeypatch.setenv("SITE_URL", SITE + "/")


def test_cylinder_pages():
    assert bloom_links.cyl_experiment(1, 7) == f"{SITE}/app/phenotypes/1/7"
    assert bloom_links.cyl_scan(1, 7, 3, 12, 99) == f"{SITE}/app/phenotypes/1/7/3/12/99"


def test_plate_pages_quote_the_plate_id():
    assert bloom_links.plate_experiment(2, 5) == f"{SITE}/app/plate-phenotypes/2/5"
    assert bloom_links.plate_wave(2, 5, 1) == f"{SITE}/app/plate-phenotypes/2/5/wave/1"
    assert (
        bloom_links.plate(2, 5, 1, "P 01/a")
        == f"{SITE}/app/plate-phenotypes/2/5/wave/1/P%2001%2Fa"
    )


def test_expression_rnaseq_and_trait_pages():
    assert bloom_links.expression_dataset(1, 4) == f"{SITE}/app/expression/1/4"
    assert bloom_links.rnaseq_run("run-8") == f"{SITE}/app/rnaseq-runs/run-8"
    assert bloom_links.traits_experiment(1, 7) == f"{SITE}/app/traits/1/7"


def test_no_link_without_site_url(monkeypatch):
    monkeypatch.delenv("SITE_URL")
    assert bloom_links.cyl_experiment(1, 7) is None
