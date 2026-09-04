"""Unit tests for `gen_plot_snapshots_golden.py`'s pure `_report_regeneration` helper and
`build`'s `--yes` confirmation gate (#713 review follow-ups).

`bloommcp/scripts/` is not a package (mirrors `test_audit_stale_outlier_trims.py`'s own
note), so the module is loaded by path. `_report_regeneration` does no I/O beyond the two
reads `compare_images` itself performs (no copying, no printing). The `build()` gate tests
below DO exercise the real 3 tool calls against the real `turface_19` fixture (same as a
real regeneration run) but monkeypatch `gen._BASELINES` to a `tmp_path` first -- `build()`
writes to the module-level `_BASELINES` constant, which otherwise points at this repo's
real committed baselines; a test must never overwrite those as a side effect of the suite
running (that would mask real drift, not catch it).
"""

from __future__ import annotations

import importlib.util
import io
import tempfile
from pathlib import Path

from PIL import Image, ImageEnhance

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "gen_plot_snapshots_golden.py"
)
_spec = importlib.util.spec_from_file_location(
    "gen_plot_snapshots_golden", _SCRIPT_PATH
)
assert _spec and _spec.loader
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)

_A_BASELINE = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "plot_baselines"
    / "histograms_turface_19_baseline.png"
)


def _use_fake_baselines_dir(monkeypatch, tmp_path: Path) -> Path:
    """Point `gen._BASELINES` -- and the `gen._FIXTURES` ancestor `build()`'s
    `target.relative_to(_FIXTURES.parents[1])` needs to resolve without raising -- at a
    `tmp_path` subdirectory instead of this repo's real committed baselines. Returns the
    fake baselines directory (not yet created; callers `mkdir()` it if they need existing
    files to pre-seed).

    Also registers every module-level global `build()` mutates via plain assignment (not
    `monkeypatch.setattr`) for monkeypatch auto-revert: `eu.TRAITS_DIR`/`eu.PLOTS_DIR`/
    `_viz_shared.PLOTS_DIR`, AND -- easy to miss, and exactly what broke a first version of
    this helper (it registered only the first three, and 30 unrelated tests elsewhere in
    the suite started failing from the corrupted `list_prefix`s leaking through
    `fake_supabase_storage`'s own monkeypatch-revert chain) -- `_manifest.list_prefix` /
    `_sc.list_prefix`. Plain assignment is safe for the script's own one-shot `__main__`
    use, but calling `build()` directly from a test in a shared pytest session would
    otherwise leak all five globals into every test that runs afterward.
    `monkeypatch.setattr(obj, name, <its own current value>)` is enough: monkeypatch
    restores whatever value it captured *before* the test ran at teardown, regardless of
    what `build()` reassigns in between.
    """
    fixtures = tmp_path / "tests" / "fixtures"
    baselines = fixtures / "plot_baselines"
    monkeypatch.setattr(gen, "_FIXTURES", fixtures)
    monkeypatch.setattr(gen, "_BASELINES", baselines)
    monkeypatch.setattr(gen.eu, "TRAITS_DIR", gen.eu.TRAITS_DIR)
    monkeypatch.setattr(gen.eu, "PLOTS_DIR", gen.eu.PLOTS_DIR)
    monkeypatch.setattr(gen._viz_shared, "PLOTS_DIR", gen._viz_shared.PLOTS_DIR)
    monkeypatch.setattr(gen._manifest, "list_prefix", gen._manifest.list_prefix)
    monkeypatch.setattr(gen._sc, "list_prefix", gen._sc.list_prefix)
    return baselines


def test_new_baseline_reports_no_prior_version(tmp_path):
    target = tmp_path / "does_not_exist_yet.png"
    msg = gen._report_regeneration(target, _A_BASELINE, Path("rel/path.png"))
    assert "new baseline, no prior version to diff against" in msg
    assert "RMS" not in msg


