"""scrna_de: one dataset's cell-type markers and genotype DE, as its Bloom page shows them."""

from __future__ import annotations

import copy

import pytest
from data_access.scrna_de_data import TABLES

from bloom_mcp.contract.errors import BloomMCPError
from bloom_mcp.data_access import gene_reads
from bloom_mcp.data_access.fake_table_reader import FakeTableReader
from bloom_mcp.sections.bloom_data import scrna_de as tool
from bloom_mcp.tools import _ports

SITE = "https://bloom.example"


@pytest.fixture
def db():
    fake = FakeTableReader(copy.deepcopy(TABLES))
    previous = _ports.table_reader()
    _ports.configure(table_reader=fake)
    yield fake
    _ports.configure(table_reader=previous)


def _call(**kw):
    return tool.scrna_de({"dataset_id": 14, **kw})


def _cell(result, cluster_id):
    return next(c for c in result.cell_types if c.cluster_id == cluster_id)


def _genes(genes):
    return [g.gene for g in genes]


# The dataset


@pytest.mark.parametrize(
    ("dataset_id", "match"),
    [
        (15, "No scRNA dataset 15"),
        (99, "No scRNA dataset 99"),
        (16, "reference atlas"),
    ],
)
def test_deleted_missing_and_reference_datasets_are_refused(db, dataset_id, match):
    with pytest.raises(BloomMCPError, match=match):
        tool.scrna_de({"dataset_id": dataset_id})


def test_links_open_the_de_tab_and_the_cell_types(db, monkeypatch):
    monkeypatch.setenv("SITE_URL", SITE)
    result = _call()
    assert result.de_tab_url == f"{SITE}/app/expression/1/14?tab=de"
    assert result.cell_types_url == f"{SITE}/app/expression/1/14?tab=umap"


def test_no_links_without_a_site(db):
    result = _call()
    assert result.de_tab_url is None and result.cell_types_url is None


# Cell types


def test_a_name_returns_every_cluster_carrying_it_in_the_websites_order(db):
    result = _call(cell_types=["cortex"])
    assert [(c.cluster_id, c.name) for c in result.cell_types] == [
        ("c4", "Cortex"),
        ("c1", "Cortex"),
    ]


def test_unknown_names_are_listed(db):
    result = _call(cell_types=["c7", "Stele"])
    assert [c.cluster_id for c in result.cell_types] == ["c7"]
    assert result.unknown_cell_types == ["Stele"]


def test_without_names_the_first_10_are_read_and_the_rest_named(db):
    result = _call()
    assert len(result.cell_types) == 10
    assert result.left_out_cell_types == ["c2", "c1"]


def test_more_than_10_names_are_refused(db):
    with pytest.raises(BloomMCPError):
        _call(cell_types=[f"c{n}" for n in range(1, 12)])


# Markers


def test_markers_are_the_cluster_panels(db):
    result = _call(cell_types=["Cortex"])
    c4, c1 = _cell(result, "c4").markers, _cell(result, "c1").markers
    assert (c4.source, c4.significant, _genes(c4.genes)) == (
        "stored",
        7,
        ["BETA", "BARE", "HUGE"],
    )
    assert (c1.source, c1.significant, _genes(c1.genes)) == (
        "comparison",
        3,
        ["atMYB41", "ZETA", "MID"],
    )


def test_marker_genes_carry_their_shares_and_flags(db):
    c4 = _cell(_call(cell_types=["c4"]), "c4").markers
    beta, _, huge = c4.genes
    assert (beta.symbol, beta.pct_in_cell_type, beta.pct_elsewhere) == ("B1", 0.8, 0.1)
    assert huge.likely_found_in_one_group_only and not huge.found_in_one_group_only


def test_a_cell_type_without_markers_says_so(db):
    c7 = _cell(_call(cell_types=["c7"]), "c7")
    assert c7.markers is None and "no markers" in c7.note.lower()


def test_markers_alone_read_no_genotype_comparisons(db):
    result = _call(cell_types=["c1"])
    assert _cell(result, "c1").genotype == [] and result.whole_dataset == []


# Genotype comparisons


def _side(comparison, group):
    return next(s for s in comparison.sides if s.higher_in == group)


def test_genotype_genes_are_named_by_the_group_they_are_higher_in(db):
    [tested, untested] = _cell(_call(cell_types=["c1"], kind="genotype"), "c1").genotype
    assert (tested.contrast, tested.group1, tested.group2) == (
        "myb41_vs_Col-0",
        "myb41",
        "Col-0",
    )
    assert (tested.cells_group1, tested.cells_group2, tested.method) == (
        200,
        210,
        "wilcoxon",
    )
    up, down = _side(tested, "myb41"), _side(tested, "Col-0")
    assert (_genes(up.genes), up.passing) == (["MID", "ZETA"], 2)
    assert (_genes(down.genes), down.passing) == (["ALPHA", "DOWN1"], 2)
    assert untested.contrast == "oe_vs_Col-0"


