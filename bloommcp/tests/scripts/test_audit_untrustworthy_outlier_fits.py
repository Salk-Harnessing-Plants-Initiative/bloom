"""Unit tests for the untrustworthy-outlier-fit audit script's pure logic (bloom#593).

`bloommcp/scripts/` is not a package (mirrors `test_audit_stale_outlier_trims.py`), so
the module is loaded by path. Manifests are real, on-disk objects via the
`local_manifest_backend` fixture (`conftest.py`) -- this script reads through
`AnalysisDir`/the storage backend directly, not through `FakeReader`/`FakeResultStore`.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from manifest_fixtures import write_cleaned_manifest, write_outlier_trim_manifest

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "audit_untrustworthy_outlier_fits.py"
)
_spec = importlib.util.spec_from_file_location(
    "audit_untrustworthy_outlier_fits", _SCRIPT_PATH
)
assert _spec and _spec.loader
audit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit)


def _hit_for(report: dict, stem: str) -> dict:
    matches = [h for h in report["hits"] if h["stem"] == stem]
    assert len(matches) == 1, f"expected exactly one hit for {stem!r}, got {matches}"
    return matches[0]


# --- (a) the actual #419 hazard: an untrustworthy fit predates the gate --------
def test_untrustworthy_fit_that_predates_the_gate_is_reported(local_manifest_backend):
    write_outlier_trim_manifest(
        local_manifest_backend,
        "exp_a",
        "outliers",
        "v1",
        "2026-01-01T00:00:00Z",
        based_on_version="v3_cleaned",
        goodness_of_fit={"fit_quality": "very_poor", "p_value": 0.001},
        n_outliers=8,
        n_input_samples=158,
        n_output_samples=150,
    )

    report = audit.scan_for_untrustworthy_outlier_fits()

    hit = _hit_for(report, "exp_a")
    assert hit["run_ref"] == "v1"
    assert hit["based_on_version"] == "v3_cleaned"
    assert hit["created_at"] == "2026-01-01T00:00:00Z"
    assert hit["fit_quality"] == "very_poor"
    assert hit["method"] == "mahalanobis"
    assert hit["n_outliers"] == 8
    assert hit["n_input_samples"] == 158
    assert hit["n_output_samples"] == 150
    assert report["errors"] == []
    assert report["experiments_scanned"] == 1


def test_poor_and_unknown_fit_quality_are_also_hits(local_manifest_backend):
    write_outlier_trim_manifest(
        local_manifest_backend,
        "exp_poor",
        "outliers",
        "v1",
        "2026-01-01T00:00:00Z",
        based_on_version="v1_cleaned",
        goodness_of_fit={"fit_quality": "poor"},
        n_outliers=9,
        n_input_samples=129,
        n_output_samples=120,
    )
    write_outlier_trim_manifest(
        local_manifest_backend,
        "exp_unknown",
        "outliers",
        "v1",
        "2026-01-01T00:00:00Z",
        based_on_version="v1_cleaned",
        goodness_of_fit={"fit_quality": "unknown"},
        n_outliers=1,
        n_input_samples=10,
        n_output_samples=9,
    )

    report = audit.scan_for_untrustworthy_outlier_fits()

    assert _hit_for(report, "exp_poor")["fit_quality"] == "poor"
    assert _hit_for(report, "exp_unknown")["fit_quality"] == "unknown"
    assert report["experiments_scanned"] == 2


# --- (b) an acceptable-or-better fit is not a hit ------------------------------
def test_acceptable_or_better_fit_is_not_a_hit(local_manifest_backend):
    write_outlier_trim_manifest(
        local_manifest_backend,
        "exp_good",
        "outliers",
        "v1",
        "2026-01-01T00:00:00Z",
        based_on_version="v1_cleaned",
        goodness_of_fit={"fit_quality": "excellent"},
        n_outliers=2,
        n_input_samples=100,
        n_output_samples=98,
    )

    report = audit.scan_for_untrustworthy_outlier_fits()

    assert report["hits"] == []
    assert report["errors"] == []
    assert report["experiments_scanned"] == 1


# --- (c) isolation_forest (no fit report at all) is not a hit ------------------
def test_isolation_forest_trim_is_not_a_hit(local_manifest_backend):
    write_outlier_trim_manifest(
        local_manifest_backend,
        "exp_iforest",
        "outliers",
        "v1",
        "2026-01-01T00:00:00Z",
        based_on_version="v1_cleaned",
        goodness_of_fit=None,
        n_outliers=16,
        n_input_samples=158,
        n_output_samples=142,
        method="isolation_forest",
    )

    report = audit.scan_for_untrustworthy_outlier_fits()

    assert report["hits"] == []
    assert report["experiments_scanned"] == 1


# --- (d) defensive: latest not remove_outliers-authored ------------------------
def test_latest_not_remove_outliers_authored_is_not_a_hit(local_manifest_backend):
    """Not expected in a real `outliers_<stem>` manifest (only `remove_outliers`
    writes to that class), but the scan must not crash if it somehow occurred."""
    write_outlier_trim_manifest(
        local_manifest_backend,
        "exp_weird",
        "outliers",
        "v1",
        "2026-01-01T00:00:00Z",
        based_on_version="v1_cleaned",
        goodness_of_fit={"fit_quality": "very_poor"},
        n_outliers=8,
        n_input_samples=158,
        n_output_samples=150,
        tool="something_else",
    )

    report = audit.scan_for_untrustworthy_outlier_fits()

    assert report["hits"] == []
    assert report["errors"] == []
    assert report["experiments_scanned"] == 1


# --- (e) Decision 2 scope boundary: legacy qc_ manifest is out of scope --------
def test_legacy_qc_manifest_with_no_outliers_manifest_is_out_of_scope(
    local_manifest_backend,
):
    """A pre-#420 trim whose `qc_<stem>` manifest's `latest` is still that
    `remove_outliers` entry, with no `outliers_<stem>` manifest ever created for
    it, is a real, disclosed scope exclusion (design.md Decision 2) -- this scan
    is bounded to `outliers_<stem>` manifests only."""
    write_cleaned_manifest(
        local_manifest_backend,
        "exp_legacy",
        "qc",
        "v1",
        "2026-01-01T00:00:00Z",
        b"a\n1\n",
        tool="qc_clean",
        based_on_version="raw",
    )
    # A second, remove_outliers-authored version becomes this qc_ manifest's
    # latest -- the pre-#420 shared-class shape -- with an untrustworthy fit.
    # write_cleaned_manifest only ever writes a fresh one-version manifest, so
    # build the second version directly via manifest_fixtures' lower-level pieces
    # (mirrors append_cleaned_version, but qc_clean's own helper carries no
    # outlier_report.json -- irrelevant here since this stem is never resolved).
    from bloom_mcp.manifest import (
        AnalysisDir,
        VersionEntry,
        get_code_versions,
        write_manifest,
    )
    from bloom_mcp.supabase_client import upload_file

    prefix = "bloommcp_output/qc_exp_legacy/"
    src = local_manifest_backend / "qc_v2_seed.csv"
    src.write_bytes(b"trim\n1\n")
    upload_file(f"{prefix}v2_2026-08-04/_cleaned.csv", src)
    manifest = AnalysisDir("bloommcp_output", "exp_legacy.csv", "qc").read_manifest()
    assert manifest is not None
    manifest.versions.append(
        VersionEntry(
            id="v2",
            created_at="2026-01-01T00:00:01Z",
            tool="remove_outliers",
            params={},
            based_on_version="v1_cleaned",
            code_versions=get_code_versions(),
            outputs={"_cleaned.csv": "_cleaned.csv"},
            version_dir="v2_2026-08-04",
        )
    )
    manifest.latest = "v2"
    write_manifest(prefix, manifest)

    report = audit.scan_for_untrustworthy_outlier_fits()

    assert report["hits"] == []
    assert report["experiments_scanned"] == 0  # no outliers_<stem> prefix exists


# --- (f) no latest pointer ------------------------------------------------------
def test_manifest_with_no_latest_pointer_is_skipped(local_manifest_backend):
    from bloom_mcp.manifest import ExperimentBlock, Manifest, write_manifest

    prefix = "bloommcp_output/outliers_exp_nolatest/"
    manifest = Manifest(
        experiment=ExperimentBlock(
            filename="exp_nolatest.csv", source_path="", input_sha256=""
        ),
        versions=[],
        latest=None,
    )
    write_manifest(prefix, manifest)

    report = audit.scan_for_untrustworthy_outlier_fits()

    assert report["hits"] == []
    assert report["errors"] == []
    assert report["experiments_scanned"] == 1


# --- (g) missing/malformed outlier_report.json ---------------------------------
def test_missing_output_key_for_report_is_recorded_as_an_error(local_manifest_backend):
    from bloom_mcp.manifest import (
        ExperimentBlock,
        Manifest,
        VersionEntry,
        get_code_versions,
        write_manifest,
    )
    from bloom_mcp.supabase_client import upload_file

    prefix = "bloommcp_output/outliers_exp_noreport/"
    src = local_manifest_backend / "v1_seed.csv"
    src.write_bytes(b"trim\n1\n")
    upload_file(f"{prefix}v1_2026-08-04/_cleaned.csv", src)
    entry = VersionEntry(
        id="v1",
        created_at="2026-01-01T00:00:00Z",
        tool="remove_outliers",
        params={},
        based_on_version="v1_cleaned",
        code_versions=get_code_versions(),
        outputs={"_cleaned.csv": "_cleaned.csv"},  # no outlier_report.json at all
        version_dir="v1_2026-08-04",
    )
    manifest = Manifest(
        experiment=ExperimentBlock(
            filename="exp_noreport.csv", source_path="", input_sha256=""
        ),
        versions=[entry],
        latest="v1",
    )
    write_manifest(prefix, manifest)

    report = audit.scan_for_untrustworthy_outlier_fits()

    assert report["hits"] == []
    errors = [e for e in report["errors"] if e["stem"] == "exp_noreport"]
    assert len(errors) == 1
    assert report["experiments_scanned"] == 1


def test_unreadable_report_json_is_recorded_as_an_error_not_a_crash(
    local_manifest_backend,
):
    from bloom_mcp.supabase_client import write_json

    write_outlier_trim_manifest(
        local_manifest_backend,
        "exp_badreport",
        "outliers",
        "v1",
        "2026-01-01T00:00:00Z",
        based_on_version="v1_cleaned",
        goodness_of_fit={"fit_quality": "very_poor"},
        n_outliers=8,
        n_input_samples=158,
        n_output_samples=150,
    )
    # Corrupt the report after the fact: overwrite with a payload whose
    # goodness_of_fit is not a dict, forcing a KeyError inside the scan when it
    # tries to read n_outliers/etc. from an otherwise-empty report.
    write_json(
        "bloommcp_output/outliers_exp_badreport/v1_2026-08-04/outlier_report.json",
        {"goodness_of_fit": {"fit_quality": "very_poor"}},  # missing n_outliers etc.
    )

    report = audit.scan_for_untrustworthy_outlier_fits()

    errors = [e for e in report["errors"] if e["stem"] == "exp_badreport"]
    assert len(errors) == 1
    assert report["experiments_scanned"] == 1


# --- (h) malformed manifest.json -----------------------------------------------
def test_malformed_manifest_does_not_abort_the_scan(local_manifest_backend):
    from manifest_fixtures import write_invalid_schema_manifest

    write_invalid_schema_manifest("exp_bad", "outliers")
    write_outlier_trim_manifest(
        local_manifest_backend,
        "exp_good",
        "outliers",
        "v1",
        "2026-01-01T00:00:00Z",
        based_on_version="v1_cleaned",
        goodness_of_fit={"fit_quality": "very_poor"},
        n_outliers=8,
        n_input_samples=158,
        n_output_samples=150,
    )

    report = audit.scan_for_untrustworthy_outlier_fits()

    _hit_for(report, "exp_good")
    bad_errors = [e for e in report["errors"] if e["stem"] == "exp_bad"]
    assert len(bad_errors) == 1
    assert report["experiments_scanned"] == 2


# --- (i) empty bucket -----------------------------------------------------------
def test_empty_bucket_produces_empty_successful_report(local_manifest_backend):
    report = audit.scan_for_untrustworthy_outlier_fits()

    assert report == {"hits": [], "errors": [], "experiments_scanned": 0}


# --- (j) enumeration itself fails ------------------------------------------------
def test_enumeration_failure_propagates(local_manifest_backend, monkeypatch):
    def _boom(_prefix):
        raise RuntimeError("storage backend unreachable")

    monkeypatch.setattr(audit, "list_prefix", _boom)

    try:
        audit.scan_for_untrustworthy_outlier_fits()
    except RuntimeError as exc:
        assert "unreachable" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("scan_for_untrustworthy_outlier_fits should have raised")


# --- run()'s exit-code contract --------------------------------------------------
def test_run_returns_zero_and_writes_report_on_success(local_manifest_backend, capsys):
    write_outlier_trim_manifest(
        local_manifest_backend,
        "exp_a",
        "outliers",
        "v1",
        "2026-01-01T00:00:00Z",
        based_on_version="v1_cleaned",
        goodness_of_fit={"fit_quality": "very_poor"},
        n_outliers=8,
        n_input_samples=158,
        n_output_samples=150,
    )

    from bloom_mcp.supabase_client import read_json

    code = audit.run()

    assert code == 0
    printed = capsys.readouterr().out
    assert "report written to" in printed

    keys = _list_all_keys_under_report_prefix(local_manifest_backend)
    assert len(keys) == 1
    written = read_json(keys[0])
    assert written["experiments_scanned"] == 1
    assert len(written["hits"]) == 1
    assert written["errors"] == []
    assert "scanned_at" in written and written["scanned_at"].endswith("Z")
    assert written["storage_backend"] == "local"
    assert written["scope_note"] == audit.SCOPE_NOTE


def test_write_report_keys_never_collide_even_within_the_same_second(
    local_manifest_backend,
):
    report = {"hits": [], "errors": [], "experiments_scanned": 0}

    key_one = audit.write_report(report)
    key_two = audit.write_report(report)

    assert key_one != key_two
    keys = _list_all_keys_under_report_prefix(local_manifest_backend)
    assert len(keys) == 2


def test_run_returns_one_and_writes_nothing_on_enumeration_failure(
    local_manifest_backend, monkeypatch, capsys
):
    def _boom(_prefix):
        raise RuntimeError("storage backend unreachable")

    monkeypatch.setattr(audit, "list_prefix", _boom)

    code = audit.run()

    assert code == 1
    assert "unreachable" in capsys.readouterr().err
    assert _list_all_keys_under_report_prefix(local_manifest_backend) == []


def _list_all_keys_under_report_prefix(local_manifest_backend: Path) -> list[str]:
    from bloom_mcp.supabase_client import list_prefix

    names = list_prefix("bloommcp_output/_audit_reports/")
    return [f"bloommcp_output/_audit_reports/{n}" for n in names]


# --- no-mutation guarantee: the scan never writes/uploads anything -------------
def test_scan_never_writes_or_uploads_even_with_hits_and_errors(local_manifest_backend):
    from bloom_mcp.supabase_client import upload_file

    # Build fixtures BEFORE patching -- these helpers themselves legitimately
    # write/upload (that's how a manifest gets seeded at all); only the scan
    # itself, called below, must make zero such calls.
    write_outlier_trim_manifest(
        local_manifest_backend,
        "exp_a",
        "outliers",
        "v1",
        "2026-01-01T00:00:00Z",
        based_on_version="v1_cleaned",
        goodness_of_fit={"fit_quality": "very_poor"},
        n_outliers=8,
        n_input_samples=158,
        n_output_samples=150,
    )
    bad = local_manifest_backend / "bad.json"
    bad.write_bytes(b"{not valid json")
    upload_file("bloommcp_output/outliers_exp_bad/manifest.json", bad)

    import bloom_mcp.manifest as manifest_pkg
    import bloom_mcp.manifest.manifest as manifest_mod
    import bloom_mcp.supabase_client as sc

    def _forbidden(*_a, **_k):
        raise AssertionError(
            "scan_for_untrustworthy_outlier_fits must never write/upload"
        )

    to_patch = [
        (manifest_mod, "write_manifest"),
        (manifest_pkg, "write_manifest"),
        (manifest_mod, "write_json"),
        (sc, "write_json"),
        (sc, "upload_file"),
        (sc, "delete_files"),
    ]
    originals = [(mod, name, getattr(mod, name)) for mod, name in to_patch]
    try:
        for mod, name in to_patch:
            setattr(mod, name, _forbidden)

        report = audit.scan_for_untrustworthy_outlier_fits()
        assert len(report["hits"]) == 1
        assert len(report["errors"]) == 1
    finally:
        for mod, name, orig in originals:
            setattr(mod, name, orig)
