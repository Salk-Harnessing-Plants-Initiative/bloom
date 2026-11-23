"""scrna_gene_cells' UMAP: one PNG after the JSON when asked, never a failed call."""

from __future__ import annotations

import base64
import io

import numpy as np
import pytest
from fastmcp.tools import ToolResult
from matplotlib.colors import to_rgb
from PIL import Image
from scrna_expression_data import SITE

from bloom_mcp.contract.errors import BloomMCPError
from bloom_mcp.data_access.scrna_plots import _png, umap_groups
from bloom_mcp.sections.bloom_data import gene_cells as tool
from bloom_mcp.sections.bloom_data import gene_cells_plot
from bloom_mcp.sections.bloom_data.gene_cells_reply import GeneCellsResult


def _run(**kw):
    return tool.scrna_gene_cells({"dataset_id": 3, "gene": "AT4G28110", **kw})


def _split(out):
    """The reply and its images, as a client receives them."""
    if not isinstance(out, ToolResult):
        return out, []
    images = [
        Image.open(io.BytesIO(base64.b64decode(b.data)))
        for b in out.content
        if b.type == "image"
    ]
    assert out.content[0].type == "text"
    return GeneCellsResult.model_validate(out.structured_content), images


@pytest.fixture
def drawn(monkeypatch):
    """The arguments the UMAP was drawn with."""
    seen = {}
    real = gene_cells_plot.umap.gene_figure

    def record(x, y, values, codes, names, focus, title):
        seen.update(values=values, codes=codes, names=names, focus=focus, title=title)
        return real(x, y, values, codes, names, focus, title)

    monkeypatch.setattr(gene_cells_plot.umap, "gene_figure", record)
    return seen


def test_a_umap_is_sent_after_the_json(expression_db):
    result, [image] = _split(_run(plot="umap"))
    assert [c.cell for c in result.cells] == [1, 2, 3]
    assert (image.format, image.size) == ("PNG", (1200, 900))
    assert result.page_url == f"{SITE}/app/expression/16/3?tab=umap&gene=AT4G28110"


def test_no_image_by_default(expression_db):
    assert isinstance(_run(), GeneCellsResult)


def test_every_cell_is_drawn_with_its_own_value_and_cell_type(expression_db, drawn):
    _run(plot="umap")
    assert list(drawn["values"]) == [0.0, 4.0, 2.0, 1.0, 0.0]
    assert list(drawn["codes"]) == [0, 0, 1, 1, -1]
    assert drawn["names"] == ["Cortex", "X"]
    assert drawn["focus"] is None
    assert drawn["title"] == "AT4G28110 in MYB41 root"


def test_named_cell_types_are_the_focus(expression_db, drawn):
    _run(plot="umap", cell_types=["x"])
    assert list(drawn["focus"]) == [False, False, True, True, False]


@pytest.mark.parametrize(
    "reason", [_png.BUSY, _png.TOO_LARGE, _png.FAILED, _png.OUT_OF_TIME]
)
def test_a_plot_that_cant_be_drawn_leaves_a_note_and_the_numbers(
    expression_db, monkeypatch, reason
):
    monkeypatch.setattr(
        gene_cells_plot._png, "draw", lambda build, deadline: _png.Drawn(None, reason)
    )
    result, images = _split(_run(plot="umap"))
    assert images == [] and reason in result.notes
    assert len(result.cells) == 3 and result.page_url


def test_a_drawing_error_never_fails_the_call(expression_db, monkeypatch):
    def broken(*args):
        raise ValueError("bad colour")

    monkeypatch.setattr(gene_cells_plot.umap, "gene_figure", broken)
    result, images = _split(_run(plot="umap"))
    assert images == [] and _png.FAILED in result.notes


@pytest.mark.parametrize("gene", ["NOPE1", "AT2G02020"])
def test_no_image_when_the_gene_has_no_values(expression_db, gene):
    result, images = _split(_run(plot="umap", gene=gene))
    assert images == [] and result.cells == []


def test_out_of_time_before_reading_draws_nothing(expression_db, monkeypatch):
    monkeypatch.setattr(tool, "CALL_SECONDS", -1)
    result, images = _split(_run(plot="umap"))
    assert images == [] and result.cells == []


def test_an_unknown_plot_is_refused_before_any_read(expression_db):
    reader, _ = expression_db
    with pytest.raises(BloomMCPError):
        _run(plot="volcano")
    assert reader.calls == []


def test_the_values_drawn_are_floats(expression_db, drawn):
    _run(plot="umap")
    assert isinstance(drawn["values"], np.ndarray) and drawn["values"].dtype == float


@pytest.fixture
def grouped(monkeypatch):
    """The arguments the group UMAP was drawn with."""
    seen = {}
    real = gene_cells_plot.umap.group_figure

    def record(x, y, groups, colours, none, names, codes, labels, focus, title, **kw):
        seen.update(groups=groups, colours=colours, none=none, names=names, title=title)
        seen.update(codes=codes, labels=labels, focus=focus)
        seen.update(kw)
        return real(
            x, y, groups, colours, none, names, codes, labels, focus, title, **kw
        )

    monkeypatch.setattr(gene_cells_plot.umap, "group_figure", record)
    return seen


def test_gene_colours_need_a_gene_before_any_read(expression_db):
    reader, _ = expression_db
    with pytest.raises(BloomMCPError):
        tool.scrna_gene_cells({"dataset_id": 3})
    assert reader.calls == []