def test_shares_are_for_the_side_the_gene_is_higher_in(db):
    [tested, _] = _cell(_call(cell_types=["c1"], kind="genotype"), "c1").genotype
    alpha = _side(tested, "Col-0").genes[0]
    # Fixture rows hold pct_1 0.8 (myb41) and pct_2 0.1 (Col-0).
    assert (alpha.pct_in_group, alpha.pct_in_other) == (0.1, 0.8)


def test_an_untested_comparison_says_not_tested_with_no_genes(db):
    c4 = _cell(_call(cell_types=["c4"], kind="genotype"), "c4")
    [comparison] = c4.genotype
    assert not comparison.tested and comparison.sides == []
    assert "not tested" in comparison.note.lower()


def test_a_contrast_keeps_only_its_comparisons(db):
    result = _call(cell_types=["c1"], kind="genotype", contrast="oe_vs_Col-0")
    assert [c.contrast for c in _cell(result, "c1").genotype] == ["oe_vs_Col-0"]


def test_top_limits_the_genes_but_not_the_count(db):
    [tested, _] = _cell(_call(cell_types=["c1"], kind="genotype", top=1), "c1").genotype
    up = _side(tested, "myb41")
    assert _genes(up.genes) == ["MID"] and up.passing == 2


def test_whole_dataset_comparisons_only_when_no_cell_type_is_named(db):
    named = _call(cell_types=["c1"], kind="genotype")
    assert named.whole_dataset == []
    [whole] = _call(kind="genotype").whole_dataset
    assert _genes(_side(whole, "myb41").genes) == ["atMYB41", "ATMYB41", "ATZ1"]


def test_genotype_alone_reads_no_markers(db):
    c1 = _cell(_call(cell_types=["c1"], kind="genotype"), "c1")
    assert c1.markers is None and c1.note is None


def test_both_returns_markers_and_genotype(db):
    c1 = _cell(_call(cell_types=["c1"], kind="both"), "c1")
    assert c1.markers is not None and len(c1.genotype) == 2


# Limits


def test_comparisons_past_the_cap_are_listed_without_genes(db, monkeypatch):
    monkeypatch.setattr(tool, "MAX_COMPARISONS", 0)
    result = _call(cell_types=["c1"], kind="genotype")
    [skipped, untested] = _cell(result, "c1").genotype
    assert skipped.tested and skipped.sides == [] and "not read" in skipped.note.lower()
    assert "not tested" in untested.note.lower()
    assert any("not read" in n for n in result.notes)


def test_the_call_deadline_stops_new_reads(db, monkeypatch):
    monkeypatch.setattr(tool, "CALL_SECONDS", -1)
    result = _call(cell_types=["c1"], kind="both")
    c1 = _cell(result, "c1")
    assert c1.markers is None and all(not c.sides for c in c1.genotype)
    assert any("time" in n for n in result.notes)


def test_a_long_reply_drops_genotype_genes_before_markers(db, monkeypatch):
    full = _call(cell_types=["c1"], kind="both")
    size = len(full.model_dump_json())
    monkeypatch.setattr(tool, "MAX_REPLY_CHARS", size - 1)
    result = _call(cell_types=["c1"], kind="both")
    c1 = _cell(result, "c1")
    assert len(result.model_dump_json()) <= size - 1
    assert c1.markers.genes and any(not s.genes for c in c1.genotype for s in c.sides)
    assert any("fit one reply" in n for n in result.notes)


def test_markers_are_dropped_last_when_nothing_else_fits(db, monkeypatch):
    monkeypatch.setattr(tool, "MAX_REPLY_CHARS", 1500)
    result = _call(cell_types=["Cortex"])
    assert len(result.model_dump_json()) <= 1500
    assert any("fit one reply" in n for n in result.notes)


def test_a_cut_read_marks_the_reply_incomplete(db, monkeypatch):
    monkeypatch.setattr(gene_reads, "IN_CHUNK", 1)
    result = _call(cell_types=["c1"])
    assert result.incomplete and any("incomplete" in n for n in result.notes)


def test_a_full_read_is_complete(db):
    assert not _call(cell_types=["c1"]).incomplete


def test_a_slow_read_says_how_to_narrow_the_call(db):
    db.slow_tables.add("scrna_de_genes")
    with pytest.raises(BloomMCPError, match="fewer cell types") as err:
        _call(cell_types=["c1"], kind="genotype")
    assert "gene lookup" not in str(err.value)


# Description


def test_the_description_says_where_the_lists_come_from():
    doc = " ".join((tool.scrna_de.__doc__ or "").split()).lower()
    assert "cluster panel" in doc and "de_tab_url" in doc and "fdr < 0.05" in doc
