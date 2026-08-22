"""Confirms the `sleap-roots-contracts` pin (bloom #653) actually resolved to a version
that ships `RunManifest`/`RUN_MANIFEST_FILENAME` — a hard runtime dependency, not gated by
`pytest.importorskip`."""

import pytest
from sleap_roots_contracts import RUN_MANIFEST_FILENAME, RunManifest


def test_run_manifest_filename_is_the_pinned_literal():
    """Pins the literal so a future change to the constant's value is a visible, deliberate
    test update here, not a silent divergence from what downstream consumers expect."""
    assert RUN_MANIFEST_FILENAME == "run_manifest.json"


def test_run_manifest_round_trips_pipeline_run_id_and_scan_keys():
    manifest = RunManifest(pipeline_run_id="x", scan_keys=["scan_1"])
    assert manifest.pipeline_run_id == "x"
    assert manifest.scan_keys == ["scan_1"]


def test_run_manifest_rejects_empty_scan_keys():
    with pytest.raises(ValueError):
        RunManifest(pipeline_run_id="x", scan_keys=[])
