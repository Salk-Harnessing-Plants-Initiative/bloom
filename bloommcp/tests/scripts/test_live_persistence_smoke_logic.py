"""Unit tests for the live-persistence smoke driver's pure logic.

The driver itself only runs end-to-end with the dev stack up, but its decision
logic — provenance assertions, the hash-compare loop, version-advance detection,
and the per-check summary/exit aggregation — is factored into pure helpers that
run with no live Supabase. We exercise them here, backing the download callable
with the in-memory ``fake_supabase_storage`` fixture so the hash loop runs against
the same storage boundary the real driver uses.
"""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

# Load the driver by path — `tests/smoke/` is not an importable package (no __init__).
_DRIVER_PATH = (
    Path(__file__).resolve().parents[1] / "smoke" / "live_persistence_smoke.py"
)
_spec = importlib.util.spec_from_file_location("live_persistence_smoke", _DRIVER_PATH)
assert _spec and _spec.loader
smoke = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(smoke)


# --- summarize / exit-code aggregation ----------------------------------------
def test_summarize_all_pass_exits_zero():
    checks = [smoke.Check("a", True), smoke.Check("b", True)]
    text, code = smoke.summarize(checks)
    assert code == 0
    assert "SMOKE PASSED" in text
    assert "FAIL" not in text


def test_summarize_any_fail_exits_one_and_names_check():
    checks = [smoke.Check("a", True), smoke.Check("bad-check", False, "boom")]
    text, code = smoke.summarize(checks)
    assert code == 1
    assert "SMOKE FAILED" in text
    assert "bad-check" in text  # the failing check is named
    assert "boom" in text


# --- provenance checks --------------------------------------------------------
def _good_prov_kwargs():
    return dict(
        schema_version=5,
        seed=42,
        agent="bloom_agent",
        environment="bloommcp==0.1.0+uvlock:abc",
        output_keys={"labels.csv": "bloommcp_output/clustering_turface/v1/labels.csv"},
        output_sha256={"labels.csv": "deadbeef"},
    )


def test_provenance_checks_all_pass_on_valid_v5_entry():
    checks = smoke.provenance_checks(**_good_prov_kwargs())
    assert all(c.ok for c in checks), [c for c in checks if not c.ok]


def test_provenance_checks_flags_null_seed():
    kwargs = _good_prov_kwargs()
    kwargs["seed"] = None
    checks = smoke.provenance_checks(**kwargs)
    failed = [c.name for c in checks if not c.ok]
    assert any("seed non-null" in n for n in failed)
    assert any("seed == 42" in n for n in failed)


def test_provenance_checks_flags_v2_schema():
    kwargs = _good_prov_kwargs()
    kwargs["schema_version"] = 2
    checks = smoke.provenance_checks(**kwargs)
    assert any(c.name == "manifest schema == 5" and not c.ok for c in checks)


def test_provenance_checks_flags_wrong_agent_and_empty_environment():
    kwargs = _good_prov_kwargs()
    kwargs["agent"] = "someone_else"
    kwargs["environment"] = ""
    checks = smoke.provenance_checks(**kwargs)
    failed = {c.name for c in checks if not c.ok}
    assert any(n.startswith("agent ==") for n in failed)
    assert any(n.startswith("environment is") for n in failed)


def test_provenance_checks_rejects_arbitrary_environment_string():
    # The guarantee is an image-digest / uv.lock pointer, not any truthy string.
    kwargs = _good_prov_kwargs()
    kwargs["environment"] = "x"
    checks = smoke.provenance_checks(**kwargs)
    assert any(c.name.startswith("environment is") and not c.ok for c in checks)


def test_provenance_checks_accepts_image_digest_environment():
    kwargs = _good_prov_kwargs()
    kwargs["environment"] = "sha256:abc123"
    checks = smoke.provenance_checks(**kwargs)
    assert all(c.ok for c in checks), [c for c in checks if not c.ok]


