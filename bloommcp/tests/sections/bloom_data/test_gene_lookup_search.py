"""gene_lookup: finding the gene by symbol or id."""

from __future__ import annotations

import pytest
from gene_lookup_data import (
    MYB41,
    MYB410,
    SOY,
    TABLES,
    lookup,
    lookup_in,
)

from bloom_mcp.data_access import gene_reads
from bloom_mcp.data_access.fake_table_reader import FakeTableReader


def test_a_symbol_matches_exactly_not_by_line_name(genes_db):
    result = lookup("myb41")
    assert [g.gene_id for g in result.genes] == [MYB41]


def test_an_agi_id_without_its_annotation_suffix_finds_the_gene(genes_db):
    assert [g.gene_id for g in lookup("at4g28110").genes] == [MYB41]


def test_a_crop_gene_matches_by_its_full_id(genes_db):
    result = lookup(SOY)
    assert [g.gene_id for g in result.genes] == [SOY]
    assert result.genes[0].assembly == "Gmax.W82a5"


@pytest.mark.parametrize("name", ["AT4G28110.1", "at4g28110.2", MYB41.lower()])
def test_an_agi_with_a_transcript_number_or_full_id_finds_the_gene(genes_db, name):
    result = lookup(name)
    assert [g.gene_id for g in result.genes] == [MYB41]
    assert [d.dataset_id for d in result.expression] == [14]


def test_a_crop_id_with_a_transcript_number_finds_the_gene(genes_db):
    assert [g.gene_id for g in lookup(SOY.upper() + ".1").genes] == [SOY]


@pytest.mark.parametrize("name", ["YB41", "MYB4", "AT4G2811", "AT4G28110.Araport"])
def test_a_part_of_a_name_or_id_matches_nothing(genes_db, name):
    assert lookup(name).genes == []


def test_a_symbol_does_not_match_a_longer_one(genes_db):
    assert [g.gene_id for g in lookup("myb41").genes] == [MYB41]
    assert [g.gene_id for g in lookup("MYB410").genes] == [MYB410]


def test_an_scrna_only_gene_with_a_transcript_number_shows_its_expression(genes_db):
    assert [d.dataset_id for d in lookup("at1g01010.1").expression] == [16]


def test_a_short_symbol_is_found_however_many_genes_contain_it():
    many = [
        {"gene_id": f"G{i:05d}", "symbol": f"COL{i}", "reference_id": None}
        for i in range(gene_reads.MAX_ROWS + 1)
    ]
    co = {"gene_id": "G99999", "symbol": "CO", "reference_id": None}
    fake = FakeTableReader({t: [] for t in TABLES} | {"genes": [*many, co]})
    result = lookup_in(fake, "co")
    assert [g.gene_id for g in result.genes] == ["G99999"]
    assert not any("incomplete" in n for n in result.notes)


def test_more_genes_than_the_cap_are_noted():
    many = [
        {"gene_id": f"G{i:05d}", "symbol": "DUP", "reference_id": None}
        for i in range(gene_reads.MAX_GENES + 1)
    ]
    fake = FakeTableReader({t: [] for t in TABLES} | {"genes": many})
    result = lookup_in(fake, "DUP")
    assert len(result.genes) == gene_reads.MAX_GENES
    assert any("incomplete" in n and "genes" in n for n in result.notes)


def test_an_unknown_gene_answers_empty_with_a_note(genes_db):
    result = lookup("NOPE1")
    assert result.genes == [] and result.expression == [] and result.hpi_notes == []
    assert result.notes


def test_a_blank_name_is_refused(genes_db):
    from bloom_mcp.contract.errors import BloomMCPError

    with pytest.raises(BloomMCPError) as err:
        lookup("  ")
    assert err.value.code == "invalid_input"
