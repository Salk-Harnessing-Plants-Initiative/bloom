"""scrna_plots.umap_groups and umap.group_figure: the UMAP coloured by cell type, genotype or
replicate, in the website's colours."""

from __future__ import annotations

import colorsys

import numpy as np
import pytest
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.colors import to_rgb

from bloom_mcp.data_access.scrna_plots import umap, umap_groups


def test_the_first_twenty_colours_are_the_integrations_palette():
    colours = umap_groups.palette(20)
    assert colours == [to_rgb(h) for h in umap_groups.BASE_PALETTE]
    assert umap_groups.BASE_PALETTE[:2] == ("#4e79a7", "#f28e2b")


def test_later_colours_go_round_the_wheel_by_the_golden_angle():
    later = umap_groups.palette(22)[20:]
    for i, colour in enumerate(later):
        hue = (i * 137.508) % 360 / 360
        assert colour == pytest.approx(colorsys.hls_to_rgb(hue, 0.62, 0.6), abs=1e-9)
    assert len(set(umap_groups.palette(25))) == 25


@pytest.mark.parametrize(
    "stored,rgb",
    [
        ("#1f77b4", to_rgb("#1f77b4")),
        ("1f77b4", to_rgb("#1f77b4")),
        (None, umap_groups.MISSING_COLOUR),
        ("teal-ish", umap_groups.MISSING_COLOUR),
        ("#zz0000", umap_groups.MISSING_COLOUR),
        ("#fff", umap_groups.MISSING_COLOUR),
    ],
)
def test_a_stored_colour_or_grey(stored, rgb):
    assert umap_groups.stored_colour(stored) == pytest.approx(rgb)


def test_values_in_name_order_and_no_value_apart():
    codes, names = umap_groups.codes(["pFACT", None, "Col-0", "pFACT", ""])
    assert names == ["Col-0", "pFACT"]
    assert list(codes) == [1, -1, 0, 1, -1]


def _figure(codes, colours, names=("A", "B"), focus=None):
    n = len(codes)
    return umap.group_figure(
        np.arange(n, dtype=float),
        np.zeros(n),
        np.asarray(codes),
        list(colours),
        umap_groups.NO_VALUE,
        list(names),
        np.asarray(codes),
        list(names),
        None if focus is None else np.asarray(focus, dtype=bool),
        "t",
    )


def _layer(fig, gid):
    [ax] = [a for a in fig.axes if a.get_label() == umap.MAP_AXES]
    [layer] = [c for c in ax.collections if c.get_gid() == gid]
    return layer


def test_each_cell_is_its_groups_colour_in_stored_order():
    red, blue = (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)
    layer = _layer(_figure([1, 0, -1, 1], [red, blue]), umap.GROUPS)
    assert [tuple(p) for p in layer.get_offsets()] == [(i, 0.0) for i in range(4)]
    colours = layer.get_facecolors()
    assert np.allclose(colours[0], (*blue, 1.0)) and np.allclose(
        colours[1], (*red, 1.0)
    )
    assert np.allclose(colours[2], umap_groups.NO_VALUE)


def test_a_legend_names_every_group():
    fig = _figure([0, 1], [(1, 0, 0), (0, 0, 1)])
    [ax] = [a for a in fig.axes if a.get_label() == umap.MAP_AXES]
    assert [t.get_text() for t in ax.get_legend().get_texts()] == ["A", "B"]


def test_a_focus_greys_the_other_cells_first():
    fig = _figure([0, 1], [(1, 0, 0), (0, 0, 1)], focus=[True, False])
    [ax] = [a for a in fig.axes if a.get_label() == umap.MAP_AXES]
    assert [c.get_gid() for c in ax.collections] == [umap.OUTSIDE, umap.GROUPS]


def test_greys_are_the_websites():
    assert umap_groups.MISSING_COLOUR == (0.5, 0.5, 0.5)
    assert umap_groups.NO_CELL_TYPE_COLOUR == (0.6, 0.6, 0.6)
    assert umap_groups.NO_VALUE == (0.45, 0.45, 0.48, 0.3)


def _legend_box(names):
    n = len(names)
    fig = _figure(np.arange(n) % n, umap_groups.palette(n), names=names)
    FigureCanvasAgg(fig).draw()
    [ax] = [a for a in fig.axes if a.get_label() == umap.MAP_AXES]
    legend = ax.get_legend()
    return (
        fig.bbox,
        legend.get_window_extent(),
        [t.get_text() for t in legend.get_texts()],
    )


@pytest.mark.parametrize(
    "names",
    [
        [f"Phellem suberized cell type {i}" for i in range(12)],
        [f"MWMWMWMWMWMWMWMWMWMWMWMWMWMWMW {i:02d}" for i in range(umap.MAX_GROUPS)],
        ["MW" * 30 for _ in range(umap.LEGEND_ROWS)],
        ["MW" * 30 for _ in range(umap.MAX_GROUPS)],
    ],
)
def test_the_legend_fits_inside_the_image(names):
    image, legend, _ = _legend_box(names)
    assert (legend.x0, legend.y0) >= (0, 0)
    assert legend.x1 <= image.x1 and legend.y1 <= image.y1


def test_names_fit_one_column_whole_and_long_ones_keep_both_ends():
    whole = "Phellem suberized cell type 1"
    _, _, shown = _legend_box([whole, "a" * 30 + "b" * 30])
    assert shown[0] == whole
    assert shown[1] == "a" * 19 + "…" + "b" * 20


def test_two_columns_cut_names_to_half():
    names = [f"pFACT::MYB41 x Col-0 line {i:02d}" for i in range(umap.LEGEND_ROWS + 1)]
    _, _, shown = _legend_box(names)
    assert shown[0] == "pFACT::MY…-0 line 00"
    assert {len(s) for s in shown} == {umap.LEGEND_CHARS // 2}


def test_the_map_leaves_room_for_the_legend():
    fig = _figure([0, 1], [(1, 0, 0), (0, 0, 1)])
    [ax] = [a for a in fig.axes if a.get_label() == umap.MAP_AXES]
    assert ax.get_position().x1 <= umap.MAP_RIGHT


def test_past_the_cap_there_is_no_legend():
    n = umap.MAX_GROUPS + 1
    fig = _figure(
        np.arange(n), umap_groups.palette(n), names=[f"t{i}" for i in range(n)]
    )
    [ax] = [a for a in fig.axes if a.get_label() == umap.MAP_AXES]
    assert ax.get_legend() is None