def test_provenance_checks_flags_keyset_mismatch():
    kwargs = _good_prov_kwargs()
    kwargs["output_sha256"] = {"other.csv": "deadbeef"}
    checks = smoke.provenance_checks(**kwargs)
    assert any(
        c.name == "output_keys / output_sha256 share one key-set" and not c.ok
        for c in checks
    )


# --- Tier-3 qc_clean leg checks -----------------------------------------------
def _good_qc_kwargs():
    return dict(
        schema_version=5,
        output_keys={
            "_cleaned.csv": "bloommcp_output/qc_turface_raw/v1/_cleaned.csv",
            "cleanup_log.json": "bloommcp_output/qc_turface_raw/v1/cleanup_log.json",
        },
        output_sha256={"_cleaned.csv": "dead", "cleanup_log.json": "beef"},
        expected_outputs={"_cleaned.csv", "cleanup_log.json"},
    )


def test_qc_persist_checks_all_pass_on_valid_v5_entry():
    checks = smoke.qc_persist_checks(**_good_qc_kwargs())
    assert all(c.ok for c in checks), [c for c in checks if not c.ok]


def test_qc_persist_checks_flags_v2_schema():
    kwargs = _good_qc_kwargs()
    kwargs["schema_version"] = 2
    checks = smoke.qc_persist_checks(**kwargs)
    assert any(c.name == "qc_clean: manifest schema == 5" and not c.ok for c in checks)


def test_qc_persist_checks_flags_missing_cleaned_artifact():
    # Drop cleanup_log.json from the committed outputs → the catalog check fails.
    kwargs = _good_qc_kwargs()
    kwargs["output_keys"] = {
        k: v for k, v in kwargs["output_keys"].items() if k == "_cleaned.csv"
    }
    kwargs["output_sha256"] = {"_cleaned.csv": "dead"}
    checks = smoke.qc_persist_checks(**kwargs)
    assert any("committed outputs include" in c.name and not c.ok for c in checks)


def test_qc_persist_checks_flags_keyset_mismatch():
    kwargs = _good_qc_kwargs()
    kwargs["output_sha256"] = {"_cleaned.csv": "dead"}  # missing cleanup_log.json
    checks = smoke.qc_persist_checks(**kwargs)
    assert any("share one key-set" in c.name and not c.ok for c in checks)


def test_qc_cleaned_read_checks_pass_on_cleaned_source():
    checks = smoke.qc_cleaned_read_checks("v3_cleaned", trait_nan_cells=0)
    assert all(c.ok for c in checks), [c for c in checks if not c.ok]


def test_qc_cleaned_read_checks_flags_raw_source():
    # The contract: require_clean must NOT resolve to the raw input.
    checks = smoke.qc_cleaned_read_checks("raw", trait_nan_cells=0)
    assert any("resolves the cleaned artifact" in c.name and not c.ok for c in checks)


def test_qc_cleaned_read_checks_flags_residual_nans():
    checks = smoke.qc_cleaned_read_checks("v1_cleaned", trait_nan_cells=5)
    assert any("zero NaN trait cells" in c.name and not c.ok for c in checks)


# --- remove_outliers leg checks (#378) ----------------------------------------
def _good_ro_kwargs():
    return dict(
        schema_version=5,
        seed=42,
        tool="remove_outliers",
        output_keys={
            "_cleaned.csv": "bloommcp_output/qc_turface_raw/v2/_cleaned.csv",
            "outlier_report.json": "bloommcp_output/qc_turface_raw/v2/outlier_report.json",
        },
        output_sha256={"_cleaned.csv": "dead", "outlier_report.json": "beef"},
        expected_outputs={"_cleaned.csv", "outlier_report.json"},
    )


def test_ro_persist_checks_all_pass_on_valid_v5_entry():
    checks = smoke.ro_persist_checks(**_good_ro_kwargs())
    assert all(c.ok for c in checks), [c for c in checks if not c.ok]


