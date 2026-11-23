"""scrna_plots.umap: the gene UMAP drawn as the website's UMAP tab draws it."""

from __future__ import annotations

import time

import numpy as np
import pytest
from matplotlib import colormaps

from bloom_mcp.data_access.scrna_plots import _png, umap, umap_labels

VIRIDIS = colormaps["viridis"]


def _layer(fig, gid):
    [ax] = [a for a in fig.axes if a.get_label() == umap.MAP_AXES]
    [layer] = [c for c in ax.collections if c.get_gid() == gid]
    return layer


def _order(fig):
    [ax] = [a for a in fig.axes if a.get_label() == umap.MAP_AXES]
    return [c.get_gid() for c in ax.collections]


def _map(values, codes=None, focus=None, names=("A", "B")):
    n = len(values)
    x = np.arange(n, dtype=float)
    y = np.zeros(n)
    codes = np.zeros(n, dtype=int) if codes is None else np.asarray(codes)
    return umap.gene_figure(
        x,
        y,
        np.asarray(values, dtype=float),
        codes,
        list(names),
        None if focus is None else np.asarray(focus, dtype=bool),
        "AT4G28110 in MYB41 root",
        units="log-normalized",
    )


def test_zero_cells_are_drawn_before_expressing_cells():
    fig = _map([0.0, 3.0, 0.0, 1.0])
    assert _order(fig) == [umap.ZERO, umap.EXPRESSING]
    assert len(_layer(fig, umap.ZERO).get_offsets()) == 2


def test_a_focus_draws_the_other_cells_grey_first():
    fig = _map([0.0, 3.0, 2.0, 1.0], codes=[0, 0, 1, 1], focus=[1, 1, 0, 0])
    assert _order(fig) == [umap.OUTSIDE, umap.ZERO, umap.EXPRESSING]
    outside = _layer(fig, umap.OUTSIDE)
    assert [tuple(p) for p in outside.get_offsets()] == [(2.0, 0.0), (3.0, 0.0)]
    assert np.allclose(outside.get_facecolors()[0], (0.42, 0.42, 0.45, 0.35))


def test_colours_run_from_the_lowest_to_the_highest_value():
    fig = _map([0.0, 8.0, 4.0])
    zero = _layer(fig, umap.ZERO).get_facecolors()
    top = _layer(fig, umap.EXPRESSING)
    by_x = dict(zip(top.get_offsets()[:, 0], top.get_facecolors()))
    assert np.allclose(zero[0], VIRIDIS(0.0), atol=1e-6)
    assert np.allclose(by_x[1.0], VIRIDIS(1.0), atol=1e-6)
    assert np.allclose(by_x[2.0], VIRIDIS(0.5), atol=1e-6)


def test_the_scale_is_over_every_cell_even_outside_the_focus():
    fig = _map([0.0, 2.0, 8.0], codes=[0, 0, 1], focus=[1, 1, 0])
    [colour] = _layer(fig, umap.EXPRESSING).get_facecolors()
    assert np.allclose(colour, VIRIDIS(0.25), atol=1e-6)


def test_every_value_equal_still_draws():
    fig = _map([2.0, 2.0, 2.0])
    assert len(_layer(fig, umap.EXPRESSING).get_offsets()) == 3
    assert _png.draw(lambda: fig, time.monotonic() + 30).reason is None


def test_dark_ground_and_equal_axes():
    fig = _map([0.0, 1.0])
    [ax] = [a for a in fig.axes if a.get_label() == umap.MAP_AXES]
    assert np.allclose(ax.get_facecolor()[:3], umap.GROUND)
    assert ax.get_aspect() == 1.0


def test_a_colour_bar_is_drawn_and_names_the_units():
    [bar] = [a for a in _map([0.0, 1.0]).axes if a.get_label() == "<colorbar>"]
    assert bar.get_ylabel() == "log-normalized"


def _grid_points(group_sizes):
    """Points of each group packed into one corner square each."""
    xs, ys, codes = [], [], []
    for code, (size, corner) in enumerate(group_sizes):
        for k in range(size):
            xs.append(corner[0] + k * 1e-3)
            ys.append(corner[1] + k * 1e-3)
            codes.append(code)
    # Stretch the map so each corner is its own grid square.
    xs += [0.0, 100.0]
    ys += [0.0, 100.0]
    codes += [-1, -1]
    return np.array(xs), np.array(ys), np.array(codes)


def test_a_label_sits_at_the_mean_of_its_densest_square():
    x, y, codes = _grid_points([(6, (10.0, 10.0)), (5, (80.0, 80.0))])
    anchors = umap_labels.anchors(x, y, codes, 2, None)
    assert anchors[0] == pytest.approx((10.0025, 10.0025))
    assert anchors[1] == pytest.approx((80.002, 80.002))


def test_no_label_under_five_cells_in_the_densest_square():
    x, y, codes = _grid_points([(4, (10.0, 10.0))])
    assert umap_labels.anchors(x, y, codes, 1, None) == {}


def test_cells_outside_the_focus_do_not_count_for_labels():
    x, y, codes = _grid_points([(6, (10.0, 10.0))])
    shown = np.zeros(len(x), dtype=bool)
    assert umap_labels.anchors(x, y, codes, 1, shown) == {}


def test_names_are_written_in_full_and_no_cell_type_is_not_named():
    long = "Phellem " * 40
    x, y, codes = _grid_points([(6, (10.0, 10.0)), (6, (80.0, 80.0))])
    codes[codes == 1] = -1
    values = np.ones(len(x))
    fig = umap.gene_figure(x, y, values, codes, [long, "B"], None, "t", units="u")
    [ax] = [a for a in fig.axes if a.get_label() == umap.MAP_AXES]
    assert [t.get_text() for t in ax.texts] == [long]


def test_constants_are_the_websites():
    assert (umap_labels.LABEL_GRID, umap_labels.MIN_LABEL_POINTS) == (40, 5)
    assert umap.GROUND == (0.05, 0.05, 0.08)
    assert umap.FOCUS_GREY == (0.42, 0.42, 0.45, 0.35)


def test_two_hundred_thousand_cells_draw_under_the_cap():
    rng = np.random.default_rng(0)
    n = 200_000
    x, y = rng.normal(size=n), rng.normal(size=n)
    values = np.where(rng.random(n) < 0.3, rng.random(n) * 5, 0.0)
    codes = rng.integers(0, 20, size=n)
    names = [f"type {i}" for i in range(20)]
    drawn = _png.draw(
        lambda: umap.gene_figure(x, y, values, codes, names, None, "t", units="u"),
        time.monotonic() + 120,
    )
    assert drawn.reason is None and len(drawn.png) <= _png.MAX_PNG_BYTES
