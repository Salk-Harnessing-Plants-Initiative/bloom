"""gene_lookup: HPI notes, orthologs, translation, links, emails and reply size."""

from __future__ import annotations

import pytest
from gene_lookup_data import (
    MYB41,
    MYB74,
    SOY,
    TABLES,
    lookup,
    lookup_in,
)

from bloom_mcp.data_access import gene_reads, table_access
from bloom_mcp.data_access.fake_table_reader import FakeTableReader
from bloom_mcp.sections.bloom_data.gene_lookup import gene_lookup


def test_hpi_notes_carry_the_scientists_and_newest_progress_without_emails(genes_db):
    [note] = lookup("MYB41").hpi_notes
    assert (note.status, note.category) == ("in-translation", "Suberin")
    assert note.scientists == ["Ada"]
    assert [p.message for p in note.progress] == [
        "T2 seeds, ask [email hidden]",
        "Lines sent",
    ]
    assert "@" not in note.model_dump_json()


def test_a_deleted_candidate_gives_no_notes(genes_db):
    assert lookup("MYB74").hpi_notes == []


def test_orthologs_are_grouped_by_assembly(genes_db):
    [group] = lookup("MYB41").orthologs
    assert group.ortho_group == "OG0001664"
    assert group.by_assembly == {"Athaliana.Col-0": [MYB74, MYB41], "Gmax.W82a5": [SOY]}
    assert group.total == 3 and group.complete


def test_translation_candidates_and_lines(genes_db):
    result = lookup("MYB41")
    [candidate] = result.translation_candidates
    assert (candidate.translation_candidate, candidate.ortho_group) == (
        SOY,
        "OG0001664",
    )
    [line] = result.translation_lines
    assert (line.name, line.accession, line.modification) == (
        "myb41-oe",
        "Col-0",
        "overexpression",
    )


def test_links_follow_site_url(genes_db, monkeypatch):
    assert lookup("MYB41").links.orthobrowser is None
    monkeypatch.setenv("SITE_URL", "https://bloom.example/")
    result = lookup("MYB41")
    assert (
        result.links.orthobrowser
        == "https://bloom.example/app/orthofinder?search=myb41"
    )
    assert result.links.genes_page == "https://bloom.example/app/genes"
    assert result.expression[0].link == "https://bloom.example/app/expression/1/14"


def test_the_orthobrowser_search_falls_back_to_the_gene_id(genes_db, monkeypatch):
    monkeypatch.setenv("SITE_URL", "https://bloom.example")
    link = lookup(SOY).links.orthobrowser
    assert (
        link
        == "https://bloom.example/app/orthofinder?search=gmax.w82a5.hpi3.chr19.g456830"
    )


def test_every_read_uses_readable_visible_columns():
    for table, columns in gene_reads.COLUMNS.items():
        assert table_access.is_readable(table), table
        assert table_access.visible_columns(table, list(columns)) == list(columns), (
            table
        )


@pytest.mark.parametrize("column", ["message", "links", "images", "tags"])
def test_progress_log_emails_are_hidden_from_every_data_tool(column):
    assert table_access.is_readable("experiment_progress_logs")
    assert "user_email" in table_access.HIDDEN_COLUMNS["experiment_progress_logs"]
    assert column in table_access.redacted_columns("experiment_progress_logs")
    assert column in table_access.unfilterable_columns("experiment_progress_logs")


def test_every_read_masks_the_columns_the_data_tools_mask(monkeypatch):
    monkeypatch.setitem(gene_reads.COLUMNS, "gene_candidates", ("gene", "status_logs"))
    fake = FakeTableReader(
        {"gene_candidates": [{"gene": "G1", "status_logs": [{"by": "ada@salk.edu"}]}]}
    )
    rows, _ = gene_reads.Reads(fake).page("gene_candidates", [])
    assert "@" not in str(rows)


def test_a_long_answer_is_cut_to_fit_one_reply():
    from bloom_mcp.sections.bloom_data._page import MAX_REPLY_CHARS, TRUNCATED

    ids = [f"G{i:05d}" for i in range(gene_reads.MAX_GENES)]
    tables = {t: [] for t in TABLES} | {
        "genes": [{"gene_id": g, "symbol": "DUP", "reference_id": None} for g in ids],
        "gene_candidates": [
            {"gene": g, "evidence_description": "e" * 5000, "deleted_at": None}
            for g in ids
        ],
        "experiment_progress_logs": [
            {
                "id": i,
                "gene": g,
                "timestamp": f"2026-01-{i + 1:02d}",
                "message": "m" * 3000,
            }
            for g in ids
            for i in range(gene_reads.MAX_PROGRESS)
        ],
    }
    result = lookup_in(FakeTableReader(tables), "DUP")
    assert len(result.model_dump_json()) <= MAX_REPLY_CHARS
    assert result.hpi_notes[0].evidence_description.endswith(TRUNCATED)
    assert any("shortened" in n.lower() for n in result.notes)


def test_a_slow_read_gives_advice_the_lookup_can_follow(genes_db):
    from bloom_mcp.contract.errors import BloomMCPError

    genes_db.slow_tables = {"scrna_de"}
    with pytest.raises(BloomMCPError) as err:
        lookup("MYB41")
    text = f"{err.value.message} {err.value.remedy}".lower()
    assert "gene lookup" in text
    for advice in ("add a filter", "list_tables", "in list", "contains"):
        assert advice not in text


def test_the_description_puts_bloom_first_and_labels_outside_knowledge():
    doc = " ".join((gene_lookup.__doc__ or "").split())
    assert "not from Bloom" in doc and "Salk" in doc and "HPI" in doc