def test_ro_persist_checks_flags_wrong_tool():
    # The provenance-based composition anchor: if the latest qc run is not the trim
    # (e.g. a stray qc_clean re-run clobbered latest), the composition did not compose.
    kwargs = _good_ro_kwargs()
    kwargs["tool"] = "qc_clean"
    checks = smoke.ro_persist_checks(**kwargs)
    assert any("latest qc run is the trim" in c.name and not c.ok for c in checks)


def test_ro_persist_checks_flags_wrong_seed():
    # remove_outliers is stochastic — a seed != 42 is a reproducibility regression.
    kwargs = _good_ro_kwargs()
    kwargs["seed"] = 7
    checks = smoke.ro_persist_checks(**kwargs)
    assert any("seed == 42" in c.name and not c.ok for c in checks)


def test_ro_persist_checks_flags_null_seed():
    kwargs = _good_ro_kwargs()
    kwargs["seed"] = None
    checks = smoke.ro_persist_checks(**kwargs)
    assert any("seed == 42" in c.name and not c.ok for c in checks)


def test_ro_persist_checks_flags_missing_report_artifact():
    kwargs = _good_ro_kwargs()
    kwargs["output_keys"] = {
        k: v for k, v in kwargs["output_keys"].items() if k == "_cleaned.csv"
    }
    kwargs["output_sha256"] = {"_cleaned.csv": "dead"}
    checks = smoke.ro_persist_checks(**kwargs)
    assert any("committed outputs include" in c.name and not c.ok for c in checks)


def test_ro_trimmed_read_checks_pass_on_fewer_rows_no_nans():
    checks = smoke.ro_trimmed_read_checks(
        "v2_cleaned", trait_nan_cells=0, n_output=150, n_pre_trim=187
    )
    assert all(c.ok for c in checks), [c for c in checks if not c.ok]


def test_ro_trimmed_read_checks_flags_raw_source():
    checks = smoke.ro_trimmed_read_checks(
        "raw", trait_nan_cells=0, n_output=150, n_pre_trim=187
    )
    assert any("resolves the trimmed artifact" in c.name and not c.ok for c in checks)


def test_ro_trimmed_read_checks_pass_on_zero_flagged_no_op_trim():
    # A no-op trim (n_output == n_pre_trim, e.g. mahalanobis flags 0 on the smoke's
    # own cleaned frame) is NOT a regression — the relaxed <= bound must pass it. The
    # trim's persistence is proven separately by the ro_persist_checks tool anchor.
    checks = smoke.ro_trimmed_read_checks(
        "v2_cleaned", trait_nan_cells=0, n_output=187, n_pre_trim=187
    )
    assert all(c.ok for c in checks), [c for c in checks if not c.ok]


def test_ro_trimmed_read_checks_flags_grown_or_empty_frame():
    # A trim that GREW the frame (n_output > n_pre_trim) or emptied it is a real
    # regression the row-count bound still catches.
    grown = smoke.ro_trimmed_read_checks(
        "v2_cleaned", trait_nan_cells=0, n_output=200, n_pre_trim=187
    )
    assert any(
        "no more rows than the pre-trim clean" in c.name and not c.ok for c in grown
    )
    empty = smoke.ro_trimmed_read_checks(
        "v2_cleaned", trait_nan_cells=0, n_output=0, n_pre_trim=187
    )
    assert any(
        "no more rows than the pre-trim clean" in c.name and not c.ok for c in empty
    )


def test_ro_trimmed_read_checks_flags_residual_nans():
    checks = smoke.ro_trimmed_read_checks(
        "v2_cleaned", trait_nan_cells=3, n_output=150, n_pre_trim=187
    )
    assert any("zero NaN trait cells" in c.name and not c.ok for c in checks)