def test_cell_type_colours_need_no_gene_or_plot(expression_db, grouped):
    out = tool.scrna_gene_cells({"dataset_id": 3, "color_by": "cell_type"})
    result, [image] = _split(out)
    assert (result.gene, result.cells, result.cells_in_scope) == (None, [], 5)
    assert result.cells_expressing is None
    assert result.page_url == f"{SITE}/app/expression/16/3?tab=umap"
    assert image.size == (1200, 900)
    assert grouped["title"] == "MYB41 root by cell type"


def test_cell_types_use_their_stored_colour_or_grey(expression_db, grouped):
    tool.scrna_gene_cells({"dataset_id": 3, "color_by": "cell_type"})
    assert list(grouped["groups"]) == [0, 0, 1, 1, -1]
    assert grouped["colours"] == [to_rgb("#1f77b4"), umap_groups.MISSING_COLOUR]
    assert grouped["none"] == (*umap_groups.NO_CELL_TYPE_COLOUR, 1.0)
    assert grouped["none_name"] == "No cell type"


def test_genotypes_take_the_palette_in_name_order(expression_db, grouped):
    tool.scrna_gene_cells({"dataset_id": 3, "color_by": "genotype"})
    assert grouped["names"] == ["Col-0", "pFACT"]
    assert list(grouped["groups"]) == [0, 1, 1, 0, -1]
    assert grouped["colours"] == umap_groups.palette(2)
    assert grouped["none"] == umap_groups.NO_VALUE


def test_the_same_call_gives_the_same_picture(expression_db):
    once = tool.scrna_gene_cells({"dataset_id": 3, "color_by": "replicate"})
    again = tool.scrna_gene_cells({"dataset_id": 3, "color_by": "replicate"})
    assert once.content[-1].data == again.content[-1].data


def test_no_genotype_recorded_greys_every_cell_with_a_note(expression_db, grouped):
    reader, _ = expression_db
    function, cells = reader.function_rows["scrna_cell_arrays"]
    for cell in cells:
        cell["genotype"] = None
    result, [_] = _split(
        tool.scrna_gene_cells({"dataset_id": 3, "color_by": "genotype"})
    )
    assert grouped["names"] == [] and list(grouped["groups"]) == [-1] * 5
    assert any("No cell records a genotype" in n for n in result.notes)


def test_twenty_five_replicates_get_twenty_five_colours(expression_db, grouped):
    reader, _ = expression_db
    function, _cells = reader.function_rows["scrna_cell_arrays"]
    many = [
        {"x": float(i), "y": 0.0, "cluster_ordinal": 0, "genotype": None,
         "replicate": f"r{i:02d}", "facets": None}
        for i in range(25)
    ]  # fmt: skip
    reader.function_rows["scrna_cell_arrays"] = (function, many)
    tool.scrna_gene_cells({"dataset_id": 3, "color_by": "replicate"})
    assert len(set(grouped["colours"])) == 25


def test_a_gene_with_group_colours_still_pages_its_cells(expression_db, grouped):
    result, [_] = _split(_run(color_by="genotype"))
    assert [c.cell for c in result.cells] == [1, 2, 3]
    assert result.page_url == f"{SITE}/app/expression/16/3?tab=umap"


def _unplace(db, *cells):
    reader, _ = db
    for i in cells:
        reader.function_rows["scrna_cell_arrays"][1][i].update(x=None, y=None)


def test_a_cell_with_no_umap_position_is_left_off_the_map(expression_db, drawn):
    _unplace(expression_db, 1)
    result, [_] = _split(_run(plot="umap", cell_types=["x"]))
    assert list(drawn["values"]) == [0.0, 2.0, 1.0, 0.0]
    assert list(drawn["codes"]) == [0, 1, 1, -1]
    assert list(drawn["focus"]) == [False, True, True, False]
    assert "1 cell has no UMAP position and is not drawn." in result.notes


def test_group_colours_leave_off_cells_with_no_position(expression_db, grouped):
    _unplace(expression_db, 0, 4)
    result, [_] = _split(
        tool.scrna_gene_cells({"dataset_id": 3, "color_by": "genotype"})
    )
    assert list(grouped["groups"]) == [1, 1, 0]
    assert "2 cells have no UMAP position and are not drawn." in result.notes


def test_no_cell_with_a_position_draws_nothing(expression_db):
    _unplace(expression_db, 0, 1, 2, 3, 4)
    result, images = _split(_run(plot="umap"))
    assert images == [] and len(result.cells) == 3
    assert "No cell has a UMAP position, so there is no plot." in result.notes


@pytest.mark.parametrize("color_by", ["cell_type", "genotype", "replicate"])
def test_group_colours_grey_the_cells_outside_the_named_cell_types(
    expression_db, grouped, color_by
):
    tool.scrna_gene_cells({"dataset_id": 3, "color_by": color_by, "cell_types": ["x"]})
    assert list(grouped["focus"]) == [False, False, True, True, False]


def test_group_colours_with_no_cell_types_grey_nothing(expression_db, grouped):
    tool.scrna_gene_cells({"dataset_id": 3, "color_by": "genotype"})
    assert grouped["focus"] is None


@pytest.mark.parametrize("color_by", ["cell_type", "genotype", "replicate"])
def test_group_colours_write_cell_type_names_on_the_map(
    expression_db, grouped, color_by
):
    tool.scrna_gene_cells({"dataset_id": 3, "color_by": color_by})
    assert list(grouped["codes"]) == [0, 0, 1, 1, -1]
    assert grouped["labels"] == ["Cortex", "X"]
