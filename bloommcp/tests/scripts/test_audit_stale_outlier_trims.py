"""Unit tests for the stale-outlier-trims audit script's pure logic (bloom#585).

`bloommcp/scripts/` is not a package (mirrors `tests/smoke/live_persistence_smoke.py`,
loaded the same way by `test_live_persistence_smoke_logic.py`), so the module is
loaded by path. Manifests are real, on-disk objects via the `local_manifest_backend`
fixture (`conftest.py`) -- this script reads through `AnalysisDir`/the storage
backend directly, not through `FakeReader`/`FakeResultStore`.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from manifest_fixtures import append_cleaned_version, write_cleaned_manifest
from manifest_fixtures import is_root_listing, spy_backend_listings

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "audit_stale_outlier_trims.py"
)
_spec = importlib.util.spec_from_file_location(
    "audit_stale_outlier_trims", _SCRIPT_PATH
)
assert _spec and _spec.loader
audit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit)


def _hit_for(report: dict, stem: str) -> dict:
    matches = [h for h in report["hits"] if h["stem"] == stem]
    assert len(matches) == 1, f"expected exactly one hit for {stem!r}, got {matches}"
    return matches[0]


# --- (a) the actual pre-#420 hazard: qc_clean -> remove_outliers -> qc_clean ---
def test_reports_a_silently_superseded_trim(local_manifest_backend):
    write_cleaned_manifest(
        local_manifest_backend,
        "exp_a",
        "qc",
        "v1",
        "2026-01-01T00:00:00Z",
        b"a\n1\n",
        tool="qc_clean",
        based_on_version="raw",
    )
    append_cleaned_version(
        local_manifest_backend,
        "exp_a",
        "qc",
        "v2",
        "2026-01-01T00:00:01Z",
        b"trim\n1\n",
        tool="remove_outliers",
        based_on_version="v1_cleaned",
    )
    append_cleaned_version(
        local_manifest_backend,
        "exp_a",
        "qc",
        "v3",
        "2026-01-01T00:00:02Z",
        b"a\n1\n2\n",
        tool="qc_clean",
        based_on_version="raw",
    )

    report = audit.scan_for_stale_outlier_trims()

    hit = _hit_for(report, "exp_a")
    assert hit["superseded_entry_id"] == "v2"
    assert hit["superseded_entry_created_at"] == "2026-01-01T00:00:01Z"
    assert hit["current_latest_id"] == "v3"
    assert hit["current_latest_tool"] == "qc_clean"
    assert hit["current_latest_created_at"] == "2026-01-01T00:00:02Z"
    assert (
        hit["post_420_status"] == "not_remediated"
    )  # no outliers_<stem> manifest at all
    assert report["errors"] == []
    assert report["experiments_scanned"] == 1


# --- (b) issue #419's legitimate re-trim, no intervening plain clean ----------
def test_legitimate_re_trim_is_not_a_hit(local_manifest_backend):
    write_cleaned_manifest(
        local_manifest_backend,
        "exp_b",
        "qc",
        "v1",
        "2026-01-01T00:00:00Z",
        b"a\n1\n",
        tool="qc_clean",
        based_on_version="raw",
    )
    append_cleaned_version(
        local_manifest_backend,
        "exp_b",
        "qc",
        "v2",
        "2026-01-01T00:00:01Z",
        b"trim\n1\n",
        tool="remove_outliers",
        based_on_version="v1_cleaned",
    )
    append_cleaned_version(
        local_manifest_backend,
        "exp_b",
        "qc",
        "v3",
        "2026-01-01T00:00:02Z",
        b"trim\n2\n",
        tool="remove_outliers",
        based_on_version="v1_cleaned",
    )

    report = audit.scan_for_stale_outlier_trims()

    assert report["hits"] == []
    assert report["errors"] == []
    assert report["experiments_scanned"] == 1


# --- (b2) tie-break: names the MOST RECENT remove_outliers entry --------------
def test_hit_names_most_recently_committed_superseded_trim(local_manifest_backend):
    write_cleaned_manifest(
        local_manifest_backend,
        "exp_b2",
        "qc",
        "v1",
        "2026-01-01T00:00:00Z",
        b"a\n1\n",
        tool="qc_clean",
        based_on_version="raw",
    )
    append_cleaned_version(
        local_manifest_backend,
        "exp_b2",
        "qc",
        "v2",
        "2026-01-01T00:00:01Z",
        b"trim\n1\n",
        tool="remove_outliers",
        based_on_version="v1_cleaned",
    )
    append_cleaned_version(
        local_manifest_backend,
        "exp_b2",
        "qc",
        "v3",
        "2026-01-01T00:00:02Z",
        b"trim\n2\n",
        tool="remove_outliers",
        based_on_version="v1_cleaned",
    )
    append_cleaned_version(
        local_manifest_backend,
        "exp_b2",
        "qc",
        "v4",
        "2026-01-01T00:00:03Z",
        b"a\n1\n2\n",
        tool="qc_clean",
        based_on_version="raw",
    )

    report = audit.scan_for_stale_outlier_trims()

    hit = _hit_for(report, "exp_b2")
    assert hit["superseded_entry_id"] == "v3"  # not v2 -- the more recent one


# --- (b3) tie-break when two remove_outliers entries share a created_at ------
def test_hit_names_later_entry_on_a_same_second_created_at_tie(local_manifest_backend):
    """`created_at` is second-granularity; two `remove_outliers` commits within
    the same wall-clock second (a scripted backfill, or a rapid re-trim -- #419's
    own workflow) must not fall back to `max()`'s first-encountered-wins default,
    which would silently name the EARLIER entry as superseded."""
    write_cleaned_manifest(
        local_manifest_backend,
        "exp_b3",
        "qc",
        "v1",
        "2026-01-01T00:00:00Z",
        b"a\n1\n",
        tool="qc_clean",
        based_on_version="raw",
    )
    append_cleaned_version(
        local_manifest_backend,
        "exp_b3",
        "qc",
        "v2",
        "2026-01-01T00:00:01Z",  # identical to v3's timestamp
        b"trim\n1\n",
        tool="remove_outliers",
        based_on_version="v1_cleaned",
    )
    append_cleaned_version(
        local_manifest_backend,
        "exp_b3",
        "qc",
        "v3",
        "2026-01-01T00:00:01Z",  # same second as v2 -- v3 is still the later commit
        b"trim\n2\n",
        tool="remove_outliers",
        based_on_version="v1_cleaned",
    )
    append_cleaned_version(
        local_manifest_backend,
        "exp_b3",
        "qc",
        "v4",
        "2026-01-01T00:00:02Z",
        b"a\n1\n2\n",
        tool="qc_clean",
        based_on_version="raw",
    )

    report = audit.scan_for_stale_outlier_trims()

    hit = _hit_for(report, "exp_b3")
    assert (
        hit["superseded_entry_id"] == "v3"
    )  # the later-committed one, despite the tie


# --- (c) no remove_outliers history at all ------------------------------------
def test_manifest_with_only_qc_clean_history_is_not_a_hit(local_manifest_backend):
    write_cleaned_manifest(
        local_manifest_backend,
        "exp_c",
        "qc",
        "v1",
        "2026-01-01T00:00:00Z",
        b"a\n1\n",
        tool="qc_clean",
        based_on_version="raw",
    )
    append_cleaned_version(
        local_manifest_backend,
        "exp_c",
        "qc",
        "v2",
        "2026-01-01T00:00:01Z",
        b"a\n1\n2\n",
        tool="qc_clean",
        based_on_version="raw",
    )

    report = audit.scan_for_stale_outlier_trims()

    assert report["hits"] == []
    assert report["errors"] == []


# --- (d) malformed JSON alongside a valid, hit-producing manifest -------------
def test_malformed_manifest_does_not_abort_the_scan(local_manifest_backend):
    from bloom_mcp.supabase_client import upload_file

    bad = local_manifest_backend / "bad.json"
    bad.write_bytes(b"{not valid json")
    upload_file("bloommcp_output/qc_exp_bad/manifest.json", bad)

    write_cleaned_manifest(
        local_manifest_backend,
        "exp_good",
        "qc",
        "v1",
        "2026-01-01T00:00:00Z",
        b"a\n1\n",
        tool="qc_clean",
        based_on_version="raw",
    )
    append_cleaned_version(
        local_manifest_backend,
        "exp_good",
        "qc",
        "v2",
        "2026-01-01T00:00:01Z",
        b"trim\n1\n",
        tool="remove_outliers",
        based_on_version="v1_cleaned",
    )
    append_cleaned_version(
        local_manifest_backend,
        "exp_good",
        "qc",
        "v3",
        "2026-01-01T00:00:02Z",
        b"a\n1\n2\n",
        tool="qc_clean",
        based_on_version="raw",
    )

    report = audit.scan_for_stale_outlier_trims()

    _hit_for(report, "exp_good")  # still reported despite the other stem's corruption
    bad_errors = [e for e in report["errors"] if e["stem"] == "exp_bad"]
    assert len(bad_errors) == 1
    assert report["experiments_scanned"] == 2


# --- (d2) a qc_<stem> prefix with no manifest.json at all ---------------------
def test_manifest_less_prefix_is_skipped_not_reported(local_manifest_backend):
    from bloom_mcp.supabase_client import upload_file

    csv = local_manifest_backend / "legacy.csv"
    csv.write_bytes(b"a,b\n1,2\n")
    upload_file("bloommcp_output/qc_exp_legacy/exp_legacy_cleaned.csv", csv)

    report = audit.scan_for_stale_outlier_trims()

    assert report["hits"] == []
    assert report["errors"] == []
    assert report["experiments_scanned"] == 1


# --- (d3) schema-valid manifest whose `latest` is None ------------------------
def test_manifest_with_no_latest_pointer_is_not_a_hit(local_manifest_backend):
    from bloom_mcp.manifest import (
        ExperimentBlock,
        Manifest,
        VersionEntry,
        get_code_versions,
        write_manifest,
    )

    entry = VersionEntry(
        id="v1",
        created_at="2026-01-01T00:00:00Z",
        tool="remove_outliers",
        params={},
        based_on_version="v1_cleaned",
        code_versions=get_code_versions(),
        outputs={"_cleaned.csv": "_cleaned.csv"},
        version_dir="v1_2026-01-01",
    )
    manifest = Manifest(
        experiment=ExperimentBlock(
            filename="exp_nolatest.csv", source_path="", input_sha256=""
        ),
        versions=[entry],
        latest=None,
    )
    write_manifest("bloommcp_output/qc_exp_nolatest/", manifest)

    report = audit.scan_for_stale_outlier_trims()

    assert report["hits"] == []
    assert report["errors"] == []


# --- dangling manifest.latest pointer (no matching VersionEntry) -------------
def test_dangling_latest_pointer_is_reported_as_an_error_not_a_crash(
    local_manifest_backend,
):
    from bloom_mcp.manifest import (
        ExperimentBlock,
        Manifest,
        VersionEntry,
        get_code_versions,
        write_manifest,
    )

    entry = VersionEntry(
        id="v1",
        created_at="2026-01-01T00:00:00Z",
        tool="qc_clean",
        params={},
        based_on_version="raw",
        code_versions=get_code_versions(),
        outputs={"_cleaned.csv": "_cleaned.csv"},
        version_dir="v1_2026-01-01",
    )
    manifest = Manifest(
        experiment=ExperimentBlock(
            filename="exp_dangling.csv", source_path="", input_sha256=""
        ),
        versions=[entry],
        latest="v99",  # does not exist in `versions`
    )
    write_manifest("bloommcp_output/qc_exp_dangling/", manifest)

    report = audit.scan_for_stale_outlier_trims()

    assert report["hits"] == []
    dangling_errors = [e for e in report["errors"] if e["stem"] == "exp_dangling"]
    assert len(dangling_errors) == 1
    assert "v99" in dangling_errors[0]["error"]
    assert report["experiments_scanned"] == 1


# --- post_420_status: has a hit already been remediated by a later, real trim? -
def test_post_420_status_not_remediated_when_no_outliers_manifest_exists(
    local_manifest_backend,
):
    write_cleaned_manifest(
        local_manifest_backend,
        "exp_unremediated",
        "qc",
        "v1",
        "2026-01-01T00:00:00Z",
        b"a\n1\n",
        tool="qc_clean",
        based_on_version="raw",
    )
    append_cleaned_version(
        local_manifest_backend,
        "exp_unremediated",
        "qc",
        "v2",
        "2026-01-01T00:00:01Z",
        b"trim\n1\n",
        tool="remove_outliers",
        based_on_version="v1_cleaned",
    )
    append_cleaned_version(
        local_manifest_backend,
        "exp_unremediated",
        "qc",
        "v3",
        "2026-01-01T00:00:02Z",
        b"a\n1\n2\n",
        tool="qc_clean",
        based_on_version="raw",
    )

    hit = _hit_for(audit.scan_for_stale_outlier_trims(), "exp_unremediated")
    assert hit["post_420_status"] == "not_remediated"


def test_post_420_status_remediated_and_current_after_a_fresh_post_420_trim(
    local_manifest_backend,
):
    # The legacy hit (pre-#420 pattern) ...
    write_cleaned_manifest(
        local_manifest_backend,
        "exp_fixed",
        "qc",
        "v1",
        "2026-01-01T00:00:00Z",
        b"a\n1\n",
        tool="qc_clean",
        based_on_version="raw",
    )
    append_cleaned_version(
        local_manifest_backend,
        "exp_fixed",
        "qc",
        "v2",
        "2026-01-01T00:00:01Z",
        b"trim\n1\n",
        tool="remove_outliers",
        based_on_version="v1_cleaned",
    )
    append_cleaned_version(
        local_manifest_backend,
        "exp_fixed",
        "qc",
        "v3",
        "2026-01-01T00:00:02Z",
        b"a\n1\n2\n",
        tool="qc_clean",
        based_on_version="raw",
    )
    # ... plus a post-#420-style trim, committed to the SEPARATE outliers_<stem>
    # manifest, based on the current qc latest (v3) -- fully remediated.
    write_cleaned_manifest(
        local_manifest_backend,
        "exp_fixed",
        "outliers",
        "v1",
        "2026-01-01T00:00:03Z",
        b"trim\n2\n",
        tool="remove_outliers",
        based_on_version="v3_cleaned",
    )

    hit = _hit_for(audit.scan_for_stale_outlier_trims(), "exp_fixed")
    assert hit["post_420_status"] == "remediated_and_current"


def test_post_420_status_remediated_but_stale_again(local_manifest_backend):
    write_cleaned_manifest(
        local_manifest_backend,
        "exp_restale",
        "qc",
        "v1",
        "2026-01-01T00:00:00Z",
        b"a\n1\n",
        tool="qc_clean",
        based_on_version="raw",
    )
    append_cleaned_version(
        local_manifest_backend,
        "exp_restale",
        "qc",
        "v2",
        "2026-01-01T00:00:01Z",
        b"trim\n1\n",
        tool="remove_outliers",
        based_on_version="v1_cleaned",
    )
    append_cleaned_version(
        local_manifest_backend,
        "exp_restale",
        "qc",
        "v3",
        "2026-01-01T00:00:02Z",
        b"a\n1\n2\n",
        tool="qc_clean",
        based_on_version="raw",
    )
    # A post-#420 trim was made against v3 ...
    write_cleaned_manifest(
        local_manifest_backend,
        "exp_restale",
        "outliers",
        "v1",
        "2026-01-01T00:00:03Z",
        b"trim\n2\n",
        tool="remove_outliers",
        based_on_version="v3_cleaned",
    )
    # ... but a fourth qc_clean ran after that, staling the post-#420 trim too.
    append_cleaned_version(
        local_manifest_backend,
        "exp_restale",
        "qc",
        "v4",
        "2026-01-01T00:00:04Z",
        b"a\n1\n2\n3\n",
        tool="qc_clean",
        based_on_version="raw",
    )

    hit = _hit_for(audit.scan_for_stale_outlier_trims(), "exp_restale")
    assert hit["post_420_status"] == "remediated_but_stale_again"


# --- (e) empty bucket ----------------------------------------------------------
def test_empty_bucket_produces_empty_successful_report(local_manifest_backend):
    report = audit.scan_for_stale_outlier_trims()

    assert report == {"hits": [], "errors": [], "experiments_scanned": 0}


# --- (f) enumeration itself fails ----------------------------------------------
def test_enumeration_failure_propagates(local_manifest_backend, monkeypatch):
    def _boom(_prefix):
        raise RuntimeError("storage backend unreachable")

    monkeypatch.setattr(audit, "list_prefix", _boom)

    try:
        audit.scan_for_stale_outlier_trims()
    except RuntimeError as exc:
        assert "unreachable" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("scan_for_stale_outlier_trims should have raised")


# --- run()'s exit-code contract -------------------------------------------------
def test_run_returns_zero_and_writes_report_on_success(local_manifest_backend, capsys):
    write_cleaned_manifest(
        local_manifest_backend,
        "exp_a",
        "qc",
        "v1",
        "2026-01-01T00:00:00Z",
        b"a\n1\n",
        tool="qc_clean",
        based_on_version="raw",
    )
    append_cleaned_version(
        local_manifest_backend,
        "exp_a",
        "qc",
        "v2",
        "2026-01-01T00:00:01Z",
        b"trim\n1\n",
        tool="remove_outliers",
        based_on_version="v1_cleaned",
    )
    append_cleaned_version(
        local_manifest_backend,
        "exp_a",
        "qc",
        "v3",
        "2026-01-01T00:00:02Z",
        b"a\n1\n2\n",
        tool="qc_clean",
        based_on_version="raw",
    )

    from bloom_mcp.supabase_client import read_json

    code = audit.run()

    assert code == 0
    printed = capsys.readouterr().out
    assert "report written to" in printed

    keys = [k for k in _list_all_keys_under_report_prefix(local_manifest_backend)]
    assert len(keys) == 1
    written = read_json(keys[0])
    assert written["experiments_scanned"] == 1
    assert len(written["hits"]) == 1
    assert written["errors"] == []
    assert "scanned_at" in written and written["scanned_at"].endswith("Z")
    assert written["storage_backend"] == "local"
    assert written["scope_note"] == audit.SCOPE_NOTE
    assert "current-state" in written["scope_note"].lower()


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
    write_cleaned_manifest(
        local_manifest_backend,
        "exp_a",
        "qc",
        "v1",
        "2026-01-01T00:00:00Z",
        b"a\n1\n",
        tool="qc_clean",
        based_on_version="raw",
    )
    append_cleaned_version(
        local_manifest_backend,
        "exp_a",
        "qc",
        "v2",
        "2026-01-01T00:00:01Z",
        b"trim\n1\n",
        tool="remove_outliers",
        based_on_version="v1_cleaned",
    )
    append_cleaned_version(
        local_manifest_backend,
        "exp_a",
        "qc",
        "v3",
        "2026-01-01T00:00:02Z",
        b"a\n1\n2\n",
        tool="qc_clean",
        based_on_version="raw",
    )
    bad = local_manifest_backend / "bad.json"
    bad.write_bytes(b"{not valid json")
    upload_file("bloommcp_output/qc_exp_bad/manifest.json", bad)

    import bloom_mcp.manifest as manifest_pkg
    import bloom_mcp.manifest.manifest as manifest_mod
    import bloom_mcp.supabase_client as sc

    def _forbidden(*_a, **_k):
        raise AssertionError("scan_for_stale_outlier_trims must never write/upload")

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

        report = audit.scan_for_stale_outlier_trims()
        assert len(report["hits"]) == 1
        assert len(report["errors"]) == 1
    finally:
        for mod, name, orig in originals:
            setattr(mod, name, orig)


# --- experiment-scoped sweeps (bloom#919) --------------------------------------
def _seed_superseded_trim(local_manifest_backend: Path, stem: str) -> None:
    """qc_clean -> remove_outliers -> qc_clean under `qc_<stem>`: a hit."""
    for version_id, created_at, content, tool, based_on in (
        ("v1", "2026-01-01T00:00:00Z", b"a\n1\n", "qc_clean", "raw"),
        ("v2", "2026-01-01T00:00:01Z", b"trim\n1\n", "remove_outliers", "v1_cleaned"),
        ("v3", "2026-01-01T00:00:02Z", b"a\n1\n2\n", "qc_clean", "raw"),
    ):
        seed = write_cleaned_manifest if version_id == "v1" else append_cleaned_version
        seed(
            local_manifest_backend,
            stem,
            "qc",
            version_id,
            created_at,
            content,
            tool=tool,
            based_on_version=based_on,
        )


def test_scoped_scan_never_lists_the_shared_root(local_manifest_backend, monkeypatch):
    _seed_superseded_trim(local_manifest_backend, "exp_a")
    write_cleaned_manifest(
        local_manifest_backend,
        "exp_a",
        "outliers",
        "v1",
        "2026-01-01T00:00:03Z",
        b"trim\n2\n",
        tool="remove_outliers",
        based_on_version="v3_cleaned",
    )
    _seed_superseded_trim(local_manifest_backend, "exp_b")
    calls = spy_backend_listings(monkeypatch)

    report = audit.scan_for_stale_outlier_trims(experiments=["exp_a.csv"])

    assert calls, "expected per-experiment listings"
    assert not [p for p in calls if is_root_listing(p)]
    assert all(
        p.startswith(("bloommcp_output/qc_exp_a/", "bloommcp_output/outliers_exp_a/"))
        for p in calls
    ), calls
    assert report["experiments_scanned"] == 1
    assert [h["stem"] for h in report["hits"]] == ["exp_a"]
    assert _hit_for(report, "exp_a")["post_420_status"] == "remediated_and_current"


def test_scoped_scan_does_not_use_the_scripts_root_enumeration(
    local_manifest_backend, monkeypatch
):
    _seed_superseded_trim(local_manifest_backend, "exp_a")

    def _boom(_prefix):
        raise RuntimeError("root enumeration must not run in a scoped scan")

    monkeypatch.setattr(audit, "list_prefix", _boom)

    report = audit.scan_for_stale_outlier_trims(experiments=["exp_a"])

    _hit_for(report, "exp_a")


def test_scoped_scan_matches_the_full_sweep_for_hits_and_errors(
    local_manifest_backend,
):
    from bloom_mcp.supabase_client import upload_file

    _seed_superseded_trim(local_manifest_backend, "exp_a")
    _seed_superseded_trim(local_manifest_backend, "exp_b")
    bad = local_manifest_backend / "bad.json"
    bad.write_bytes(b"{not valid json")
    upload_file("bloommcp_output/qc_exp_bad/manifest.json", bad)

    full = audit.scan_for_stale_outlier_trims()
    scoped = audit.scan_for_stale_outlier_trims(experiments=["exp_a.csv", "exp_bad"])

    assert _hit_for(scoped, "exp_a") == _hit_for(full, "exp_a")
    assert [h["stem"] for h in scoped["hits"]] == ["exp_a"]
    assert scoped["errors"] == [e for e in full["errors"] if e["stem"] == "exp_bad"]
    assert len(scoped["errors"]) == 1


def test_scoped_duplicates_collapse_to_one_experiment(local_manifest_backend):
    _seed_superseded_trim(local_manifest_backend, "exp_a")

    report = audit.scan_for_stale_outlier_trims(experiments=["exp_a.csv", "exp_a"])

    assert report["experiments_scanned"] == 1
    assert len(report["hits"]) == 1


def test_dotted_report_stem_round_trips_via_dot_csv(local_manifest_backend):
    _seed_superseded_trim(local_manifest_backend, "exp.v2")

    full_hit = _hit_for(audit.scan_for_stale_outlier_trims(), "exp.v2")
    scoped = audit.scan_for_stale_outlier_trims(experiments=["exp.v2.csv"])

    assert _hit_for(scoped, "exp.v2") == full_hit


def test_scoped_scan_discloses_unevaluated_experiments(local_manifest_backend):
    from bloom_mcp.manifest import (
        ExperimentBlock,
        Manifest,
        VersionEntry,
        get_code_versions,
        write_manifest,
    )

    entry = VersionEntry(
        id="v1",
        created_at="2026-01-01T00:00:00Z",
        tool="qc_clean",
        params={},
        based_on_version="raw",
        code_versions=get_code_versions(),
        outputs={"_cleaned.csv": "_cleaned.csv"},
        version_dir="v1_2026-01-01",
    )
    write_manifest(
        "bloommcp_output/qc_exp_nolatest/",
        Manifest(
            experiment=ExperimentBlock(
                filename="exp_nolatest.csv", source_path="", input_sha256=""
            ),
            versions=[entry],
            latest=None,
        ),
    )

    report = audit.scan_for_stale_outlier_trims(
        experiments=["exp_missing.csv", "exp_nolatest.csv"]
    )

    assert report["unevaluated"] == [
        {"stem": "exp_missing", "reason": "no_manifest"},
        {"stem": "exp_nolatest", "reason": "no_latest"},
    ]
    assert report["hits"] == []
    assert report["errors"] == []
    assert report["experiments_scanned"] == 2


def test_full_sweep_result_has_no_unevaluated_key(local_manifest_backend):
    _seed_superseded_trim(local_manifest_backend, "exp_a")

    assert "unevaluated" not in audit.scan_for_stale_outlier_trims()


def test_scoped_run_persists_its_experiment_scope(local_manifest_backend, capsys):
    from bloom_mcp.supabase_client import read_json

    _seed_superseded_trim(local_manifest_backend, "exp_a")

    code = audit.run(["--experiment", "exp_a.csv"])

    assert code == 0
    assert "scoped to 1 experiment(s): " in capsys.readouterr().out
    (key,) = _list_all_keys_under_report_prefix(local_manifest_backend)
    written = read_json(key)
    assert written["experiment_scope"] == {
        "mode": "experiments",
        "experiments": [{"requested": "exp_a.csv", "stem": "exp_a"}],
    }
    assert written["unevaluated"] == []
    assert written["experiments_scanned"] == 1


def test_full_sweep_run_persists_mode_all_and_is_not_labelled_scoped(
    local_manifest_backend, capsys
):
    from bloom_mcp.supabase_client import read_json

    _seed_superseded_trim(local_manifest_backend, "exp_a")

    assert audit.run() == 0

    assert "scoped to" not in capsys.readouterr().out
    (key,) = _list_all_keys_under_report_prefix(local_manifest_backend)
    written = read_json(key)
    assert written["experiment_scope"] == {"mode": "all"}
    assert "unevaluated" not in written


def test_run_ignores_the_host_processs_argv(local_manifest_backend, monkeypatch):
    import sys

    _seed_superseded_trim(local_manifest_backend, "exp_a")
    monkeypatch.setattr(sys, "argv", ["pytest", "-q", "tests/scripts/"])

    assert audit.run() == 0


@pytest.mark.parametrize("value", ["../x.csv", "a/b.csv", "a\\b.csv", ""])
def test_run_rejects_prefix_escaping_values_before_any_storage_access(
    local_manifest_backend, monkeypatch, capsys, value
):
    calls = spy_backend_listings(monkeypatch)

    code = audit.run(["--experiment", value])

    assert code == 2
    assert "experiment identifier" in capsys.readouterr().err
    assert calls == []
    assert _list_all_keys_under_report_prefix(local_manifest_backend) == []


def test_run_rejects_unknown_flags(local_manifest_backend):
    with pytest.raises(SystemExit) as exc:
        audit.run(["--bogus"])

    assert exc.value.code == 2
    assert _list_all_keys_under_report_prefix(local_manifest_backend) == []


def test_scoped_run_where_every_experiment_errors_is_a_loud_failure(
    local_manifest_backend, monkeypatch, capsys
):
    import bloom_mcp.storage_backend as sb

    _seed_superseded_trim(local_manifest_backend, "exp_a")
    backend = sb.active_backend()
    original = backend.list_prefix

    def _unreachable(prefix):
        if prefix.startswith("bloommcp_output/qc_"):
            raise RuntimeError("storage backend unreachable")
        return original(prefix)

    monkeypatch.setattr(backend, "list_prefix", _unreachable)

    code = audit.run(["--experiment", "exp_a.csv", "--experiment", "exp_b.csv"])

    assert code == 1
    assert "unreachable" in capsys.readouterr().err
    assert _list_all_keys_under_report_prefix(local_manifest_backend) == []


def test_scoped_scan_never_writes_or_uploads(local_manifest_backend, monkeypatch):
    import bloom_mcp.manifest as manifest_pkg
    import bloom_mcp.manifest.manifest as manifest_mod
    import bloom_mcp.supabase_client as sc

    _seed_superseded_trim(local_manifest_backend, "exp_a")

    def _forbidden(*_a, **_k):
        raise AssertionError("scan_for_stale_outlier_trims must never write/upload")

    for mod, name in [
        (manifest_mod, "write_manifest"),
        (manifest_pkg, "write_manifest"),
        (manifest_mod, "write_json"),
        (sc, "write_json"),
        (sc, "upload_file"),
        (sc, "delete_files"),
    ]:
        monkeypatch.setattr(mod, name, _forbidden)

    report = audit.scan_for_stale_outlier_trims(experiments=["exp_a.csv", "nope"])

    assert len(report["hits"]) == 1