# --- hash-compare loop (against the fake storage boundary) --------------------
def test_hash_checks_pass_when_bytes_match(fake_supabase_storage):
    store = fake_supabase_storage
    key = "bloommcp_output/clustering_turface/v1/labels.csv"
    payload = b"sample_index,cluster\n0,1\n1,0\n"
    store.objects[key] = payload
    output_keys = {"labels.csv": key}
    output_sha256 = {"labels.csv": hashlib.sha256(payload).hexdigest()}

    checks = smoke.hash_checks(
        output_keys, output_sha256, read_bytes=lambda k: store.objects[k]
    )
    assert len(checks) == 1
    assert checks[0].ok


def test_hash_checks_fail_on_mismatch(fake_supabase_storage):
    store = fake_supabase_storage
    key = "bloommcp_output/clustering_turface/v1/labels.csv"
    store.objects[key] = b"actual bytes"
    output_keys = {"labels.csv": key}
    output_sha256 = {"labels.csv": hashlib.sha256(b"different").hexdigest()}

    checks = smoke.hash_checks(
        output_keys, output_sha256, read_bytes=lambda k: store.objects[k]
    )
    assert len(checks) == 1
    assert not checks[0].ok
    assert "recorded=" in checks[0].detail and "actual=" in checks[0].detail


def test_hash_checks_fail_on_download_error():
    def boom(_key):
        raise RuntimeError("object not found")

    checks = smoke.hash_checks(
        {"labels.csv": "missing/key"}, {"labels.csv": "abc"}, read_bytes=boom
    )
    assert len(checks) == 1
    assert not checks[0].ok
    assert "download failed" in checks[0].detail


# --- version-advance detection (relational, not hardcoded v1->v2) -------------
def test_version_advance_pass_v1_to_v2():
    assert smoke.version_advance_check("v1", "v2").ok


def test_version_advance_pass_from_nonzero_start():
    # The regression the relational check guards: a re-run starting past v1 still
    # passes as long as latest advances by exactly one.
    assert smoke.version_advance_check("v3", "v4").ok


def test_version_advance_fail_when_not_advanced():
    assert not smoke.version_advance_check("v1", "v1").ok


def test_version_advance_fail_when_skips_a_version():
    assert not smoke.version_advance_check("v1", "v3").ok


def test_version_advance_fail_on_unparseable_ref():
    assert not smoke.version_advance_check("latest", "v2").ok


# --- bounded retry ------------------------------------------------------------
def test_retry_returns_on_eventual_success():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("not ready")
        return "ok"

    assert smoke.retry(flaky, attempts=5, delay=0) == "ok"
    assert calls["n"] == 3


def test_retry_reraises_after_exhausting_attempts():
    def always_fail():
        raise RuntimeError("never ready")

    try:
        smoke.retry(always_fail, attempts=3, delay=0)
    except RuntimeError as exc:
        assert "never ready" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("retry should have re-raised")


# --- clustering leg checks (#309) ---------------------------------------------
def _good_cl_kwargs():
    return dict(
        schema_version=5,
        seed=42,
        tool="clustering",
        source="v3_cleaned",
        output_keys={
            "labels.csv": "bloommcp_output/clustering_turface_raw/v1/labels.csv",
            "cluster_result.json": "bloommcp_output/clustering_turface_raw/v1/cluster_result.json",
        },
        output_sha256={"labels.csv": "dead", "cluster_result.json": "beef"},
        expected_outputs={"labels.csv", "cluster_result.json"},
    )


def test_clustering_persist_checks_all_pass_on_valid_v5_entry():
    checks = smoke.clustering_persist_checks(**_good_cl_kwargs())
    assert all(c.ok for c in checks), [c for c in checks if not c.ok]


def test_clustering_persist_checks_flags_wrong_seed():
    # clustering is stochastic — a seed != 42 is a reproducibility regression.
    kwargs = _good_cl_kwargs()
    kwargs["seed"] = 7
    checks = smoke.clustering_persist_checks(**kwargs)
    assert any("seed == 42" in c.name and not c.ok for c in checks)


