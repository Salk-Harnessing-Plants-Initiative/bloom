"""Unit tests for `gen_plot_snapshots_golden.py`'s pure `_report_regeneration` helper
(#713 review follow-up).

`bloommcp/scripts/` is not a package (mirrors `test_audit_stale_outlier_trims.py`'s own
note), so the module is loaded by path. Only `_report_regeneration` is exercised here --
it does no I/O beyond the two reads `compare_images` itself performs (no copying, no
printing), unlike `build`/`main`, which need the full `viz_env`-style fixture setup and are
already covered end-to-end by `scripts/gen_plot_snapshots_golden.py` producing the real,
committed baselines this repo ships.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from PIL import Image, ImageEnhance

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "gen_plot_snapshots_golden.py"
)
_spec = importlib.util.spec_from_file_location("gen_plot_snapshots_golden", _SCRIPT_PATH)
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
