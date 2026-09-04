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

Calls the 5 MCP tool functions directly (not just their delegates) against
``turface_19_final_data.csv`` -- the same fixture and tool entrypoints
``tests/tools/test_viz_tools.py``'s ``viz_env`` already exercises -- so the baseline
reflects the full save path (``_viz_shared.save_plot``/``save_plot_or_plots``, including
``dpi``/``bbox_inches``), not a hand-rolled re-render that could quietly drift from what
the tool actually produces.

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
    plot_trait_boxplots as plot_trait_boxplots_mod,
    plot_trait_histograms as plot_trait_histograms_mod,
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
]


def _report_regeneration(target: Path, produced: Path, rel: Path) -> str:
    """Return the print-worthy message for overwriting (or first-writing) one baseline.

    Pure w.r.t. I/O beyond the two reads `compare_images` itself does -- no copying,
    no printing -- so this is unit-testable in isolation (`tests/scripts/`).
    """
    if not target.is_file():
        return f"wrote {rel} (new baseline, no prior version to diff against)"
    diff = compare_images(str(target), str(produced), tol=0, in_decorator=True)
    rms = diff["rms"] if diff else 0.0
    return (
        f"REGENERATED {rel}: old-vs-new RMS={rms:.1f} -- if this is not ~0, "
        "the PR description should say what visually changed and why "
        "(see this script's module docstring)"
    )


def build(tmp_path: Path, *, confirmed: bool) -> bool:
    """Render all 5 tools and print each baseline's old-vs-new RMS. Only actually writes
    the PNGs if `confirmed` is True, or none of them already exist (a first-time run has
    nothing to silently launder) -- all-or-nothing, not a per-file mix, so the "did this
    actually write anything" question always has one simple answer. Returns whether
    anything was written (`main()` uses this to decide whether `write_manifest()` runs).
    """
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
    copies: list[tuple[Path, Path]] = []
    any_existing = False
    for baseline_name, tool_fn, produced_name in _TOOLS:
        result = tool_fn(_EXPERIMENT)
        if "Plot saved:" not in result:
            raise RuntimeError(f"{tool_fn.__module__} did not report success: {result}")
        produced = plots / produced_name
        if not produced.is_file():
            raise RuntimeError(f"expected {produced} to exist, tool reported: {result}")

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
