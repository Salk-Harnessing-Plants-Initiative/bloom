"""Unit tests for the #573 backend-sentinel audit script (task 5.6).

Loaded by path (bloommcp/scripts/ is not a package) and run over real,
on-disk manifests via `local_manifest_backend`, with sentinels hand-patched —
the only way to manufacture non-matching states (write_manifest always
re-stamps from the active backend).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from manifest_fixtures import write_cleaned_manifest

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "audit_backend_sentinels.py"
)
_spec = importlib.util.spec_from_file_location("audit_backend_sentinels", _SCRIPT_PATH)
assert _spec and _spec.loader
audit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit)


def _patch_sentinel(root: Path, catalog: str, value) -> None:
    path = root / "root" / "bloommcp_output" / catalog / "manifest.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    if value == "__absent__":
        raw.pop("storage_backend", None)
    else:
        raw["storage_backend"] = value
    path.write_text(json.dumps(raw), encoding="utf-8")


def test_scan_classifies_every_sentinel_state(local_manifest_backend):
    ts = "2026-07-06T00:00:00Z"
    write_cleaned_manifest(local_manifest_backend, "native", "qc", "v1", ts, b"a\n1\n")
    write_cleaned_manifest(local_manifest_backend, "foreign", "qc", "v1", ts, b"a\n1\n")
    write_cleaned_manifest(local_manifest_backend, "old", "qc", "v1", ts, b"a\n1\n")
    write_cleaned_manifest(local_manifest_backend, "blank", "qc", "v1", ts, b"a\n1\n")
    write_cleaned_manifest(local_manifest_backend, "weird", "qc", "v1", ts, b"a\n1\n")
    root = local_manifest_backend
    _patch_sentinel(root, "qc_foreign", "supabase")  # active is `local`
    _patch_sentinel(root, "qc_old", "__absent__")  # pre-#572
    _patch_sentinel(root, "qc_blank", "  ")
    _patch_sentinel(root, "qc_weird", "minio")

    report = audit.scan_backend_sentinels()

    assert report["active_backend"] == "local"
    assert report["catalogs_scanned"] == 5
    assert report["catalogs"]["matching"] == ["qc_native"]
    assert report["catalogs"]["foreign"] == ["qc_foreign"]
    assert sorted(report["catalogs"]["unstamped"]) == ["qc_blank", "qc_old"]
    assert report["catalogs"]["unrecognized"] == ["qc_weird"]
    assert report["errors"] == []


def test_run_exit_codes_are_three_valued(local_manifest_backend, capsys):
    """PR #782 review: a sweep that finds nothing foreign is not the same as a
    sweep that verified anything, so 'clean' and 'clean but blind' must be
    distinguishable exit codes — otherwise the day-one blind spot passes with
    nobody required to act on it."""
    ts = "2026-07-06T00:00:00Z"
    write_cleaned_manifest(local_manifest_backend, "native", "qc", "v1", ts, b"a\n1\n")

    # Fully verified: every catalog stamped and matching.
    assert audit.run([]) == 0

    # Blind spot: nothing foreign, but an unstamped catalog the guard can't
    # verify -> exit 3, and the summary names both numbers for the PR record.
    write_cleaned_manifest(local_manifest_backend, "old", "qc", "v1", ts, b"a\n1\n")
    _patch_sentinel(local_manifest_backend, "qc_old", "__absent__")
    assert audit.run([]) == 3
    out = capsys.readouterr()
    assert "1 unstamped" in out.out
    assert "foreign+unrecognized=0, unstamped=1" in out.out
    assert "BLIND SPOT" in out.err

    # ...acknowledged explicitly -> 0.
    assert audit.run(["--allow-unstamped"]) == 0

    # A catalog the guard would refuse outranks the blind spot -> 2.
    write_cleaned_manifest(local_manifest_backend, "bad", "qc", "v1", ts, b"a\n1\n")
    _patch_sentinel(local_manifest_backend, "qc_bad", "supabase")
    assert audit.run([]) == 2
    assert audit.run(["--allow-unstamped"]) == 2
    assert "FAIL" in capsys.readouterr().err


def test_classify_sentinel_mirrors_the_guard_predicate():
    assert audit.classify_sentinel(None, "supabase") == "unstamped"
    assert audit.classify_sentinel("", "supabase") == "unstamped"
    assert audit.classify_sentinel("  ", "supabase") == "unstamped"
    assert audit.classify_sentinel("SUPABASE", "supabase") == "matching"
    assert audit.classify_sentinel("local", "supabase") == "foreign"
    assert audit.classify_sentinel("minio", "supabase") == "unrecognized"
    assert audit.classify_sentinel(123, "supabase") == "unrecognized"


def test_sweep_refuses_to_certify_a_possibly_truncated_listing(
    local_manifest_backend, monkeypatch, capsys
):
    """#396 / PR #782 review round 3: `list_prefix` returns one unpaginated
    upstream page, and BOTH the catalog enumeration and each manifest probe
    ride it. A "0 foreign" verdict off a listing that may have been cut at the
    page cap is exactly the false assurance task 5.6 exists to prevent, so the
    sweep refuses to certify instead of reporting clean."""
    ts = "2026-07-06T00:00:00Z"
    write_cleaned_manifest(local_manifest_backend, "native", "qc", "v1", ts, b"a\n1\n")

    real_list = audit.list_prefix
    root = f"{audit._OUTPUT_ROOT}/"

    def _capped(prefix):
        names = real_list(prefix)
        if prefix == root:
            # Simulate the upstream page cap: a full page came back, so we
            # cannot know whether more catalogs exist beyond it.
            return (names + [f"qc_filler{i}" for i in range(200)])[
                : audit._SUSPECTED_PAGE_LIMIT
            ]
        return names

    monkeypatch.setattr(audit, "list_prefix", _capped)

    report = audit.scan_backend_sentinels()
    assert report["truncation_suspected"], "a full-page listing must be flagged"
    assert report["truncation_suspected"][0]["prefix"] == root
    assert report["counts"]["foreign"] == 0  # nothing foreign was actually seen…

    # …and yet the gate must NOT certify that as clean.
    assert audit.run([]) == 4
    err = capsys.readouterr().err
    assert "TRUNCATION SUSPECTED" in err
    assert "#854" in err  # points at the fix that must land first


def test_truncation_outranks_the_other_exit_codes(
    local_manifest_backend, monkeypatch, capsys
):
    """Even with a genuinely foreign catalog present, an untrustworthy
    inventory is the more fundamental problem: the operator must land #854 and
    re-run rather than act on counts drawn from a partial listing."""
    ts = "2026-07-06T00:00:00Z"
    write_cleaned_manifest(local_manifest_backend, "bad", "qc", "v1", ts, b"a\n1\n")
    _patch_sentinel(local_manifest_backend, "qc_bad", "supabase")

    real_list = audit.list_prefix

    def _capped(prefix):
        names = real_list(prefix)
        if prefix == f"{audit._OUTPUT_ROOT}/":
            return (names + [f"qc_f{i}" for i in range(200)])[
                : audit._SUSPECTED_PAGE_LIMIT
            ]
        return names

    monkeypatch.setattr(audit, "list_prefix", _capped)

    assert audit.run([]) == 4  # not 2
    assert audit.run(["--allow-unstamped"]) == 4  # and not maskable
    assert "REFUSING TO CERTIFY" in capsys.readouterr().err


def test_a_normal_small_sweep_still_certifies(local_manifest_backend, capsys):
    """The guard must not fire on ordinary inventories — otherwise 5.6 could
    never pass."""
    ts = "2026-07-06T00:00:00Z"
    write_cleaned_manifest(local_manifest_backend, "native", "qc", "v1", ts, b"a\n1\n")

    report = audit.scan_backend_sentinels()
    assert report["truncation_suspected"] == []
    assert audit.run([]) == 0