def test_clustering_persist_checks_flags_null_seed():
    kwargs = _good_cl_kwargs()
    kwargs["seed"] = None
    checks = smoke.clustering_persist_checks(**kwargs)
    assert any("seed == 42" in c.name and not c.ok for c in checks)


def test_clustering_persist_checks_flags_wrong_tool():
    kwargs = _good_cl_kwargs()
    kwargs["tool"] = "run_clustering_workflow"
    checks = smoke.clustering_persist_checks(**kwargs)
    assert any("tool == 'clustering'" in c.name and not c.ok for c in checks)


def test_clustering_persist_checks_flags_raw_source():
    # The consumer must resolve a cleaned version, never the raw input.
    kwargs = _good_cl_kwargs()
    kwargs["source"] = "raw"
    checks = smoke.clustering_persist_checks(**kwargs)
    assert any("consumed a cleaned source" in c.name and not c.ok for c in checks)


def test_clustering_persist_checks_flags_missing_result_artifact():
    kwargs = _good_cl_kwargs()
    kwargs["output_keys"] = {
        k: v for k, v in kwargs["output_keys"].items() if k == "labels.csv"
    }
    kwargs["output_sha256"] = {"labels.csv": "dead"}
    checks = smoke.clustering_persist_checks(**kwargs)
    assert any("committed outputs include" in c.name and not c.ok for c in checks)


# --- descriptive_stats leg checks (#488) ---------------------------------------
def _good_stats_kwargs():
    return dict(
        schema_version=5,
        seed=None,
        tool="descriptive_stats",
        source="v3_cleaned",
        output_keys={"stats.csv": "bloommcp_output/stats_turface_raw/v1/stats.csv"},
        output_sha256={"stats.csv": "dead"},
        expected_outputs={"stats.csv"},
    )


def test_stats_persist_checks_all_pass_on_valid_v5_entry():
    checks = smoke.stats_persist_checks(**_good_stats_kwargs())
    assert all(c.ok for c in checks), [c for c in checks if not c.ok]


def test_stats_persist_checks_flags_non_null_seed():
    # descriptive_stats is deterministic — a non-None seed would be a reproducibility lie.
    kwargs = _good_stats_kwargs()
    kwargs["seed"] = 42
    checks = smoke.stats_persist_checks(**kwargs)
    assert any("seed == None" in c.name and not c.ok for c in checks)


def test_stats_persist_checks_flags_wrong_tool():
    kwargs = _good_stats_kwargs()
    kwargs["tool"] = "run_descriptive_stats_workflow"
    checks = smoke.stats_persist_checks(**kwargs)
    assert any("tool == 'descriptive_stats'" in c.name and not c.ok for c in checks)


def test_stats_persist_checks_flags_raw_source():
    kwargs = _good_stats_kwargs()
    kwargs["source"] = "raw"
    checks = smoke.stats_persist_checks(**kwargs)
    assert any("consumed a cleaned source" in c.name and not c.ok for c in checks)


def test_stats_persist_checks_flags_missing_stats_csv():
    kwargs = _good_stats_kwargs()
    kwargs["output_keys"] = {}
    kwargs["output_sha256"] = {}
    checks = smoke.stats_persist_checks(**kwargs)
    assert any("committed outputs include" in c.name and not c.ok for c in checks)


def test_stats_result_checks_all_pass_on_valid_result():
    checks = smoke.stats_result_checks(19, 0)
    assert all(c.ok for c in checks), [c for c in checks if not c.ok]


def test_stats_result_checks_flags_zero_traits_reported():
    checks = smoke.stats_result_checks(0, 0)
    assert any("n_traits_reported > 0" in c.name and not c.ok for c in checks)


def test_stats_result_checks_flags_nonzero_failed():
    checks = smoke.stats_result_checks(18, 1)
    assert any("n_failed == 0" in c.name and not c.ok for c in checks)
