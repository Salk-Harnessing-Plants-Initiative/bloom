"""gene_lookup: what Bloom knows about one gene, found the way Bloom's pages find it."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from bloom_mcp.data_access import gene_reads, table_access
from bloom_mcp.data_access.fake_table_reader import FakeTableReader
from bloom_mcp.sections.bloom_data.gene_lookup import gene_lookup
from bloom_mcp.tools import _ports

MYB41 = "AT4G28110.Araport11.447"
MYB74 = "AT4G05100.Araport11.447"
SOY = "Gmax.W82a5.HPI3.Chr19.g456830"
MYB410 = "AT4G28111.Araport11.447"
_WEB = Path(__file__).resolve().parents[4] / "web" / "components"


def _de(id_, run, kind, group1, group2, n1, n2, cluster=None, tested=True):
    return {
        "id": id_, "run_id": run, "dataset_id": 14, "group_kind": kind,
        "cluster_id": cluster, "group1": group1, "group2": group2,
        "n_group1": n1, "n_group2": n2, "tested": tested,
    }  # fmt: skip


def _hit(de_id, log2fc, fdr):
    return {"de_id": de_id, "gene_id": 100, "log2fc": log2fc, "fdr": fdr,
            "pct_1": 0.8, "pct_2": 0.1, "dataset_id": 14}  # fmt: skip


TABLES = {
    "genes": [
        {"gene_id": MYB41, "symbol": "MYB41", "standard_name": "ATC2-myb41-3",
         "short_description": "myb domain protein 41", "ortho_group": "OG0001664",
         "reference_id": 1},
        {"gene_id": MYB74, "symbol": "MYB74", "standard_name": "ATC2-myb41-1",
         "short_description": None, "ortho_group": "OG0001664", "reference_id": 1},
        {"gene_id": SOY, "symbol": None, "standard_name": None,
         "short_description": None, "ortho_group": "OG0001664", "reference_id": 2},
        {"gene_id": MYB410, "symbol": "MYB410", "standard_name": None,
         "short_description": None, "ortho_group": None, "reference_id": 1},
    ],
    "assemblies": [
        {"id": 1, "prefix": "Athaliana.Col-0", "species_id": 1},
        {"id": 2, "prefix": "Gmax.W82a5", "species_id": 2},
    ],
    "gene_candidates": [
        {"gene": MYB41, "status": "in-translation", "category": "Suberin",
         "evidence_description": "Suberin in roots", "experiment_plans_and_progress": None,
         "publication_status": False, "disclosed_to_otd": True,
         "translation_approval_date": None, "deleted_at": None},
        {"gene": MYB74, "status": "suspected", "category": None,
         "evidence_description": None, "experiment_plans_and_progress": None,
         "publication_status": None, "disclosed_to_otd": None,
         "translation_approval_date": None, "deleted_at": "2026-01-01T00:00:00Z"},
    ],
    "gene_candidate_scientists": [{"id": 1, "gene_candidate_id": MYB41, "scientist_id": 1}],
    "people": [{"id": 1, "name": "Ada", "email": "ada@salk.edu"}],
    "experiment_progress_logs": [
        {"id": 1, "gene": MYB41, "timestamp": "2026-02-01T00:00:00Z",
         "message": "Lines sent", "user_email": "ada@salk.edu"},
        {"id": 2, "gene": MYB41, "timestamp": "2026-03-01T00:00:00Z",
         "message": "T2 seeds, ask ada@salk.edu", "user_email": "ada@salk.edu"},
    ],
    "translation_candidates": [{"id": 1, "gene_candidate": MYB41, "translation_candidate": SOY}],
    "translation_lines": [
        {"id": 1, "gene_id": MYB41, "name": "myb41-oe", "accession_id": 5,
         "modification_description": "overexpression"},
    ],
    "accessions": [{"id": 5, "name": "Col-0"}],
    "scrna_datasets": [
        {"id": 14, "name": "MYB41 roots", "species_id": 1, "kind": "full", "deleted_at": None},
        {"id": 16, "name": "Atlas", "species_id": 1, "kind": "reference", "deleted_at": None},
    ],
    "scrna_genes": [
        {"id": 100, "dataset_id": 14, "gene_name": "AT4G28110", "gene_number": 1},
        {"id": 200, "dataset_id": 15, "gene_name": "AT4G28110", "gene_number": 1},
        {"id": 300, "dataset_id": 16, "gene_name": "AT1G01010", "gene_number": 1},
    ],
    "scrna_de_runs": [
        {"id": 1, "dataset_id": 14, "status": "complete", "completed_at": "2026-01-01"},
        {"id": 2, "dataset_id": 14, "status": "complete", "completed_at": "2026-02-01"},
        {"id": 3, "dataset_id": 14, "status": "failed", "completed_at": None},
    ],
    "scrna_de": [
        _de(10, 1, "cluster", "c3", "rest", 50, 500, cluster="c3"),
        _de(11, 2, "cluster", "c4", "rest", 60, 490, cluster="c4"),
        _de(12, 1, "cluster", "c4", "rest", 60, 490, cluster="c4"),
        _de(13, 3, "cluster", "c3", "rest", 50, 500, cluster="c3"),
        _de(14, 2, "genotype", "pFACT", "Col-0", 300, 300),
        _de(15, 2, "cluster", "c5", "rest", 40, 510, cluster="c5"),
        _de(16, 2, "cluster", "c4", "rest", 10, 100, cluster="c4"),
        _de(17, 1, "genotype", "old", "Col-0", 300, 300),
    ],
    "scrna_de_genes": [
        _hit(10, 2.0, 0.001), _hit(11, 1.5, 0.01), _hit(12, 3.0, 1e-5), _hit(13, 4.0, 1e-6),
        _hit(14, 1.2, 0.001), _hit(15, -2.0, 0.001), _hit(16, 9.0, 1e-9),
        _hit(17, 5.0, 1e-9),
    ],
    "scrna_clusters": [
        {"id": 1, "dataset_id": 14, "cluster_id": "c3", "name": "Endodermis", "source": "nuclei"},
        {"id": 2, "dataset_id": 14, "cluster_id": "c4", "name": "Cortex", "source": None},
        {"id": 3, "dataset_id": 14, "cluster_id": "c5", "name": "Xylem", "source": None},
    ],
    "scrna_genotypes": [
        {"id": 1, "dataset_id": 14, "name": "pFACT", "construct": "pFACT::MYB41"},
        {"id": 2, "dataset_id": 14, "name": "Col-0", "construct": None},
    ],
}  # fmt: skip


@pytest.fixture
def genes_db():
    import copy

    fake = FakeTableReader(copy.deepcopy(TABLES))
    previous = _ports.table_reader()
    _ports.configure(table_reader=fake)
    yield fake
    _ports.configure(table_reader=previous)


def lookup(gene: str):
    return gene_lookup({"gene": gene})


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
    previous = _ports.table_reader()
    _ports.configure(table_reader=fake)
    try:
        result = lookup("co")
    finally:
        _ports.configure(table_reader=previous)
    assert [g.gene_id for g in result.genes] == ["G99999"]
    assert not any("incomplete" in n for n in result.notes)


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


def test_only_live_datasets_that_measured_the_gene_are_listed(genes_db):
    assert [d.dataset_id for d in lookup("MYB41").expression] == [14]


def test_markers_use_the_newest_run_with_the_comparison_over_most_cells(genes_db):
    [dataset] = lookup("MYB41").expression
    markers = {m.cluster_id: m for m in dataset.markers}
    assert set(markers) == {"c3", "c4"}
    # c3 has no comparison in the newest run, so run 1's; c4 the larger of run 2's two.
    assert markers["c3"].log2fc == 2.0
    assert markers["c4"].log2fc == 1.5
    assert (markers["c3"].cell_type, markers["c3"].label_source) == (
        "Endodermis",
        "nuclei",
    )
    assert (markers["c4"].pct_in_cluster, markers["c4"].pct_elsewhere) == (0.8, 0.1)


def test_a_gene_down_in_a_cluster_is_not_its_marker(genes_db):
    [dataset] = lookup("MYB41").expression
    assert "c5" not in {m.cluster_id for m in dataset.markers}


def test_genotype_comparisons_come_from_the_newest_run_that_has_them(genes_db):
    [dataset] = lookup("MYB41").expression
    [comparison] = dataset.genotype_comparisons
    assert (comparison.group1, comparison.group2, comparison.direction) == (
        "pFACT",
        "Col-0",
        "up",
    )
    assert not comparison.absent_in_one_group


def test_a_construct_naming_the_gene_flags_the_dataset(genes_db):
    [dataset] = lookup("MYB41").expression
    assert dataset.constructs == ["pFACT::MYB41"]


def test_a_gene_missing_from_the_gene_table_still_shows_its_expression(genes_db):
    result = lookup("AT1G01010")
    assert result.genes == []
    assert [d.dataset_id for d in result.expression] == [16]
    assert result.expression[0].kind == "reference"
    assert any("gene table" in n for n in result.notes)


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


def test_an_unknown_gene_answers_empty_with_a_note(genes_db):
    result = lookup("NOPE1")
    assert result.genes == [] and result.expression == [] and result.hpi_notes == []
    assert result.notes


def test_a_blank_name_is_refused(genes_db):
    from bloom_mcp.contract.errors import BloomMCPError

    with pytest.raises(BloomMCPError) as err:
        lookup("  ")
    assert err.value.code == "invalid_input"


def test_every_read_uses_readable_visible_columns():
    for table, columns in gene_reads.COLUMNS.items():
        assert table_access.is_readable(table), table
        assert table_access.visible_columns(table, list(columns)) == list(columns), (
            table
        )


def test_progress_log_emails_are_hidden_from_every_data_tool():
    assert table_access.is_readable("experiment_progress_logs")
    assert "user_email" in table_access.HIDDEN_COLUMNS["experiment_progress_logs"]
    assert "message" in table_access.redacted_columns("experiment_progress_logs")
    assert "message" in table_access.unfilterable_columns("experiment_progress_logs")


def test_the_description_puts_bloom_first_and_labels_outside_knowledge():
    doc = " ".join((gene_lookup.__doc__ or "").split())
    assert "not from Bloom" in doc and "Salk" in doc and "HPI" in doc


@pytest.mark.skipif(not _WEB.is_dir(), reason="the web app isn't checked out here")
def test_the_cuts_match_the_websites():
    def const(path: str, name: str) -> float:
        match = re.search(rf"const {name} = ([\d.]+);", (_WEB / path).read_text())
        assert match, name
        return float(match.group(1))

    assert gene_reads.MARKER_FDR_CUT == const(
        "expression-lib/cluster-markers.ts", "MARKER_FDR_CUT"
    )
    assert gene_reads.DE_FDR_CUT == const(
        "expression-lib/de-types.ts", "DEFAULT_FDR_CUT"
    )
    assert gene_reads.DE_LOG2FC_CUT == const(
        "expression-lib/de-types.ts", "DEFAULT_LOG2FC_CUT"
    )
    assert gene_reads.OFF_SCALE_LOG2FC == const(
        "expression-differential-analysis.tsx", "OFF_SCALE_LOG2FC"
    )
