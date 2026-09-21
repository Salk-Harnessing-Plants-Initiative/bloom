"""Per-cell verification of `plot_correlation_matrix`'s heatmap (#768).

`test_viz_snapshot.py` compares each plot's whole rendered image against a committed baseline
via an RMS pixel-diff at `SNAPSHOT_TOL = 15`. That check is broad and cheap, and it structurally
CANNOT catch a single wrong cell in this heatmap: RMS is a whole-image average, so one cell out
of 55 is diluted into the same numeric range as ordinary cross-platform FreeType noise (~5-12
RMS). There is no tolerance that separates them -- see that file's "Known limitation" section.

This file closes that gap from the other side, taking #768's first named path ("extract and
compare each heatmap cell individually"): every cell the delegate draws is asserted against a
`df[trait_cols].corr()` recomputed here, at four layers -- the QuadMesh's data array and mask,
the color scale's endpoints, each cell's annotation string, each cell's facecolor -- plus the
cell's actual pixels in the saved PNG.

Measured, not guessed (design.md Decision 2 in
`openspec/changes/add-bloommcp-correlation-cell-oracle/`), by making the delegate draw a matrix
with exactly one cell shifted -- the fixture's strongest real pair, Surface Area x Network Area,
true r=0.9942:

    cell drawn as | whole-image RMS | caught at TOL=15 | per-cell dC | caught per-cell
    --------------+-----------------+------------------+-------------+----------------
    0.944 (-0.05) |            1.66 |               no |       0.090 |            yes
    0.694 (-0.30) |            4.92 |               no |       0.408 |            yes
   -0.006 (-1.00) |           11.08 |               no |       0.851 |            yes
   -0.306 (-1.30) |           11.24 |               no |       0.838 |            yes

The last row is the headline: r=0.9942 -> r=-0.306 is the widest error this colormap can express
on this fixture (norm spans -0.30799..0.99421) -- a clean sign flip from "almost perfectly
correlated" to "the most negatively correlated pair on the plot" -- and whole-image RMS still
does not flag it. #768's own figure (an opaque-orange recolor, RMS~5.2) understated the gap;
the honest conclusion is the stronger one: **no single-cell correlation error in this heatmap is
caught by the RMS layer, at any magnitude.**

Per-cell, signal and noise are cleanly separated where RMS's overlap:

  * noise floor: worst disagreement between a cell's sampled pixels and the color its own value
    implies, across all 55 cells, is 0.00196 (per-channel, 0-1) -- anti-aliasing + PNG quantization
  * smallest signal tested: 0.090, for a -0.05 shift (already below anything worth caring about)
  * `_CELL_ATOL = 0.01` therefore sits ~5x above the noise and ~9x below the smallest signal

Division of labor (enforced by `test_single_cell_defect_rms_misses_but_cell_oracle_catches`, not
just described here): the RMS layer answers "does this figure still rasterize like the committed
baseline?" -- global regressions, layout shifts, dependency bumps. This file answers "does each
drawn cell carry the value it should?". Both are kept.

Residual, stated plainly: a defect confined to matplotlib's rasterizer -- one that painted a
cell's pixels wrongly while leaving the figure's artist state correct -- is caught by neither
layer, since the assertions here read artist state and the pixel check samples a render produced
by that same rasterizer. That is not the failure mode #768 describes (a wrong *value* reaching a
researcher) and no realistic bug here produces it; recorded so the boundary is known rather than
inferred.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from matplotlib import pyplot as plt
from matplotlib.testing.compare import compare_images
from PIL import Image
from sleap_roots_analyze.visualization import create_correlation_heatmap

from bloom_mcp import experiment_utils as eu

# Imported for its `matplotlib.use("Agg")` side effect as well as for the tool entrypoint
# `test_the_oracles_subject_is_the_tools_committed_png` drives.
from bloom_mcp.sections.sleap_roots.analysis import (
    plot_correlation_matrix as plot_correlation_matrix_mod,
)

from .conftest import SNAPSHOT_TOL, render_tool_to_dir

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_RAW_FIXTURE = _FIXTURES / "turface_19_final_data.csv"

# The exact savefig arguments `plot_correlation_matrix` uses. Kept as named constants because
# the cell-geometry mapping below depends on all three: `bbox_inches="tight"` is what makes the
# saved canvas differ from `figsize * dpi`, and `pad_inches` is savefig's own default, which the
# tool does not override.
_SAVE_DPI = 150
_SAVE_BBOX_INCHES = "tight"
_SAVE_PAD_INCHES = 0.1

# Measured (see module docstring): legitimate per-channel disagreement peaks at 0.00196 across
# all 55 cells; the smallest defect signal tested is 0.090. This sits between them with an order
# of magnitude of headroom on each side -- the separation whole-image RMS does not have.
_CELL_ATOL = 0.01

# Sampling windows as (inner_fraction, outer_fraction) of a cell's width. The wide one spans the
# centered annotation glyphs; the narrow one is a left strip that contains none. Both are
# median-sampled and both must agree -- see test_cell_sampling_is_unbiased_by_annotation_glyphs.
_WIDE_INSET = (0.10, 0.90)
_NARROW_INSET = (0.12, 0.30)

# Read from the delegate's own signature rather than duplicated as a literal, so a change to its
# default fails `test_annotation_threshold_is_read_from_the_delegate` instead of silently
# invalidating every annotation assertion here (the same self-verifying-constant lesson
# `test_viz_snapshot.py`'s `_REAL_CORRELATION_CELL_AREA_FRACTION` learned the hard way).
_ANNOT_THRESHOLD = (
    inspect.signature(create_correlation_heatmap).parameters["annot_threshold"].default
)


def _require_annotations(n_traits: int) -> None:
    """Guard the premise every annotation assertion in this file rests on.

    The delegate is `show_annot = n_traits <= annot_threshold`, so annotations stop being drawn
    STRICTLY ABOVE the threshold. Above it `ax.texts` is empty and the annotation assertions
    would fail with an incidental "0 != 55" rather than an explanation.
    """
    if n_traits > _ANNOT_THRESHOLD:
        raise AssertionError(
            f"this fixture resolves {n_traits} traits, above create_correlation_heatmap's "
            f"annot_threshold={_ANNOT_THRESHOLD}, at which it stops drawing per-cell "
            "annotations -- the annotation layer of this oracle does not apply. The "
            "array/norm/facecolor layers still do; see this change's Non-goals."
        )


def _fixture_frame_and_corr():
    """The independent oracle: the raw fixture, its resolved traits, and the UNGUARDED `.corr()`.

    Unguarded on purpose. The tool computes its JSON summary from a guarded
    `.corr(min_periods=...)`, while `create_correlation_heatmap` runs its own plain `.corr()` --
    they disagree exactly on low-overlap pairs, the still-open #747 gap. Comparing against the
    plain one keeps this file's verdict "the heatmap drew the wrong value" and stops it
    conflating that with "the heatmap should not have drawn this value at all".
    """
    df = pd.read_csv(_RAW_FIXTURE, encoding="utf-8")
    trait_cols = eu.detect_columns(df)["trait_cols"]
    return df, trait_cols, df[trait_cols].corr()


def _drawn_mask(corr: pd.DataFrame) -> np.ndarray:
    """True where the delegate leaves a cell blank.

    NOT simply the `np.triu(...)` mask the delegate passes to seaborn: seaborn's `_HeatMapper`
    applies its own `np.ma.masked_invalid` ON TOP of it, so a NaN correlation is blank too.
    Measured: forcing one trait in this fixture to a constant drops the drawn cells from 55 to
    45. That matters here specifically because this tool reads raw, uncleaned data, where a
    zero-variance or low-overlap trait is a first-class case (`zero_variance_traits` and
    `low_overlap_trait_pairs` are shipped result fields). Deriving the set this way -- rather
    than hardcoding "the 55 lower-triangle cells" -- is also what lets this file distinguish
    "legitimately blank" from "should have carried a value and was left blank".
    """
    values = corr.to_numpy()
    return np.triu(np.ones(values.shape, dtype=bool)) | ~np.isfinite(values)


def _render(df, trait_cols):
    fig = create_correlation_heatmap(df, trait_cols)
    fig.canvas.draw()
    return fig


def _quadmesh(fig):
    return fig.axes[0].collections[0]


@pytest.fixture(scope="module")
def heatmap(tmp_path_factory):
    """One clean render of the real fixture, plus its saved PNG, shared by the read-only checks.

    Module-scoped because every assertion below is read-only and rendering this figure is the
    expensive part; the negative control renders its own doctored figures separately.
    """
    df, trait_cols, corr = _fixture_frame_and_corr()
    fig = _render(df, trait_cols)
    png = tmp_path_factory.mktemp("cell_oracle") / "correlation_matrix.png"
    fig.savefig(png, dpi=_SAVE_DPI, bbox_inches=_SAVE_BBOX_INCHES)
    try:
        yield {
            "df": df,
            "trait_cols": trait_cols,
            "corr": corr,
            "values": corr.to_numpy(),
            "mask": _drawn_mask(corr),
            "fig": fig,
            "ax": fig.axes[0],
            "qm": _quadmesh(fig),
            "png": png,
        }
    finally:
        plt.close(fig)


# ── the drawn cell set ───────────────────────────────────────────────────────


def test_drawn_cell_set_is_the_finite_lower_triangle(heatmap):
    qm, mask, values = heatmap["qm"], heatmap["mask"], heatmap["values"]
    array = qm.get_array()

    assert np.array_equal(np.ma.getmaskarray(array), mask), (
        "the cells the heatmap actually draws are not `triu | ~isfinite` -- either a cell that "
        "should carry a finite correlation was left blank, or a cell that should be blank was "
        "filled in"
    )
    # Non-vacuity: without these, an all-masked or all-unmasked render would satisfy every
    # per-cell assertion in this file by having nothing to check.
    assert (
        ~mask
    ).sum() > 0, "no cells are drawn -- every per-cell check below is vacuous"
    assert (
        mask.sum() > 0
    ), "every cell is drawn -- the upper triangle is not being masked"

    np.testing.assert_allclose(
        array.compressed(),
        np.ma.masked_array(values, mask=mask).compressed(),
        err_msg="a drawn cell's value differs from the independently recomputed correlation",
    )


def test_drawn_cell_set_shrinks_when_a_trait_carries_no_variance(heatmap):
    """Exercise the `~isfinite` half of `_drawn_mask`, which the clean fixture cannot reach.

    `turface_19` has no zero-variance trait, so on it `triu | ~isfinite` and a plain `triu` are
    the same set and the distinction is untested -- confirmed by mutation: deleting the
    `~isfinite` term leaves every other test in this file green. That matters because a NaN
    correlation is a first-class case for THIS tool specifically: it reads raw, uncleaned data,
    and `zero_variance_traits`/`low_overlap_trait_pairs` are shipped result fields.

    Forcing one trait constant makes its whole row and column NaN. seaborn's `_HeatMapper`
    applies `np.ma.masked_invalid` on top of the caller's mask, so those cells are not drawn --
    measured here as 55 drawn cells dropping to 45, with annotations following.
    """
    df, trait_cols = heatmap["df"].copy(), heatmap["trait_cols"]
    df[trait_cols[3]] = 1.0

    corr = df[trait_cols].corr()
    mask = _drawn_mask(corr)
    plain_triu = np.triu(np.ones(corr.shape, dtype=bool))
    assert not np.array_equal(mask, plain_triu), (
        "this fixture no longer produces any NaN correlation, so it cannot exercise the "
        "`~isfinite` half of the drawn-cell set -- pick a different degeneracy"
    )

    fig = _render(df, trait_cols)
    try:
        array = _quadmesh(fig).get_array()
        assert np.array_equal(np.ma.getmaskarray(array), mask)
        assert (~mask).sum() == 45 and (~plain_triu).sum() == 55, (
            f"expected a constant trait to drop the drawn cells 55 -> 45, got "
            f"{(~plain_triu).sum()} -> {(~mask).sum()}"
        )
        assert set(_annotations_by_cell(fig.axes[0])) == {
            (r, c)
            for r in range(mask.shape[0])
            for c in range(mask.shape[1])
            if not mask[r, c]
        }, "annotations were drawn for cells the delegate masked as non-finite"
    finally:
        plt.close(fig)


def test_color_scale_spans_exactly_the_drawn_cells(heatmap):
    """A global rescale must fail here rather than silently shifting every cell's expected color.

    `test_every_drawn_cell_is_colored_by_its_own_value` maps value -> expected color through the
    figure's own `norm`, so a changed normalization would move both sides of that comparison
    together. Pinning the endpoints against a recomputed min/max is what closes that.
    """
    drawn = np.ma.masked_array(heatmap["values"], mask=heatmap["mask"])
    qm = heatmap["qm"]
    assert qm.norm.vmin == pytest.approx(float(drawn.min()))
    assert qm.norm.vmax == pytest.approx(float(drawn.max()))


def test_cells_outside_the_drawn_set_are_not_painted(heatmap):
    qm, mask = heatmap["qm"], heatmap["mask"]
    facecolors = qm.get_facecolors()
    n = mask.shape[0]
    hidden = [r * n + c for r in range(n) for c in range(n) if mask[r, c]]

    assert hidden, "nothing is hidden -- this assertion would pass vacuously"
    np.testing.assert_array_equal(
        facecolors[hidden],
        np.zeros((len(hidden), 4)),
        err_msg="a cell outside the drawn set was painted rather than left transparent",
    )


def test_axis_labels_are_the_resolved_trait_columns_in_order(heatmap):
    """`plot_correlation_matrix`'s `heatmap_caveat` tells a PNG-only viewer to match flagged
    trait names "against the image's own axis labels". That instruction is only sound if the
    labels really are the resolved trait columns, in order -- an unstated premise until now.
    """
    ax, trait_cols = heatmap["ax"], heatmap["trait_cols"]
    assert (
        trait_cols
    ), "no trait columns resolved -- this assertion would pass vacuously"
    assert [t.get_text() for t in ax.get_xticklabels()] == list(trait_cols)
    assert [t.get_text() for t in ax.get_yticklabels()] == list(trait_cols)


# ── per-cell values ──────────────────────────────────────────────────────────


def _annotations_by_cell(ax) -> dict[tuple[int, int], str]:
    """seaborn places each annotation at the centre of its cell: `(col + 0.5, row + 0.5)`."""
    out = {}
    for text in ax.texts:
        x, y = text.get_position()
        out[(int(y - 0.5), int(x - 0.5))] = text.get_text()
    return out


def test_every_drawn_cell_annotation_matches_its_own_value(heatmap):
    """The annotation is the literal number a researcher reads off the plot.

    Keyed by each annotation's OWN grid position, so a correct value drawn in the wrong cell
    fails just as a wrong value does -- a transposition would otherwise slip through a check
    that only compared the two multisets.
    """
    _require_annotations(len(heatmap["trait_cols"]))
    ax, mask, values = heatmap["ax"], heatmap["mask"], heatmap["values"]

    drawn = [
        (r, c)
        for r in range(mask.shape[0])
        for c in range(mask.shape[1])
        if not mask[r, c]
    ]
    annotations = _annotations_by_cell(ax)

    assert set(annotations) == set(drawn), (
        "the annotated cells are not exactly the drawn cells -- "
        f"{len(annotations)} annotations for {len(drawn)} drawn cells"
    )
    wrong = {
        (r, c): (annotations[(r, c)], f"{values[r, c]:.2f}")
        for r, c in drawn
        if annotations[(r, c)] != f"{values[r, c]:.2f}"
    }
    assert not wrong, f"cells annotated with a value that is not their own: {wrong}"


def test_every_drawn_cell_is_colored_by_its_own_value(heatmap):
    qm, mask, values = heatmap["qm"], heatmap["mask"], heatmap["values"]
    n = mask.shape[0]
    facecolors = qm.get_facecolors()

    wrong = []
    for r in range(n):
        for c in range(n):
            if mask[r, c]:
                continue
            expected = qm.cmap(qm.norm(values[r, c]))
            if not np.allclose(facecolors[r * n + c], expected, atol=1e-9):
                wrong.append(((r, c), values[r, c]))
    assert (
        not wrong
    ), f"cells painted a color their own correlation value does not imply: {wrong}"


def test_annotation_threshold_is_read_from_the_delegate():
    """Pin `_ANNOT_THRESHOLD` to the delegate rather than to a literal that can go stale."""
    assert _ANNOT_THRESHOLD == (
        inspect.signature(create_correlation_heatmap)
        .parameters["annot_threshold"]
        .default
    )
    assert isinstance(_ANNOT_THRESHOLD, int) and _ANNOT_THRESHOLD > 0


def test_annotation_precondition_fails_loudly_above_the_threshold():
    """Exercise the guard's failure path directly.

    The committed 11-trait fixture can never reach `annot_threshold`, so without this the guard
    would be asserted only by prose -- exactly the gap #713's review closed for
    `test_missing_baseline_fails_with_an_actionable_message`.
    """
    _require_annotations(
        _ANNOT_THRESHOLD
    )  # at the threshold: still annotated, must not raise
    with pytest.raises(AssertionError, match="annot_threshold"):
        _require_annotations(_ANNOT_THRESHOLD + 1)


# ── the saved PNG ────────────────────────────────────────────────────────────


def _cell_pixel_box(fig, ax, image_height, r, c):
    """Map a cell's data coords to its box in the PNG `savefig(bbox_inches="tight")` writes.

    `bbox_inches="tight"` crops the canvas, so a cell's display coordinates are not its saved
    coordinates. `fig.get_tightbbox(renderer)` gives that crop in inches; savefig then pads it by
    `pad_inches` on every side. Display coords are rescaled from the figure's own dpi to the save
    dpi, and flipped vertically because display y is bottom-up while image y is top-down.
    """
    bbox = fig.get_tightbbox(fig.canvas.get_renderer())
    x0_inches = bbox.x0 - _SAVE_PAD_INCHES
    y0_inches = bbox.y0 - _SAVE_PAD_INCHES

    lower_left = ax.transData.transform((c, r))
    upper_right = ax.transData.transform((c + 1, r + 1))

    def to_x(display_x):
        return (display_x / fig.dpi - x0_inches) * _SAVE_DPI

    def to_y(display_y):
        return image_height - (display_y / fig.dpi - y0_inches) * _SAVE_DPI

    xs = sorted((to_x(lower_left[0]), to_x(upper_right[0])))
    ys = sorted((to_y(lower_left[1]), to_y(upper_right[1])))
    return xs[0], ys[0], xs[1], ys[1]


def _sample_cell(pixels, box, inset):
    """Median RGB (0-1) over an inset of one cell.

    Median rather than mean so the centered annotation glyphs cannot drag the sample toward the
    text color -- see test_cell_sampling_is_unbiased_by_annotation_glyphs, which demonstrates
    that rather than taking it on trust.
    """
    x0, y0, x1, y1 = box
    width, height = x1 - x0, y1 - y0
    inner, outer = inset
    patch = pixels[
        int(y0 + inner * height) : int(y1 - inner * height),
        int(x0 + inner * width) : int(x0 + outer * width),
        :,
    ]
    return np.median(patch.reshape(-1, 3), axis=0) / 255.0


def _saved_cell_samples(heatmap, inset):
    pixels = np.asarray(Image.open(heatmap["png"]).convert("RGB"), dtype=float)
    fig, ax, mask = heatmap["fig"], heatmap["ax"], heatmap["mask"]
    height = pixels.shape[0]
    return {
        (r, c): _sample_cell(pixels, _cell_pixel_box(fig, ax, height, r, c), inset)
        for r in range(mask.shape[0])
        for c in range(mask.shape[1])
        if not mask[r, c]
    }


def test_saved_png_cell_pixels_match_their_correlation_value(heatmap):
    """The end of the chain: value -> color -> the bytes a researcher actually opens.

    Retained even though the artist-state checks above already catch every defect in the module
    docstring's table, because this is the only per-cell coverage that does not read the
    seaborn/matplotlib artist tree -- an upgrade restructuring that tree breaks all of them at
    once, and this is what keeps per-cell coverage from lapsing entirely in that window.
    """
    qm, values = heatmap["qm"], heatmap["values"]
    samples = _saved_cell_samples(heatmap, _WIDE_INSET)
    assert samples, "no cells sampled -- this assertion would pass vacuously"

    wrong = {}
    for (r, c), sampled in samples.items():
        expected = np.asarray(qm.cmap(qm.norm(values[r, c]))[:3])
        if not np.allclose(sampled, expected, atol=_CELL_ATOL):
            wrong[(r, c)] = (sampled.round(4).tolist(), expected.round(4).tolist())
    assert not wrong, (
        f"saved-PNG cells whose pixels do not match their own correlation value "
        f"(atol={_CELL_ATOL}): {wrong}"
    )


def test_cell_sampling_is_unbiased_by_annotation_glyphs(heatmap):
    """Demonstrate the median-sampling property instead of asserting the technique.

    Each cell is sampled twice: over a wide inset that spans the centered annotation text, and
    over a narrow left strip containing none. If the glyphs biased the sample the two would
    diverge -- and the check would inherit exactly the cross-platform font-rendering sensitivity
    that makes the whole-image RMS tolerance unable to catch single-cell defects.
    """
    _require_annotations(len(heatmap["trait_cols"]))
    wide = _saved_cell_samples(heatmap, _WIDE_INSET)
    narrow = _saved_cell_samples(heatmap, _NARROW_INSET)
    assert wide and narrow

    worst = max(float(np.abs(wide[k] - narrow[k]).max()) for k in wide)
    assert worst < _CELL_ATOL, (
        f"text-including and text-free samples of the same cells disagree by {worst:.5f} "
        f"(atol={_CELL_ATOL}) -- the sampling is being biased by glyph placement"
    )


def test_the_oracles_subject_is_the_tools_committed_png(heatmap, viz_env):
    """Tie every assertion in this file to the artifact the tool really produces.

    Everything above inspects a figure this test module rendered itself. That is only evidence
    about `plot_correlation_matrix` if the two are the same image -- so compare this module's
    render against the PNG the real tool entrypoint commits, at `tol=0`. Both are rendered in
    this same process on this same machine, so `tol=0` is the correct strictness rather than a
    brittle one: any difference means the tool does something to the figure the oracle is not
    seeing (a `heatmap_caveat` footnote firing, say), not platform noise.
    """
    committed = (
        render_tool_to_dir(
            "correlation_matrix",
            plot_correlation_matrix_mod,
            "plot_correlation_matrix",
            viz_env,
        )
        / "correlation_matrix.png"
    )
    assert committed.is_file()

    diff = compare_images(str(committed), str(heatmap["png"]), tol=0)
    assert diff is None, (
        "this module's own render is no longer identical to the PNG plot_correlation_matrix "
        f"commits, so the per-cell assertions here may not describe the tool's real output: {diff}"
    )


# ── the negative control: why both layers exist (#768) ───────────────────────

# Measured shifts against the fixture's strongest real pair -- see the module docstring's table.
# The last entry is the widest error this colormap can express on this fixture.
_DEFECT_SHIFTS = [0.05, 0.30, 1.00, 1.30]


def _strongest_drawn_pair(values, mask):
    candidates = np.ma.masked_array(np.abs(values), mask=mask)
    return np.unravel_index(np.ma.argmax(candidates), candidates.shape)


@pytest.mark.parametrize(
    "shift", _DEFECT_SHIFTS, ids=[f"shift{s}" for s in _DEFECT_SHIFTS]
)
def test_single_cell_defect_rms_misses_but_cell_oracle_catches(
    monkeypatch, tmp_path, heatmap, shift
):
    """#768's whole point, asserted rather than described.

    The defect is introduced by making the delegate RENDER a doctored matrix -- not by recoloring
    the saved PNG afterwards. A post-hoc pixel edit is invisible to every artist-state assertion
    and would negatively control only the saved-PNG layer, leaving most of this file unexercised.

    Both halves are asserted here so the division of labor in the module docstring cannot rot:
    if RMS ever starts catching this, or if any one of the three per-cell layers ever stops, this
    test fails and says which.
    """
    df, trait_cols, values, mask = (
        heatmap["df"],
        heatmap["trait_cols"],
        heatmap["values"],
        heatmap["mask"],
    )
    r, c = _strongest_drawn_pair(values, mask)
    true_value = values[r, c]
    drawn_as = float(np.clip(true_value - shift, -1.0, 1.0))

    real_corr = pd.DataFrame.corr

    def _corr_with_one_cell_shifted(self, *args, **kwargs):
        result = real_corr(self, *args, **kwargs)
        if list(result.columns) == list(trait_cols):
            result = result.copy()
            result.iloc[r, c] = drawn_as
            result.iloc[c, r] = drawn_as
        return result

    monkeypatch.setattr(pd.DataFrame, "corr", _corr_with_one_cell_shifted)
    defective = _render(df, trait_cols)
    monkeypatch.undo()

    try:
        defective_png = tmp_path / "defective.png"
        defective.savefig(defective_png, dpi=_SAVE_DPI, bbox_inches=_SAVE_BBOX_INCHES)

        # Half one: whole-image RMS does not notice.
        assert (
            compare_images(str(heatmap["png"]), str(defective_png), tol=SNAPSHOT_TOL)
            is None
        ), (
            f"whole-image RMS at tol={SNAPSHOT_TOL} now CATCHES a single cell drawn as "
            f"{drawn_as:.3f} instead of {true_value:.3f}. That is an improvement, but it "
            "invalidates the premise this file was written on -- update this change's design.md, "
            "test_viz_snapshot.py's 'Known limitation' section, and "
            "test_realistic_single_cell_defect_in_correlation_matrix_is_not_caught rather than "
            "loosening this assertion."
        )

        # Half two: each per-cell layer catches it, asserted separately so no single layer can
        # carry the test while another quietly stops detecting.
        qm = _quadmesh(defective)
        n = mask.shape[0]

        assert qm.get_array()[r, c] != pytest.approx(
            true_value
        ), "the data-array layer no longer detects a doctored cell"
        assert (
            _annotations_by_cell(defective.axes[0])[(r, c)] != f"{true_value:.2f}"
        ), "the annotation layer no longer detects a doctored cell"
        assert not np.allclose(
            qm.get_facecolors()[r * n + c],
            qm.cmap(qm.norm(true_value)),
            atol=_CELL_ATOL,
        ), "the facecolor layer no longer detects a doctored cell"
    finally:
        plt.close(defective)
