"""Confirms the `sleap-roots-contracts` pin actually resolved to a version that ships what
bloomctl imports: `RunManifest`/`RUN_MANIFEST_FILENAME` (a7+, bloom #653) and the per-run
run-manifest naming and resolution helpers (a9+, bloom #934) — hard runtime dependencies,
not gated by `pytest.importorskip`.

These prove the *resolved* environment has them, not that the committed `uv.lock` does; the
Docker image builds with `uv sync --frozen`, so the lock itself is what ships."""

import pytest
from sleap_roots_contracts import (
    RUN_MANIFEST_FILENAME,
    RunManifest,
    RunManifestIdentityError,
    RunManifestMissingError,
    load_run_manifest,
    pipeline_run_id_from_env,
    run_manifest_name_for_writing,
)


def test_run_manifest_filename_is_the_pinned_literal():
    """Pins the literal so a future change to the constant's value is a visible, deliberate
    test update here, not a silent divergence from what downstream consumers expect. It is
    still the name a stage with no run identity writes and reads."""
    assert RUN_MANIFEST_FILENAME == "run_manifest.json"


def test_run_manifest_round_trips_pipeline_run_id_and_scan_keys():
    manifest = RunManifest(pipeline_run_id="x", scan_keys=["scan_1"])
    assert manifest.pipeline_run_id == "x"
    assert manifest.scan_keys == ["scan_1"]


def test_run_manifest_rejects_empty_scan_keys():
    with pytest.raises(ValueError):
        RunManifest(pipeline_run_id="x", scan_keys=[])


def test_name_for_writing_without_a_run_id_is_the_legacy_name():
    assert run_manifest_name_for_writing(None) == "run_manifest.json"


def test_name_for_writing_with_a_run_id_is_per_run():
    assert run_manifest_name_for_writing("wf-1") == "run_manifest.wf-1.json"


def test_run_id_from_env_strips_and_treats_blank_as_absent():
    assert pipeline_run_id_from_env({"ARGO_WORKFLOW_NAME": " wf-1\n"}) == "wf-1"
    assert pipeline_run_id_from_env({"ARGO_WORKFLOW_NAME": "   "}) is None
    assert pipeline_run_id_from_env({}) is None


def test_reader_symbols_are_exported():
    assert callable(load_run_manifest)
    assert issubclass(RunManifestMissingError, LookupError)
    assert issubclass(RunManifestIdentityError, Exception)
