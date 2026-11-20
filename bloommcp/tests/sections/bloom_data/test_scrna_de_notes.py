"""scrna_de's notes on cell types: why a list is empty names what was asked for."""

from __future__ import annotations

from test_scrna_de import _call, _cell, db  # noqa: F401  (db is a fixture)


def test_a_contrast_a_cell_type_lacks_is_named_in_its_note(db):  # noqa: F811
    # c4 has a myb41_vs_Col-0 comparison, but no oe_vs_Col-0 one.
    result = _call(cell_types=["c1", "c4"], kind="genotype", contrast="oe_vs_Col-0")
    assert _cell(result, "c4").note == "No oe_vs_Col-0 comparison for this cell type."
    assert _cell(result, "c1").note is None


def test_without_a_contrast_the_note_says_no_genotype_comparison(db):  # noqa: F811
    c7 = _cell(_call(cell_types=["c7"], kind="genotype"), "c7")
    assert c7.note == "No genotype comparison for this cell type."
