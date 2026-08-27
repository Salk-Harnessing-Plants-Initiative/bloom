"""Regenerate the 5 plotting-tool baseline PNGs under
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

Calls the 5 MCP tool functions directly (not just their delegates) against
``turface_19_final_data.csv`` -- the same fixture and tool entrypoints
``tests/tools/test_viz_tools.py``'s ``viz_env`` already exercises -- so the baseline
reflects the full save path (``_viz_shared.save_plot``/``save_plot_or_plots``, including
``dpi``/``bbox_inches``), not a hand-rolled re-render that could quietly drift from what
the tool actually produces.

Run:  cd bloommcp && uv run --frozen --extra test python scripts/gen_plot_snapshots_golden.py

Regenerating over an *existing* baseline is exactly the moment a real rendering
regression could get silently "laundered" into a new golden -- a PR that touches these
PNGs is, by construction, changing the thing the tests exist to catch changes to. To make
that visible rather than invisible, this script reports the old-vs-new RMS
(`matplotlib.testing.compare.compare_images`) for every baseline it overwrites. A PR that
regenerates baselines MUST state, per file, what the reported RMS was and why the change
is expected (matplotlib bump, an intentional style/color default change, etc.) -- not just
commit new PNGs silently. An RMS of 0 (or near it) confirms nothing visually changed and
this run was just re-stamping provenance (e.g. a `sleap-roots-analyze` patch bump with no
rendering effect).
"""

from __future__ import annotations

import json
import platform
import shutil
import tempfile
from pathlib import Path

import matplotlib
import PIL
import sleap_roots_analyze as sra
from matplotlib.testing.compare import compare_images

import bloom_mcp.manifest.manifest as _manifest
import bloom_mcp.supabase_client as _sc
from bloom_mcp import experiment_utils as eu
from bloom_mcp.sections.sleap_roots.analysis import (
    _viz_shared,
    plot_correlation_matrix as plot_correlation_matrix_mod,
    plot_heritability_bar as plot_heritability_bar_mod,
    plot_trait_boxplots as plot_trait_boxplots_mod,
    plot_trait_histograms as plot_trait_histograms_mod,
    plot_variance_decomposition as plot_variance_decomposition_mod,
)

_FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
_RAW = _FIXTURES / "turface_19_final_data.csv"
_EXPERIMENT = "turface_19.csv"
_BASELINES = _FIXTURES / "plot_baselines"

# (output basename, tool callable, PNG name the tool itself writes under PLOTS_DIR)
_TOOLS = [
    (
        "histograms_turface_19_baseline.png",
        plot_trait_histograms_mod.plot_trait_histograms,
        "histograms_turface_19.png",
    ),
    (
        "boxplots_turface_19_baseline.png",
        plot_trait_boxplots_mod.plot_trait_boxplots,
        "boxplots_turface_19.png",
    ),
    (
        "correlation_matrix_turface_19_baseline.png",
        plot_correlation_matrix_mod.plot_correlation_matrix,
        "correlation_matrix_turface_19.png",
    ),
    (
        "heritability_turface_19_baseline.png",
        plot_heritability_bar_mod.plot_heritability_bar,
        "heritability_turface_19.png",
    ),
    (
        "variance_decomposition_turface_19_baseline.png",
        plot_variance_decomposition_mod.plot_variance_decomposition,
        "variance_decomposition_turface_19.png",
    ),
]


def build(tmp_path: Path) -> None:
    # Same versioned-manifest miss `tests/tools/test_viz_tools.py`'s `viz_env` fixture
    # forces via `fake_supabase_storage` -- no Supabase env is configured here, so
    # without this, `load_experiment_data`'s manifest lookup raises before ever
    # falling through to the raw TRAITS_DIR read.
    _manifest.list_prefix = lambda _prefix: []
    _sc.list_prefix = lambda _prefix: []

    traits = tmp_path / "traits"
    traits.mkdir()
    shutil.copy(_RAW, traits / _EXPERIMENT)
    eu.TRAITS_DIR = traits

    plots = tmp_path / "plots"
    eu.PLOTS_DIR = plots
    _viz_shared.PLOTS_DIR = plots

    _BASELINES.mkdir(parents=True, exist_ok=True)
    for baseline_name, tool_fn, produced_name in _TOOLS:
        result = tool_fn(_EXPERIMENT)
        if "Plot saved:" not in result:
            raise RuntimeError(f"{tool_fn.__module__} did not report success: {result}")
        produced = plots / produced_name
        if not produced.is_file():
            raise RuntimeError(f"expected {produced} to exist, tool reported: {result}")

        target = _BASELINES / baseline_name
        rel = target.relative_to(_FIXTURES.parents[1])
        if target.is_file():
            diff = compare_images(str(target), str(produced), tol=0, in_decorator=True)
            rms = diff["rms"] if diff else 0.0
            print(
                f"REGENERATED {rel}: old-vs-new RMS={rms:.1f} -- if this is not ~0, "
                "the PR description MUST say what visually changed and why "
                "(see this script's module docstring)"
            )
        else:
            print(f"wrote {rel} (new baseline, no prior version to diff against)")
        shutil.copy(produced, target)


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
    }
    out = _BASELINES / "MANIFEST.json"
    out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {out.relative_to(_FIXTURES.parents[1])}")


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        build(Path(tmp))
    write_manifest()


if __name__ == "__main__":
    main()
