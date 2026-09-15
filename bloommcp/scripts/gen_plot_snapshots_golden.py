"""Regenerate the 3 plotting-tool baseline PNGs under
``tests/fixtures/plot_baselines/`` (#713).

Unlike every other ``gen_*_golden.py`` script in this directory, this one's output is a
**rendering** golden, not a numeric one -- the artifact under test is pixel content, not
JSON. That makes the *environment* part of the golden's provenance in a way JSON goldens
never need to worry about: matplotlib rasterizes text via FreeType, and FreeType's hinting
differs across OS/font-stack combinations. ``tests/tools/test_viz_snapshot.py`` compares
against these baselines with ``matplotlib.testing.compare.compare_images`` at a tolerance
wide enough to absorb that cross-platform noise (see that file's module docstring for the
tolerance rationale) -- but the baselines themselves should still be (re)generated on
Linux via ``uv run --frozen --extra test python scripts/gen_plot_snapshots_golden.py``,
matching the ``ubuntu-latest`` runner ``python-audit`` actually asserts against, so the
*starting* comparison point is the canonical one rather than an already-off-tolerance
macOS render.

Calls the 3 MCP tool functions directly (not just their delegates) against
``turface_19_final_data.csv`` -- the same fixture and tool entrypoints
``tests/tools/test_viz_snapshot.py`` exercises through the shared ``viz_env`` fixture -- and
captures the bytes each tool *commits* to its ``ResultStore`` run (via a ``commit`` spy, the
last point at which the file exists on disk), so the baseline reflects the full save path
(the tool's own ``savefig`` call, ``dpi``/``bbox_inches`` included), not a hand-rolled
re-render that could quietly drift from what the tool actually produces.

This was 5 tools until #462 retired ``plot_heritability_bar``/``plot_variance_decomposition``
into ``heritability_analysis``; those two wrote straight to ``PLOTS_DIR``, and the branch that
captured them from there went with them.

Run:  cd bloommcp && uv run --frozen --extra test python scripts/gen_plot_snapshots_golden.py --yes

Regenerating over an *existing* baseline is exactly the moment a real rendering
regression could get silently "laundered" into a new golden -- a PR that touches these
PNGs is, by construction, changing the thing the tests exist to catch changes to. This
script prints the old-vs-new RMS (`matplotlib.testing.compare.compare_images`) for every
baseline it would overwrite, via `_report_regeneration` below, and requires an explicit
`--yes` flag before actually overwriting anything that already exists -- run it once
without `--yes` to preview every file's RMS, then again with `--yes` once you've confirmed
each one is expected. This is a **local, opt-in speed bump, not a CI-enforced gate**:
nothing in CI reads this script's output or runs it itself, and `--yes` is trivial to pass
without actually reading the RMS above it -- but it does mean "just run the regen script"
can no longer silently overwrite an existing baseline in one uninterrupted step. A PR that
regenerates baselines should still quote the printed RMS per file and say why the change is
expected (matplotlib bump, an intentional style/color default change, etc.) as a matter of
review convention. An RMS of 0 (or near it) confirms nothing visually changed -- e.g. a
`sleap-roots-analyze` patch bump with no rendering effect.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import shutil
import tempfile
from pathlib import Path

import matplotlib
import matplotlib.ft2font
import PIL
import sleap_roots_analyze as sra
from matplotlib.testing.compare import compare_images
from matplotlib.testing.exceptions import ImageComparisonFailure

import bloom_mcp.manifest.manifest as _manifest
import bloom_mcp.supabase_client as _sc
from bloom_mcp import experiment_utils as eu
from bloom_mcp.sections.sleap_roots.analysis import (
    plot_correlation_matrix as plot_correlation_matrix_mod,
    plot_trait_boxplots as plot_trait_boxplots_mod,
    plot_trait_histograms as plot_trait_histograms_mod,
)

_FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
_RAW = _FIXTURES / "turface_19_final_data.csv"
_EXPERIMENT = "turface_19.csv"
_BASELINES = _FIXTURES / "plot_baselines"

# (output basename, tool callable, PNG name the tool itself writes, is-converged)
#
# The 3 tools #466 converged onto `@as_mcp_tool` take a Pydantic params model rather than a
# bare experiment string, return a result model rather than a "Plot saved: ..." string, and
# persist into a ResultStore version dir rather than PLOTS_DIR — so they are rendered via
# `_render_converged` below instead of the legacy call. The rendered pixels are unchanged;
# only the calling convention and the output path are (#466 review round 7).
_TOOLS = [
    (
        "histograms_turface_19_baseline.png",
        plot_trait_histograms_mod.plot_trait_histograms,
        "trait_histograms.png",
        True,
    ),
    (
        "boxplots_turface_19_baseline.png",
        plot_trait_boxplots_mod.plot_trait_boxplots,
        "trait_boxplots.png",
        True,
    ),
    (
        "correlation_matrix_turface_19_baseline.png",
        plot_correlation_matrix_mod.plot_correlation_matrix,
        "correlation_matrix.png",
        True,
    ),
]

# (baseline basename, tool fn name, catalog key) for the `include_plots=True` optional keys
# (#723). A separate table from `_TOOLS` rather than more rows in it: these tools take a
# Pydantic params model, are `require_clean=True` consumers (seeded via
# `add_cleaned_version`, not `add_experiment`), and emit N figures per call rather than one.
# Concatenating the two would also break `tests/scripts/`'s 4-tuple destructuring.
_OPTIONAL_CATALOG = {
    "pca_analysis": (
        "create_pca_scree_plot",
        "create_pca_biplot",
        "create_feature_contribution_plot",
        "create_feature_contribution_heatmap",
    ),
    "umap_analysis": ("create_umap_single_trait", "create_umap_colored_by_top_traits"),
    "clustering": ("create_cluster_scatter_pca", "create_cluster_size_barplot"),
}
# No committed baseline: UMAP's embedding is not bit-reproducible across numba/LLVM, which
# moves an axis tick label's width and therefore the `bbox_inches="tight"` canvas. PR #841's
# first ubuntu-latest run measured a 2px width difference (769 -> 771) against the
# macOS-generated baseline while all 6 other keys passed. `compare_images` raises on a
# dimension mismatch rather than returning an RMS, so no tolerance can absorb it, and a
# Linux-generated baseline would merely invert the failure for macOS developers. These keys
# are still rendered and commit-checked by `test_viz_snapshot.py`, just not pixel-compared.
_CROSS_PLATFORM_UNSTABLE_KEYS = frozenset(
    {"create_umap_single_trait", "create_umap_colored_by_top_traits"}
)
_OPTIONAL_TOOLS = [
    (f"{key}_turface_19_baseline.png", fn_name, f"{key}.png")
    for fn_name, keys in _OPTIONAL_CATALOG.items()
    for key in keys
    if key not in _CROSS_PLATFORM_UNSTABLE_KEYS
]


def _report_regeneration(target: Path, produced: Path, rel: Path) -> str:
    """Return the print-worthy message for overwriting (or first-writing) one baseline.

    Pure w.r.t. I/O beyond the two reads `compare_images` itself does -- no copying,
    no printing -- so this is unit-testable in isolation (`tests/scripts/`).
    """
    if not target.is_file():
        return f"wrote {rel} (new baseline, no prior version to diff against)"
    try:
        diff = compare_images(str(target), str(produced), tol=0, in_decorator=True)
    except ImageComparisonFailure as exc:
        # `compare_images` RAISES on a pixel-dimension mismatch rather than returning an
        # RMS. That cannot happen for the dpi-pinned tools, but the #723 optional keys save
        # with `bbox_inches="tight"` and no dpi, so their canvas is derived from rendered
        # text extents -- and a platform whose font metrics or data labels differ by a pixel
        # or two produces a different-sized PNG. Report it as the regeneration-worthy event
        # it is instead of taking the whole `build()` down with an exception.
        return (
            f"REGENERATED {rel}: CANVAS SIZE CHANGED ({exc}) -- no RMS is computable "
            "across different dimensions. This is the expected shape of a cross-platform "
            "regeneration; confirm the content is otherwise unchanged before accepting it."
        )
    rms = diff["rms"] if diff else 0.0
    return (
        f"REGENERATED {rel}: old-vs-new RMS={rms:.1f} -- if this is not ~0, "
        "the PR description should say what visually changed and why "
        "(see this script's module docstring)"
    )


def _render_converged(tool_fn, produced_name: str, capture_root: Path) -> Path:
    """Render one #466-converged tool and return the committed PNG on disk.

    These tools read through the `ExperimentReader` port and persist through a
    `ResultStore`, so the script's `TRAITS_DIR`/`PLOTS_DIR` monkeypatching does not reach
    them. A `FakeReader`/`FakeResultStore` pair stands in, and the bytes are copied out
    inside a `commit` spy — `FakeResultStore.commit` deletes the staging dir on success, so
    that is the last moment the committed file exists. The pixels are the committed ones,
    not an intermediate render.
    """
    import pandas as pd
    from bloom_mcp.data_access import FakeReader, SupabaseReader
    from bloom_mcp.result_store import FakeResultStore, SupabaseResultStore
    from bloom_mcp.tools import _ports

    capture_root.mkdir(parents=True, exist_ok=True)
    reader = FakeReader()
    reader.add_experiment(_EXPERIMENT, pd.read_csv(_RAW))
    store = FakeResultStore()
    real_commit = store.commit

    def _spy_commit(run, outputs):
        for name in outputs:
            shutil.copy(run.staging_dir / name, capture_root / name)
        return real_commit(run, outputs)

    store.commit = _spy_commit
    _ports.configure(reader=reader, store=store)
    try:
        tool_fn(experiment=_EXPERIMENT)
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())

    produced = capture_root / produced_name
    if not produced.is_file():
        raise RuntimeError(f"expected {produced} to exist after commit")
    return produced


def _render_optional(fn_name: str, capture_root: Path) -> Path:
    """Render one `include_plots=True` tool's figures and return the capture dir.

    Same `commit`-spy technique as `_render_converged` (the staging dir is deleted on
    commit, so that is the last moment the bytes exist), differing only in seeding a
    *cleaned* version -- these tools reject a raw frame with `require_clean=True` -- and in
    taking a params model with `include_plots=True` rather than a bare experiment string.
    """
    import pandas as pd
    from bloom_mcp.data_access import FakeReader, SupabaseReader
    from bloom_mcp.result_store import FakeResultStore, SupabaseResultStore
    from bloom_mcp.sections.sleap_roots.analysis.clustering import (
        ClusteringParams,
        clustering,
    )
    from bloom_mcp.sections.sleap_roots.analysis.pca_analysis import (
        PCAAnalysisParams,
        pca_analysis,
    )
    from bloom_mcp.sections.sleap_roots.analysis.umap_analysis import (
        UMAPAnalysisParams,
        umap_analysis,
    )
    from bloom_mcp.tools import _ports

    specs = {
        "pca_analysis": (pca_analysis, PCAAnalysisParams),
        "umap_analysis": (umap_analysis, UMAPAnalysisParams),
        "clustering": (clustering, ClusteringParams),
    }
    tool_fn, model = specs[fn_name]

    capture_root.mkdir(parents=True, exist_ok=True)
    reader = FakeReader()
    reader.add_cleaned_version(_EXPERIMENT, "v1", pd.read_csv(_RAW), make_latest=True)
    store = FakeResultStore()
    real_commit = store.commit

    def _spy_commit(run, outputs):
        for name in outputs:
            if name.endswith(".png"):
                shutil.copy(run.staging_dir / name, capture_root / name)
        return real_commit(run, outputs)

    store.commit = _spy_commit
    _ports.configure(reader=reader, store=store)
    try:
        tool_fn(model(experiment=_EXPERIMENT, include_plots=True))
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())
    return capture_root


def build(tmp_path: Path, *, confirmed: bool) -> bool:
    """Render all 3 tools and print each baseline's old-vs-new RMS. Only actually writes
    the PNGs if `confirmed` is True, or none of them already exist (a first-time run has
    nothing to silently launder) -- all-or-nothing, not a per-file mix, so the "did this
    actually write anything" question always has one simple answer. Returns whether
    anything was written (`main()` uses this to decide whether `write_manifest()` runs).
    """
    # Same versioned-manifest miss `tests/tools/conftest.py`'s `viz_env` fixture
    # forces via `fake_supabase_storage` -- no Supabase env is configured here, so
    # without this, `load_experiment_data`'s manifest lookup raises before ever
    # falling through to the raw TRAITS_DIR read.
    _manifest.list_prefix = lambda _prefix: []
    _sc.list_prefix = lambda _prefix: []

    traits = tmp_path / "traits"
    traits.mkdir()
    shutil.copy(_RAW, traits / _EXPERIMENT)
    eu.TRAITS_DIR = traits

    _BASELINES.mkdir(parents=True, exist_ok=True)
    copies: list[tuple[Path, Path]] = []
    any_existing = False
    # The 4th tuple field was a `converged` flag selecting between this ResultStore
    # capture and a legacy branch that read the PNG straight out of PLOTS_DIR. #462 retired
    # the last two tools that took the legacy branch, so every entry is converged now; the
    # field is kept only because the test module destructures the tuple.
    for baseline_name, tool_fn, produced_name, _converged in _TOOLS:
        produced = _render_converged(tool_fn, produced_name, tmp_path / "committed")

        target = _BASELINES / baseline_name
        rel = target.relative_to(_FIXTURES.parents[1])
        print(_report_regeneration(target, produced, rel))
        any_existing = any_existing or target.is_file()
        copies.append((target, produced))

    # The optional `include_plots=True` keys (#723). Rendered once per tool -- each call
    # emits every key in that tool's catalog -- then matched to baselines by filename.
    optional_capture = tmp_path / "committed_optional"
    for fn_name in sorted({fn for _b, fn, _p in _OPTIONAL_TOOLS}):
        _render_optional(fn_name, optional_capture)
    for baseline_name, _fn_name, produced_name in _OPTIONAL_TOOLS:
        produced = optional_capture / produced_name
        if not produced.is_file():
            raise RuntimeError(f"expected {produced} to exist after commit")
        target = _BASELINES / baseline_name
        rel = target.relative_to(_FIXTURES.parents[1])
        print(_report_regeneration(target, produced, rel))
        any_existing = any_existing or target.is_file()
        copies.append((target, produced))

    if any_existing and not confirmed:
        return False
    for target, produced in copies:
        shutil.copy(produced, target)
    return True


def _version(module_name: str) -> str:
    """Best-effort installed version, so a missing optional dep is recorded, not fatal."""
    try:
        return importlib.metadata.version(module_name)
    except importlib.metadata.PackageNotFoundError:  # pragma: no cover - env-dependent
        return "not installed"


def write_manifest() -> None:
    manifest = {
        "_comment": (
            "Rendering-environment provenance for the baseline PNGs in this directory -- "
            "NOT asserted by any test (pixel content is compared via "
            "matplotlib.testing.compare.compare_images, not this file). Regenerate "
            "on Linux (matching the python-audit ubuntu-latest runner) via "
            "scripts/gen_plot_snapshots_golden.py after any intentional rendering change "
            "(matplotlib bump, plot-style-kwargs default change, delegate upgrade)."
        ),
        "matplotlib_version": matplotlib.__version__,
        "pillow_version": PIL.__version__,
        "sleap_roots_analyze_version": sra.__version__,
        "platform": platform.platform(),
        "python_version": platform.python_version(),
        # Everything below was added with the #723 optional-key baselines. Without it a
        # cross-platform failure is undiagnosable: a scikit-learn `svd_flip` change alone
        # mirrors the biplot for a scientifically meaningless reason, adjustText's absence
        # is swallowed by a bare `except ImportError` and silently changes the biplot's
        # layout, and FreeType is the single biggest driver of the text-extent differences
        # that move a `bbox_inches="tight"` canvas.
        "_optional_key_comment": (
            "Libraries below back the pca_analysis/umap_analysis/clustering optional plot "
            "keys; fixture_sha256 ties these baselines to the CSV that produced them, and "
            "seeds record the stochastic tools' resolved defaults."
        ),
        "umap_learn_version": _version("umap-learn"),
        "numba_version": _version("numba"),
        "llvmlite_version": _version("llvmlite"),
        "scikit_learn_version": _version("scikit-learn"),
        "seaborn_version": _version("seaborn"),
        "adjusttext_version": _version("adjustText"),
        "numpy_version": _version("numpy"),
        "freetype_version": matplotlib.ft2font.__freetype_version__,
        "fixture": _RAW.name,
        "fixture_sha256": hashlib.sha256(_RAW.read_bytes()).hexdigest(),
        "seeds": {"umap_analysis": 42, "clustering": 42, "pca_analysis": None},
    }
    out = _BASELINES / "MANIFEST.json"
    out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {out.relative_to(_FIXTURES.parents[1])}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--yes",
        action="store_true",
        help=(
            "Confirm overwriting existing baselines. Without it, prints the old-vs-new "
            "RMS for every file and exits without writing anything."
        ),
    )
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        wrote = build(Path(tmp), confirmed=args.yes)
    if not wrote:
        print(
            "\nExisting baselines found -- nothing was written. Review the RMS values "
            "above, then rerun with --yes to actually overwrite them."
        )
        raise SystemExit(1)
    write_manifest()


if __name__ == "__main__":
    main()
