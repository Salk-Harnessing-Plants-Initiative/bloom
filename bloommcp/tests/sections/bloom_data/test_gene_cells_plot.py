"""scrna_gene_cells' UMAP: one PNG after the JSON when asked, never a failed call."""

from __future__ import annotations

import base64
import io

import numpy as np
import pytest
from fastmcp.tools import ToolResult
from PIL import Image
from scrna_expression_data import SITE

from bloom_mcp.contract.errors import BloomMCPError
from bloom_mcp.data_access.scrna_plots import _png
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
