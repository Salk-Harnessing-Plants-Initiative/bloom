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


def test_run_exit_codes_gate_on_refused_catalogs(local_manifest_backend, capsys):
    ts = "2026-07-06T00:00:00Z"
    write_cleaned_manifest(local_manifest_backend, "native", "qc", "v1", ts, b"a\n1\n")
    write_cleaned_manifest(local_manifest_backend, "old", "qc", "v1", ts, b"a\n1\n")
    _patch_sentinel(local_manifest_backend, "qc_old", "__absent__")

    # matching + unstamped only -> clean gate (0), and the unstamped count —
    # the guard's blind spot the review asked to report — is in the summary.
    assert audit.run() == 0
    out = capsys.readouterr().out
    assert "1 unstamped" in out

    write_cleaned_manifest(local_manifest_backend, "bad", "qc", "v1", ts, b"a\n1\n")
    _patch_sentinel(local_manifest_backend, "qc_bad", "supabase")
    assert audit.run() == 2  # a catalog the guard would refuse -> gate fails


def test_classify_sentinel_mirrors_the_guard_predicate():
    assert audit.classify_sentinel(None, "supabase") == "unstamped"
    assert audit.classify_sentinel("", "supabase") == "unstamped"
    assert audit.classify_sentinel("  ", "supabase") == "unstamped"
    assert audit.classify_sentinel("SUPABASE", "supabase") == "matching"
    assert audit.classify_sentinel("local", "supabase") == "foreign"
    assert audit.classify_sentinel("minio", "supabase") == "unrecognized"
    assert audit.classify_sentinel(123, "supabase") == "unrecognized"