def test_identical_regeneration_reports_zero_rms(tmp_path):
    target = tmp_path / "target.png"
    target.write_bytes(_A_BASELINE.read_bytes())
    msg = gen._report_regeneration(target, _A_BASELINE, Path("rel/path.png"))
    assert "REGENERATED rel/path.png: old-vs-new RMS=0.0" in msg


def test_visually_changed_regeneration_reports_a_nonzero_rms(tmp_path):
    target = tmp_path / "target.png"
    target.write_bytes(_A_BASELINE.read_bytes())
    dimmed = ImageEnhance.Brightness(Image.open(_A_BASELINE)).enhance(0.9)
    produced = tmp_path / "produced.png"
    dimmed.save(produced)

    msg = gen._report_regeneration(target, produced, Path("rel/path.png"))
    assert "REGENERATED rel/path.png: old-vs-new RMS=" in msg
    rms = float(msg.split("RMS=")[1].split(" ")[0])
    assert rms > 20  # matches the ~24.4 measured for this exact 10% dim elsewhere


# The real, currently-committed baselines dir -- read-only reference for building
# per-target markers below, distinct from `gen._BASELINES`, which tests monkeypatch away.
_REAL_BASELINES_DIR = _A_BASELINE.parent


def _dimension_matched_markers() -> dict[str, bytes]:
    """One marker PNG per real baseline name, each a 50%-dimmed copy of THAT specific
    baseline -- same pixel dimensions as what the corresponding tool will actually
    re-render (`compare_images` raises `ImageComparisonFailure` on a dimension mismatch,
    so a single marker shared across all 3 differently-sized baselines doesn't work), but
    different content, so it's distinguishable from a fresh, correct re-render.
    """
    markers = {}
    for baseline_name, _tool_fn, _produced_name in gen._TOOLS:
        real = _REAL_BASELINES_DIR / baseline_name
        dimmed = ImageEnhance.Brightness(Image.open(real)).enhance(0.5)
        buf = io.BytesIO()
        dimmed.save(buf, format="PNG")
        markers[baseline_name] = buf.getvalue()
    return markers


def test_build_without_yes_does_not_overwrite_existing_baselines(tmp_path, monkeypatch):
    fake_baselines = _use_fake_baselines_dir(monkeypatch, tmp_path)
    fake_baselines.mkdir(parents=True)
    markers = _dimension_matched_markers()
    for baseline_name, marker in markers.items():
        (fake_baselines / baseline_name).write_bytes(marker)

    with tempfile.TemporaryDirectory() as scratch:
        wrote = gen.build(Path(scratch), confirmed=False)

    assert wrote is False
    for baseline_name, marker in markers.items():
        assert (fake_baselines / baseline_name).read_bytes() == marker


def test_build_with_yes_overwrites_existing_baselines(tmp_path, monkeypatch):
    fake_baselines = _use_fake_baselines_dir(monkeypatch, tmp_path)
    fake_baselines.mkdir(parents=True)
    markers = _dimension_matched_markers()
    for baseline_name, marker in markers.items():
        (fake_baselines / baseline_name).write_bytes(marker)

    with tempfile.TemporaryDirectory() as scratch:
        wrote = gen.build(Path(scratch), confirmed=True)

    assert wrote is True
    for baseline_name, marker in markers.items():
        assert (fake_baselines / baseline_name).read_bytes() != marker


def test_build_writes_new_baselines_without_needing_yes(tmp_path, monkeypatch):
    # Directory intentionally left uncreated -- does not exist yet, nothing to launder.
    fake_baselines = _use_fake_baselines_dir(monkeypatch, tmp_path)

    with tempfile.TemporaryDirectory() as scratch:
        wrote = gen.build(Path(scratch), confirmed=False)

    assert wrote is True
    for baseline_name, _tool_fn, _produced_name in gen._TOOLS:
        assert (fake_baselines / baseline_name).is_file()
