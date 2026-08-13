"""Tests for the object-storage backend seam (bloommcp-storage-backend).

Covers backend selection (`BLOOM_STORAGE_BACKEND`), the local filesystem backend
(key→path mapping, listing, escape guard, overwrite, verbatim bytes, atomic
writes, redacted errors), root resolution + boot-time validation, and
parity/integrity (byte-identical manifest, hash-equality on disk, the
default-writes-no-local-files guard, and legacy-fallback disjointness). No live
Supabase. (The `run_qc_workflow` local round-trip test was removed when the
Phase-1 workflow tools were retired — devendor-bloommcp-analysis C6.1 — its
`local_workflow_env` fixture had no other consumer and was removed with it.)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

from bloom_mcp import storage_backend as sb
from manifest_fixtures import write_cleaned_manifest, write_invalid_schema_manifest


@pytest.fixture(autouse=True)
def _reset_backend():
    """Isolate the memoized backend so env changes take effect per-test."""
    sb.reset_backend_for_tests()
    yield
    sb.reset_backend_for_tests()


def _seed_file(tmp_path: Path, data: bytes = b"x") -> Path:
    p = tmp_path / "seed.bin"
    p.write_bytes(data)
    return p


# ─── 1. Backend interface + selection ─────────────────────────────────────────


def test_default_selects_supabase(monkeypatch):
    monkeypatch.delenv("BLOOM_STORAGE_BACKEND", raising=False)
    sb.reset_backend_for_tests()
    assert isinstance(sb.active_backend(), sb.SupabaseStorageBackend)


def test_explicit_supabase_selects_supabase(monkeypatch):
    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "supabase")
    sb.reset_backend_for_tests()
    assert isinstance(sb.active_backend(), sb.SupabaseStorageBackend)


def test_local_selects_local(monkeypatch, tmp_path):
    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "local")
    monkeypatch.setenv("BLOOM_STORAGE_LOCAL_ROOT", str(tmp_path))
    sb.reset_backend_for_tests()
    assert isinstance(sb.active_backend(), sb.LocalStorageBackend)


def test_invalid_backend_raises_naming_value_and_valid_set(monkeypatch):
    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "locel")
    sb.reset_backend_for_tests()
    with pytest.raises(RuntimeError) as exc:
        sb.active_backend()
    assert "locel" in str(exc.value)
    assert "supabase" in str(exc.value) and "local" in str(exc.value)


def test_selection_reexamines_env_across_values(monkeypatch, tmp_path):
    """Reset seam lets one session exercise supabase → local without stale memo."""
    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "supabase")
    sb.reset_backend_for_tests()
    assert isinstance(sb.active_backend(), sb.SupabaseStorageBackend)
    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "local")
    monkeypatch.setenv("BLOOM_STORAGE_LOCAL_ROOT", str(tmp_path))
    sb.reset_backend_for_tests()
    assert isinstance(sb.active_backend(), sb.LocalStorageBackend)


def test_import_does_not_resolve_backend(monkeypatch):
    """Resolution is lazy: an invalid value does not blow up until first use."""
    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "locel")
    sb.reset_backend_for_tests()
    assert sb._active is None  # nothing resolved yet
    with pytest.raises(RuntimeError, match="locel"):
        sb.active_backend()


def test_server_import_is_pure_with_invalid_backend():
    """A fresh interpreter imports bloom_mcp.server with NO bloom env and an
    invalid BLOOM_STORAGE_BACKEND — proving selection is never resolved at import."""
    bloom_vars = (
        "SUPABASE_URL",
        "BLOOM_AGENT_KEY",
        "BLOOM_TRAITS_DIR",
        "BLOOM_OUTPUT_DIR",
        "BLOOM_PLOTS_DIR",
        "BLOOM_PLOTS_URL",
        "BLOOM_STORAGE_LOCAL_ROOT",
    )
    env = {k: v for k, v in os.environ.items() if k not in bloom_vars}
    env["BLOOM_STORAGE_BACKEND"] = "locel"
    result = subprocess.run(
        [sys.executable, "-c", "import bloom_mcp.server"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


# ─── 2. Local filesystem backend ──────────────────────────────────────────────


def _local(tmp_path: Path) -> sb.LocalStorageBackend:
    return sb.LocalStorageBackend(tmp_path)


def test_upload_download_roundtrip_by_key(tmp_path):
    b = _local(tmp_path)
    src = _seed_file(tmp_path, b"a,b\n1,2\n")
    b.upload_file("bloommcp_output/qc_x/v1/_cleaned.csv", src)
    on_disk = tmp_path / "bloommcp_output" / "qc_x" / "v1" / "_cleaned.csv"
    assert on_disk.read_bytes() == b"a,b\n1,2\n"

    dest = tmp_path / "out" / "got.csv"
    b.download_file("bloommcp_output/qc_x/v1/_cleaned.csv", dest)
    assert dest.read_bytes() == b"a,b\n1,2\n"


def test_write_read_json_roundtrip(tmp_path):
    b = _local(tmp_path)
    b.write_json("bloommcp_output/qc_x/manifest.json", {"b": 2, "a": 1})
    assert b.read_json("bloommcp_output/qc_x/manifest.json") == {"a": 1, "b": 2}


def test_verbatim_bytes_no_newline_translation(tmp_path):
    b = _local(tmp_path)
    payload = b"line1\r\nline2\nline3"  # mixed CRLF/LF must survive intact
    src = _seed_file(tmp_path, payload)
    b.upload_file("bloommcp_output/x/v1/f.bin", src)
    assert (tmp_path / "bloommcp_output/x/v1/f.bin").read_bytes() == payload
    dest = tmp_path / "d.bin"
    b.download_file("bloommcp_output/x/v1/f.bin", dest)
    assert dest.read_bytes() == payload


def test_writes_overwrite_in_place(tmp_path):
    b = _local(tmp_path)
    b.write_json("k/m.json", {"v": 1})
    b.write_json("k/m.json", {"v": 2})  # second write wins
    assert b.read_json("k/m.json") == {"v": 2}
    # idempotent parent-dir creation: second upload into an existing dir is fine
    src = _seed_file(tmp_path)
    b.upload_file("k/a.bin", src)
    b.upload_file("k/a.bin", src)


def test_list_prefix_returns_bare_children(tmp_path):
    b = _local(tmp_path)
    (tmp_path / "bloommcp_output" / "qc_x" / "v1_2026").mkdir(parents=True)
    (tmp_path / "bloommcp_output" / "qc_x" / "manifest.json").write_bytes(b"{}")
    names = b.list_prefix("bloommcp_output/qc_x/")
    assert set(names) == {"v1_2026", "manifest.json"}
    # bare names: no trailing slash, no path prefix — both caller checks work
    assert all("/" not in n and not n.endswith("/") for n in names)
    assert "manifest.json" in names  # manifest membership check
    assert any(n.startswith("v1_") for n in names)  # version-dir startswith check


def test_list_prefix_root_and_missing(tmp_path):
    b = _local(tmp_path)
    (tmp_path / "a").mkdir()
    (tmp_path / "b.txt").write_bytes(b"x")
    assert set(b.list_prefix("")) == {"a", "b.txt"}  # root listing
    assert b.list_prefix("does/not/exist/") == []  # missing → [], not error


def test_missing_key_raises_redacted(tmp_path):
    b = _local(tmp_path)
    with pytest.raises(FileNotFoundError) as exc:
        b.download_file("bloommcp_output/x/missing.csv", tmp_path / "o")
    msg = str(exc.value)
    assert str(tmp_path) not in msg  # no absolute host path leaked
    assert "bloommcp_output/x/missing.csv" in msg  # only the logical key
    # read_json arm is redacted the same way as download_file
    with pytest.raises(FileNotFoundError) as exc2:
        b.read_json("bloommcp_output/x/missing.json")
    msg2 = str(exc2.value)
    assert str(tmp_path) not in msg2
    assert "bloommcp_output/x/missing.json" in msg2


def test_empty_file_roundtrip(tmp_path):
    b = _local(tmp_path)
    src = _seed_file(tmp_path, b"")
    b.upload_file("bloommcp_output/x/v1/empty.csv", src)
    assert (tmp_path / "bloommcp_output/x/v1/empty.csv").read_bytes() == b""
    dest = tmp_path / "d.csv"
    b.download_file("bloommcp_output/x/v1/empty.csv", dest)
    assert dest.read_bytes() == b""


def test_unicode_key_and_payload_roundtrip(tmp_path):
    b = _local(tmp_path)
    key = "bloommcp_output/qc_café/v1/manifest.json"
    b.write_json(key, {"trait": "primär-läng"})
    assert (tmp_path / "bloommcp_output" / "qc_café" / "v1" / "manifest.json").is_file()
    assert b.read_json(key) == {"trait": "primär-läng"}


@pytest.mark.parametrize(
    "bad_key",
    ["../../etc/passwd", "/etc/passwd", "a/../../b", "a\\b", "..", "", "a/./b"],
)
def test_escape_guard_rejects_bad_keys(tmp_path, bad_key):
    b = _local(tmp_path)
    src = _seed_file(tmp_path)
    before = sorted(p.name for p in tmp_path.iterdir())
    with pytest.raises(ValueError):
        b.upload_file(bad_key, src)
    # the rejected key performs no I/O: nothing new written under the root
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_escape_guard_rejects_symlink(tmp_path):
    b = _local(tmp_path)
    outside = tmp_path.parent / "sb_outside"
    outside.mkdir(exist_ok=True)
    link = tmp_path / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this platform/privilege")
    with pytest.raises(ValueError):
        b.upload_file("link/evil.txt", _seed_file(tmp_path))


def test_atomic_write_temp_is_colocated_with_target(tmp_path, monkeypatch):
    b = _local(tmp_path)
    captured: dict = {}
    real_mkstemp = sb.tempfile.mkstemp

    def spy(*a, **k):
        captured["dir"] = k.get("dir")
        return real_mkstemp(*a, **k)

    monkeypatch.setattr(sb.tempfile, "mkstemp", spy)
    b.write_json("bloommcp_output/qc_x/manifest.json", {"v": 1})
    target = tmp_path / "bloommcp_output" / "qc_x" / "manifest.json"
    # temp lives in the target's dir → same filesystem → os.replace is atomic
    assert captured["dir"] == str(target.parent)


def test_interrupted_write_leaves_prior_content_intact(tmp_path, monkeypatch):
    b = _local(tmp_path)
    key = "bloommcp_output/qc_x/manifest.json"
    b.write_json(key, {"v": 1})
    target = tmp_path / "bloommcp_output" / "qc_x" / "manifest.json"
    original = target.read_bytes()

    def boom(_src, _dst):
        raise OSError("disk full")

    monkeypatch.setattr(sb.os, "replace", boom)
    with pytest.raises(OSError):
        b.write_json(key, {"v": 2})
    # never truncated: the whole prior content survives
    assert target.read_bytes() == original
    # and no orphaned temp files remain
    leftovers = [
        p.name for p in target.parent.iterdir() if p.name.startswith(sb._TMP_PREFIX)
    ]
    assert leftovers == []


def test_write_permission_error_is_redacted(tmp_path, monkeypatch):
    """A permission/OS error during the atomic write surfaces to the caller with
    NO absolute host path — only the logical key (spec: errors do not leak host
    paths). The raw path is logged server-side, not raised."""
    b = _local(tmp_path)
    key = "bloommcp_output/qc_x/manifest.json"
    leaked = str(tmp_path / "bloommcp_output" / "qc_x" / ".tmp-abc")

    def boom(*a, **k):
        raise PermissionError(13, "Permission denied", leaked)

    monkeypatch.setattr(sb.tempfile, "mkstemp", boom)
    with pytest.raises(OSError) as exc:
        b.write_json(key, {"v": 1})
    msg = str(exc.value)
    assert str(tmp_path) not in msg  # no absolute host path leaked
    assert leaked not in msg
    assert key in msg  # only the logical storage key
    assert "permission" in msg.lower()


def test_read_permission_error_is_redacted(tmp_path, monkeypatch):
    """The read path redacts a permission/OS error the same way — logical key
    only, no host path (distinct from the not-found redaction)."""
    b = _local(tmp_path)
    key = "bloommcp_output/x/f.csv"
    on_disk = tmp_path / "bloommcp_output" / "x" / "f.csv"
    on_disk.parent.mkdir(parents=True)
    on_disk.write_bytes(b"data")  # exists, so we pass is_file() and hit read_bytes

    def boom(self, *a, **k):
        raise PermissionError(13, "Permission denied", str(on_disk))

    monkeypatch.setattr(sb.Path, "read_bytes", boom)
    with pytest.raises(OSError) as exc:
        b.download_file(key, tmp_path / "dest")
    msg = str(exc.value)
    assert str(tmp_path) not in msg
    assert key in msg


def test_local_delete_files_removes_existing_and_ignores_missing(tmp_path):
    b = _local(tmp_path)
    src = _seed_file(tmp_path)
    b.upload_file("bloommcp_output/qc_x/v1/a.csv", src)
    b.upload_file("bloommcp_output/qc_x/v1/b.csv", src)
    assert (tmp_path / "bloommcp_output/qc_x/v1/a.csv").exists()

    b.delete_files(
        [
            "bloommcp_output/qc_x/v1/a.csv",
            "bloommcp_output/qc_x/v1/missing.csv",  # never existed — no error
        ]
    )
    assert not (tmp_path / "bloommcp_output/qc_x/v1/a.csv").exists()
    assert (tmp_path / "bloommcp_output/qc_x/v1/b.csv").exists()  # untouched


def test_local_delete_files_empty_list_is_noop(tmp_path):
    b = _local(tmp_path)
    b.delete_files([])  # must not raise


def test_supabase_backend_delete_files_calls_bucket_remove(monkeypatch):
    calls = []

    class _FakeClient:
        def remove(self, paths):
            calls.append(list(paths))

    monkeypatch.setattr(
        "bloom_mcp.supabase_client.get_storage_client",
        lambda **_kwargs: _FakeClient(),
    )

    backend = sb.SupabaseStorageBackend()
    backend.delete_files(
        ["bloommcp_output/qc_x/v1/a.csv", "bloommcp_output/qc_x/v1/b.csv"]
    )
    assert calls == [["bloommcp_output/qc_x/v1/a.csv", "bloommcp_output/qc_x/v1/b.csv"]]


def test_supabase_backend_delete_files_passes_timeout_override(monkeypatch):
    captured = {}

    class _FakeClient:
        def remove(self, paths):
            return paths

    def _fake_get_storage_client(**kwargs):
        captured.update(kwargs)
        return _FakeClient()

    monkeypatch.setattr(
        "bloom_mcp.supabase_client.get_storage_client", _fake_get_storage_client
    )

    sb.SupabaseStorageBackend().delete_files(["k"], timeout_seconds=5.0)
    assert captured == {"timeout_seconds": 5.0}


def test_supabase_backend_delete_files_empty_list_skips_client(monkeypatch):
    def _boom(**_kwargs):
        raise AssertionError("get_storage_client called for an empty delete")

    monkeypatch.setattr("bloom_mcp.supabase_client.get_storage_client", _boom)
    sb.SupabaseStorageBackend().delete_files([])  # must not raise / not call the client


class _FakeSbClient:
    """Stand-in for `supabase.Client`, minimal enough for `.storage.from_()`."""

    class storage:  # noqa: N801 - mirrors the real client's attribute name
        @staticmethod
        def from_(_bucket):
            return "bucket-proxy"


def test_get_storage_client_default_passes_no_options_override(monkeypatch):
    import bloom_mcp.supabase_client as sc

    captured = {}

    def _fake_create_client(url, key, options=None):
        captured["options"] = options
        return _FakeSbClient()

    monkeypatch.setenv("SUPABASE_URL", "http://x")
    monkeypatch.setenv("BLOOM_AGENT_KEY", "k")
    monkeypatch.setattr(sc.supabase, "create_client", _fake_create_client)

    sc.get_storage_client()
    assert captured["options"] is None


def test_get_storage_client_timeout_override_builds_client_options(monkeypatch):
    import bloom_mcp.supabase_client as sc

    captured = {}

    def _fake_create_client(url, key, options=None):
        captured["options"] = options
        return _FakeSbClient()

    monkeypatch.setenv("SUPABASE_URL", "http://x")
    monkeypatch.setenv("BLOOM_AGENT_KEY", "k")
    monkeypatch.setattr(sc.supabase, "create_client", _fake_create_client)

    sc.get_storage_client(timeout_seconds=5.0)
    assert captured["options"].storage_client_timeout == 5.0


# ─── 3. Root resolution + startup validation ──────────────────────────────────


def test_root_prefers_dedicated_var(monkeypatch, tmp_path):
    # Explicit BLOOM_STORAGE_LOCAL_ROOT wins outright — assert that holds even
    # with BLOOM_LOCAL_ROOT also set (#479's middle tier), not just when it
    # happens to be unset in the ambient test environment.
    monkeypatch.setenv("BLOOM_STORAGE_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setenv("BLOOM_OUTPUT_DIR", str(tmp_path / "other"))
    monkeypatch.setenv("BLOOM_LOCAL_ROOT", str(tmp_path / "unused"))
    assert sb._resolve_local_root() == tmp_path


def test_root_falls_back_to_output_dir(monkeypatch, tmp_path):
    # Explicitly clear BLOOM_LOCAL_ROOT so this exercises the true 2-tier
    # fallback regardless of ambient env (e.g. a dev's shell profile) — a
    # BLOOM_LOCAL_ROOT left set there would otherwise silently divert this to
    # the #479 middle tier instead of BLOOM_OUTPUT_DIR.
    monkeypatch.delenv("BLOOM_STORAGE_LOCAL_ROOT", raising=False)
    monkeypatch.delenv("BLOOM_LOCAL_ROOT", raising=False)
    monkeypatch.setenv("BLOOM_OUTPUT_DIR", str(tmp_path))
    assert sb._resolve_local_root() == tmp_path


def test_invalid_backend_fails_via_boot_validator(monkeypatch):
    """experiment_utils.validate_env (what server.main() calls) fails fast."""
    import bloom_mcp.experiment_utils as eu

    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "locel")
    with pytest.raises(RuntimeError, match="locel"):
        eu.validate_env()


def test_local_unusable_root_fails_via_boot_validator(monkeypatch, tmp_path):
    import bloom_mcp.experiment_utils as eu

    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "local")
    monkeypatch.setenv("BLOOM_STORAGE_LOCAL_ROOT", str(tmp_path / "nonexistent"))
    with pytest.raises(RuntimeError, match="does not exist|not a directory"):
        eu.validate_env()


def test_local_valid_root_passes_boot_validator(monkeypatch, tmp_path):
    import bloom_mcp.experiment_utils as eu

    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "local")
    monkeypatch.setenv("BLOOM_STORAGE_LOCAL_ROOT", str(tmp_path))
    eu.validate_env()  # must not raise


# ─── 4. Parity, integrity + workflow round-trip ───────────────────────────────


def test_manifest_bytes_identical_fake_vs_local(fake_supabase_storage, tmp_path):
    """The serialized manifest is byte-identical across backends (the in-memory
    fake is the Supabase parity oracle)."""
    payload = {
        "manifest_schema_version": 3,
        "versions": [{"id": "v1", "b": 2, "a": 1}],
        "latest": "v1",
    }
    key = "bloommcp_output/qc_x/manifest.json"
    fake_supabase_storage.write_json(key, payload)
    fake_bytes = fake_supabase_storage.objects[key]

    sb.LocalStorageBackend(tmp_path).write_json(key, payload)
    local_bytes = (tmp_path / "bloommcp_output" / "qc_x" / "manifest.json").read_bytes()

    assert local_bytes == fake_bytes


def test_local_store_roundtrip_matches_contract(monkeypatch, tmp_path):
    """SupabaseResultStore on the local backend yields the same observable
    outcome test_store_parity locks for the fake/Supabase path — plus real files."""
    from bloom_mcp.contract import Provenance
    from bloom_mcp.result_store import SupabaseResultStore

    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "local")
    monkeypatch.setenv("BLOOM_STORAGE_LOCAL_ROOT", str(tmp_path))
    monkeypatch.delenv("BLOOM_STORAGE_URL", raising=False)
    sb.reset_backend_for_tests()

    store = SupabaseResultStore()
    run = store.create_run(
        experiment="exp.csv",
        tool_class="qc",
        provenance=Provenance.stamp(tool="t", params={"a": 1}, seed=5),
    )
    (run.staging_dir / "_cleaned.csv").write_bytes(b"data")
    stored = store.commit(run, {"cleaned": "_cleaned.csv"})

    assert stored.run_ref == "v1"
    assert stored.seed == 5
    assert stored.output_sha256["cleaned"] == hashlib.sha256(b"data").hexdigest()
    assert "\\" not in stored.output_keys["cleaned"]
    assert stored.output_keys["cleaned"].startswith("bloommcp_output/qc_exp/")
    assert store.get_run("exp.csv", "qc", "latest").run_ref == "v1"

    # #642 follow-up: local backend surfaces the resolved direct path, not a
    # URL — no BLOOM_STORAGE_URL needed at all.
    link = stored.output_links["cleaned"]
    assert link.path == str(tmp_path / stored.output_keys["cleaned"])
    assert link.url is None
    assert link.size_bytes == len(b"data")

    # real files on disk, laid out by key
    out = tmp_path / "bloommcp_output" / "qc_exp"
    assert (out / "manifest.json").is_file()
    assert (out / stored.version_dir / "_cleaned.csv").read_bytes() == b"data"

    # #642 review finding: get_download_links must not require
    # BLOOM_STORAGE_URL either — it re-derives the same direct path rather
    # than calling create_signed_url (which would raise without that var).
    resolved = store.get_download_links("exp.csv", "qc", "latest")
    relink = resolved.output_links["cleaned"]
    assert relink.path == link.path
    assert relink.url is None
    assert relink.size_bytes == len(b"data")

    # #643 review finding: a deleted/moved committed output must not leak its
    # raw storage key to the caller — get_download_links redacts this the
    # same way it already redacts a scope-mismatched key (CorruptRunLinksError).
    Path(link.path).unlink()
    from bloom_mcp.result_store import OutputFileMissingError

    with pytest.raises(OutputFileMissingError) as exc_info:
        store.get_download_links("exp.csv", "qc", "latest")
    msg = str(exc_info.value)
    assert stored.output_keys["cleaned"] not in msg
    assert "see server logs" in msg


def test_default_path_writes_no_local_files(
    fake_supabase_storage, monkeypatch, tmp_path
):
    """Opt-in guard: with the default backend, a commit writes NO local files and
    the (faked) Supabase store receives the bytes."""
    from bloom_mcp.contract import Provenance
    from bloom_mcp.result_store import SupabaseResultStore

    monkeypatch.delenv("BLOOM_STORAGE_BACKEND", raising=False)  # default supabase
    monkeypatch.setenv("BLOOM_STORAGE_LOCAL_ROOT", str(tmp_path))  # would-be root
    sb.reset_backend_for_tests()

    store = SupabaseResultStore()
    run = store.create_run(
        experiment="exp.csv",
        tool_class="qc",
        provenance=Provenance.stamp(tool="t", params={}, seed=1),
    )
    (run.staging_dir / "_cleaned.csv").write_bytes(b"data")
    store.commit(run, {"cleaned": "_cleaned.csv"})

    assert any(k.endswith("_cleaned.csv") for k in fake_supabase_storage.objects)
    assert list(tmp_path.rglob("*")) == []  # nothing written to the local root


def test_local_layout_disjoint_from_legacy_fallback(monkeypatch, tmp_path):
    """Local outputs live under bloommcp_output/ and never at the legacy
    <BLOOM_OUTPUT_DIR>/qc_<stem>/<stem>_cleaned.csv fallback path."""
    from bloom_mcp.contract import Provenance
    from bloom_mcp.result_store import SupabaseResultStore

    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "local")
    monkeypatch.setenv("BLOOM_STORAGE_LOCAL_ROOT", str(tmp_path))  # == BLOOM_OUTPUT_DIR
    monkeypatch.setenv("BLOOM_STORAGE_URL", "http://localhost/output")
    sb.reset_backend_for_tests()

    store = SupabaseResultStore()
    run = store.create_run(
        experiment="exp.csv",
        tool_class="qc",
        provenance=Provenance.stamp(tool="t", params={}, seed=1),
    )
    (run.staging_dir / "_cleaned.csv").write_bytes(b"data")
    store.commit(run, {"cleaned": "_cleaned.csv"})

    legacy = tmp_path / "qc_exp" / "exp_cleaned.csv"
    assert not legacy.exists()
    assert (tmp_path / "bloommcp_output" / "qc_exp" / "manifest.json").is_file()


class _FakeSbStorageClient:
    """In-memory stand-in for the storage3 bucket client that
    `SupabaseStorageBackend`'s methods call via `get_storage_client()`.

    Unlike the `fake_supabase_storage` fixture (which monkeypatches
    `bloom_mcp.manifest.manifest`'s module-level `list_prefix`/`read_json`/
    `write_json` directly, bypassing `storage_backend.active_backend()`
    dispatch entirely), patching only `get_storage_client` lets the real
    `SupabaseStorageBackend` class run through the real dispatch path — so a
    test can toggle `BLOOM_STORAGE_BACKEND` mid-test and genuinely switch
    between this fake and the real `LocalStorageBackend`.
    """

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def upload(self, *, path, file, file_options=None):
        del file_options
        self.objects[path] = file

    def download(self, path):
        if path not in self.objects:
            raise KeyError(f"object not found: {path}")
        return self.objects[path]

    def list(self, prefix):
        norm = (prefix.rstrip("/") + "/") if prefix else ""
        names: set[str] = set()
        for key in self.objects:
            if key.startswith(norm):
                names.add(key[len(norm) :].split("/", 1)[0])
        return [{"name": n} for n in sorted(names) if n]

    def remove(self, paths):
        for p in paths:
            self.objects.pop(p, None)

    def create_signed_url(self, path, expires_in):
        # Realistic dict shape (bloom#581) — the real client returns a dict,
        # not a bare string. No BLOOM_PUBLIC_SUPABASE_URL is set by the tests
        # using this fake, so _to_public_url is a no-op and this is returned
        # verbatim.
        return {"signedURL": f"http://kong:8000/sign/{path}?expires_in={expires_in}"}

    def info(self, path):
        # Realistic nested shape (bloom#599) — real Supabase Storage's
        # object-info endpoint nests size under `metadata`, matching
        # storage3's only comparable typed object (`SearchV2Object`).
        if path not in self.objects:
            raise KeyError(f"object not found: {path}")
        return {"name": path, "metadata": {"size": len(self.objects[path])}}


def test_write_manifest_stamps_active_backend(monkeypatch, tmp_path):
    """`write_manifest` stamps `Manifest.storage_backend` with whichever
    backend is active at write time (#395's sentinel), for both `supabase`
    (default) and `local`."""
    from bloom_mcp.contract import Provenance
    from bloom_mcp.manifest import AnalysisDir
    from bloom_mcp.result_store import SupabaseResultStore

    # One shared instance: SupabaseStorageBackend calls get_storage_client()
    # fresh per method (stateless wrapper around real Supabase's server-side
    # state) — the fake must hand back the SAME instance every call, or each
    # upload/list/download would silently talk to a different empty store.
    fake_client = _FakeSbStorageClient()
    monkeypatch.setattr(
        "bloom_mcp.supabase_client.get_storage_client",
        lambda **_kwargs: fake_client,
    )

    def _commit(experiment: str) -> None:
        store = SupabaseResultStore()
        run = store.create_run(
            experiment=experiment,
            tool_class="qc",
            provenance=Provenance.stamp(tool="t", params={}, seed=1),
        )
        (run.staging_dir / "_cleaned.csv").write_bytes(b"data")
        store.commit(run, {"cleaned": "_cleaned.csv"})

    monkeypatch.delenv("BLOOM_STORAGE_BACKEND", raising=False)
    sb.reset_backend_for_tests()
    _commit("exp.csv")
    manifest = AnalysisDir("bloommcp_output", "exp.csv", "qc").read_manifest()
    assert manifest.storage_backend == "supabase"

    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "local")
    monkeypatch.setenv("BLOOM_STORAGE_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setenv("BLOOM_STORAGE_URL", "http://localhost/output")
    sb.reset_backend_for_tests()
    _commit("exp2.csv")
    manifest = AnalysisDir("bloommcp_output", "exp2.csv", "qc").read_manifest()
    assert manifest.storage_backend == "local"


def test_manifest_identical_across_backends_except_storage_backend(
    monkeypatch, tmp_path
):
    """A real commit through `SupabaseResultStore`/`write_manifest` (not a
    hand-built dict, unlike `test_manifest_bytes_identical_fake_vs_local`
    above) produces byte-identical manifests across backends except
    `storage_backend`, which legitimately differs — the MODIFIED spec's core
    claim for the #395 sentinel. Uses `_FakeSbStorageClient` (not the
    `fake_supabase_storage` fixture) so `BLOOM_STORAGE_BACKEND` toggling
    between the two commits genuinely switches the active backend."""
    from bloom_mcp.contract import Provenance
    from bloom_mcp.manifest import AnalysisDir
    from bloom_mcp.result_store import SupabaseResultStore

    # See test_write_manifest_stamps_active_backend for why this must be one
    # shared instance, not a fresh one per get_storage_client() call.
    fake_client = _FakeSbStorageClient()
    monkeypatch.setattr(
        "bloom_mcp.supabase_client.get_storage_client",
        lambda **_kwargs: fake_client,
    )

    # One shared Provenance instance so created_at/code_versions/environment
    # are identical across both commits, not just coincidentally equal.
    prov = Provenance.stamp(tool="t", params={"n": 1}, seed=9)

    def _commit() -> None:
        store = SupabaseResultStore()
        run = store.create_run(experiment="exp.csv", tool_class="qc", provenance=prov)
        (run.staging_dir / "_cleaned.csv").write_bytes(b"data")
        store.commit(run, {"cleaned": "_cleaned.csv"})

    monkeypatch.delenv("BLOOM_STORAGE_BACKEND", raising=False)
    sb.reset_backend_for_tests()
    _commit()
    supabase_manifest = AnalysisDir("bloommcp_output", "exp.csv", "qc").read_manifest()

    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "local")
    monkeypatch.setenv("BLOOM_STORAGE_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setenv("BLOOM_STORAGE_URL", "http://localhost/output")
    sb.reset_backend_for_tests()
    _commit()
    local_manifest = AnalysisDir("bloommcp_output", "exp.csv", "qc").read_manifest()

    assert supabase_manifest.storage_backend == "supabase"
    assert local_manifest.storage_backend == "local"

    supabase_dump = supabase_manifest.model_dump(mode="json")
    local_dump = local_manifest.model_dump(mode="json")
    del supabase_dump["storage_backend"], local_dump["storage_backend"]
    assert supabase_dump == local_dump

    # Also compare the actual serialized bytes each store received (not a
    # re-derived model_dump()), matching the spec's literal "byte-identical
    # serialized manifest.json" claim.
    supabase_raw = json.loads(
        fake_client.objects["bloommcp_output/qc_exp/manifest.json"]
    )
    local_raw = json.loads(
        (tmp_path / "bloommcp_output" / "qc_exp" / "manifest.json").read_bytes()
    )
    del supabase_raw["storage_backend"], local_raw["storage_backend"]
    assert supabase_raw == local_raw

    # bloom#581: output_links (and any URL/size sibling) is a StoredRun-only,
    # request-time field — it must never appear in either serialized manifest.
    for raw in (supabase_raw, local_raw):
        for version in raw["versions"]:
            assert "output_links" not in version
            assert "size_bytes" not in version


def test_repeated_backend_flip_logs_once_not_on_return(monkeypatch, tmp_path, caplog):
    """#395 spec scenario "Repeated backend flips do not repeatedly signal":
    supabase -> local -> supabase logs the fresh-catalog message on the first
    commit to each backend's own (initially-empty) catalog, but NOT again on
    the return trip to supabase, whose manifest already exists from the first
    commit."""
    from bloom_mcp.contract import Provenance
    from bloom_mcp.result_store import SupabaseResultStore

    fake_client = _FakeSbStorageClient()
    monkeypatch.setattr(
        "bloom_mcp.supabase_client.get_storage_client",
        lambda **_kwargs: fake_client,
    )

    def _commit() -> None:
        store = SupabaseResultStore()
        run = store.create_run(
            experiment="exp.csv",
            tool_class="qc",
            provenance=Provenance.stamp(tool="t", params={}, seed=1),
        )
        (run.staging_dir / "_cleaned.csv").write_bytes(b"data")
        store.commit(run, {"cleaned": "_cleaned.csv"})

    def _fresh_catalog_log_count() -> int:
        return sum(
            1
            for r in caplog.records
            if "fresh manifest catalog" in r.getMessage().lower()
        )

    with caplog.at_level(logging.INFO):
        # supabase: first commit ever for this pair -> fresh catalog, logs.
        monkeypatch.delenv("BLOOM_STORAGE_BACKEND", raising=False)
        sb.reset_backend_for_tests()
        caplog.clear()
        _commit()
        assert _fresh_catalog_log_count() == 1

        # flip to local: a different, still-empty catalog -> also logs.
        monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "local")
        monkeypatch.setenv("BLOOM_STORAGE_LOCAL_ROOT", str(tmp_path))
        monkeypatch.setenv("BLOOM_STORAGE_URL", "http://localhost/output")
        sb.reset_backend_for_tests()
        caplog.clear()
        _commit()
        assert _fresh_catalog_log_count() == 1

        # flip back to supabase: its manifest already exists from the first
        # commit above -> no log this time, even though a local-backed run
        # happened in between (the documented residual gap).
        monkeypatch.delenv("BLOOM_STORAGE_BACKEND", raising=False)
        sb.reset_backend_for_tests()
        caplog.clear()
        _commit()
        assert _fresh_catalog_log_count() == 0


# ─── 5. Cross-backend list_prefix parity + read-path fallback ──────────────────


def test_list_prefix_parity_fake_vs_local(fake_supabase_storage, tmp_path):
    """The in-memory fake (Supabase oracle) and the local backend return the same
    list_prefix results across empty / no-slash / trailing-slash / missing prefixes
    — the property read_manifest and _resolve_versioned_cleaned depend on."""
    fake = fake_supabase_storage
    root = tmp_path / "root"
    root.mkdir()
    local = sb.LocalStorageBackend(root)
    src = tmp_path / "seed.csv"
    src.write_bytes(b"x")

    keys = [
        "bloommcp_output/qc_x/manifest.json",
        "bloommcp_output/qc_x/v1_2026/_cleaned.csv",
        "bloommcp_output/qc_x/v2_2026/_cleaned.csv",
        "bloommcp_output/qc_y/manifest.json",
    ]
    for k in keys:
        if k.endswith(".json"):
            fake.write_json(k, {"k": 1})
            local.write_json(k, {"k": 1})
        else:
            fake.upload_file(k, src)
            local.upload_file(k, src)

    for prefix in [
        "",
        "bloommcp_output",
        "bloommcp_output/",
        "bloommcp_output/qc_x",
        "bloommcp_output/qc_x/",
        "bloommcp_output/qc_missing/",
    ]:
        fake_names = sorted(fake.list_prefix(prefix))
        local_names = sorted(local.list_prefix(prefix))
        assert fake_names == local_names, f"list_prefix mismatch for {prefix!r}"


def test_resolve_versioned_cleaned_via_local_list_prefix_fallback(
    monkeypatch, tmp_path
):
    """A manifest entry with version_dir='' forces _resolve_versioned_cleaned to
    locate the version directory via list_prefix — exercising the local backend's
    list leg end-to-end in the read path (the qc round-trip never hits it because
    writers always set version_dir)."""
    from bloom_mcp import experiment_utils as eu
    from bloom_mcp.manifest import (
        ExperimentBlock,
        Manifest,
        VersionEntry,
        get_code_versions,
        write_manifest,
    )
    from bloom_mcp.supabase_client import upload_file

    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "local")
    monkeypatch.setenv("BLOOM_STORAGE_LOCAL_ROOT", str(root))
    sb.reset_backend_for_tests()

    stem = "exp"
    prefix = f"bloommcp_output/qc_{stem}/"
    cleaned = tmp_path / "cleaned.csv"
    cleaned.write_bytes(b"trait,value\n1,2\n")
    upload_file(f"{prefix}v1_2026-07-06/_cleaned.csv", cleaned)

    entry = VersionEntry(
        id="v1",
        created_at="2026-07-06T00:00:00Z",
        tool="run_qc_workflow",
        params={},
        based_on_version="raw",
        code_versions=get_code_versions(),
        outputs={"_cleaned.csv": "_cleaned.csv"},
        version_dir="",  # empty → forces the list_prefix sibling lookup
    )
    manifest = Manifest(
        experiment=ExperimentBlock(
            filename=f"{stem}.csv", source_path="", input_sha256=""
        ),
        versions=[entry],
        latest="v1",
    )
    write_manifest(prefix, manifest)

    path, label, err = eu._resolve_versioned_cleaned(eu.OUTPUT_DIR, stem, "latest")
    assert err is None
    assert path is not None
    assert path.read_bytes() == b"trait,value\n1,2\n"
    assert label == "v1_cleaned"


# ─── 5b. #420 — outliers-preferring "latest" vs qc-only "latest_qc" ────────────
#
# `write_cleaned_manifest`/`write_invalid_schema_manifest` (imported at top of
# file) live in `manifest_fixtures.py`; `local_manifest_backend` lives in
# `conftest.py` — both promoted out of here (bloom#585) so other test files can
# build real, on-disk manifests too.


def test_latest_resolves_qc_only_unqualified(local_manifest_backend):
    """(a) Only `qc` has a version — `version="latest"` resolves it, today's exact
    unqualified label, unchanged. The overwhelmingly common (never-trimmed) case must
    see zero observable change from this fix."""
    from bloom_mcp import experiment_utils as eu

    tmp_path = local_manifest_backend
    write_cleaned_manifest(
        tmp_path, "exp", "qc", "v1", "2026-07-06T00:00:00Z", b"a,b\n1,2\n"
    )

    path, label, err = eu._resolve_versioned_cleaned(eu.OUTPUT_DIR, "exp", "latest")
    assert err is None
    assert path is not None and path.read_bytes() == b"a,b\n1,2\n"
    assert label == "v1_cleaned"


def test_latest_resolves_outliers_only_qualified(local_manifest_backend):
    """(b) Only `outliers` has a version — `version="latest"` resolves it, with the
    tool-class-qualified label."""
    from bloom_mcp import experiment_utils as eu

    tmp_path = local_manifest_backend
    write_cleaned_manifest(
        tmp_path, "exp", "outliers", "v1", "2026-07-06T00:00:00Z", b"a,b\n3,4\n"
    )

    path, label, err = eu._resolve_versioned_cleaned(eu.OUTPUT_DIR, "exp", "latest")
    assert err is None
    assert path is not None and path.read_bytes() == b"a,b\n3,4\n"
    assert label == "outliers_v1_cleaned"


def test_latest_prefers_outliers_regardless_of_recency(local_manifest_backend):
    """(c) The actual #420 repro: `qc`'s entry is committed (and timestamped) LATER
    than `outliers`'s, yet `version="latest"` must still resolve `outliers` — proving
    this is a fixed priority, not a recency comparison. An earlier (wrong) draft of
    this fix compared `created_at` across classes and would resolve `qc` here."""
    from bloom_mcp import experiment_utils as eu

    tmp_path = local_manifest_backend
    write_cleaned_manifest(
        tmp_path, "exp", "outliers", "v1", "2026-07-06T00:00:00Z", b"trim,ok\n1,1\n"
    )
    write_cleaned_manifest(
        tmp_path, "exp", "qc", "v2", "2026-07-06T23:59:59Z", b"untrimmed\n9\n"
    )

    path, label, err = eu._resolve_versioned_cleaned(eu.OUTPUT_DIR, "exp", "latest")
    assert err is None
    assert path is not None and path.read_bytes() == b"trim,ok\n1,1\n"
    assert label == "outliers_v1_cleaned"


def test_latest_qc_resolves_qc_ignoring_outliers(local_manifest_backend):
    """(d) `version="latest_qc"` resolves the `qc` class specifically, even when a
    newer-looking `outliers` version exists — this is what `remove_outliers` itself
    reads as its trimming input."""
    from bloom_mcp import experiment_utils as eu

    tmp_path = local_manifest_backend
    write_cleaned_manifest(
        tmp_path, "exp", "outliers", "v1", "2026-07-06T00:00:00Z", b"trim,ok\n1,1\n"
    )
    write_cleaned_manifest(
        tmp_path, "exp", "qc", "v2", "2026-07-06T23:59:59Z", b"untrimmed\n9\n"
    )

    path, label, err = eu._resolve_versioned_cleaned(eu.OUTPUT_DIR, "exp", "latest_qc")
    assert err is None
    assert path is not None and path.read_bytes() == b"untrimmed\n9\n"
    assert label == "v2_cleaned"  # unqualified — same format as version="latest_qc"


def test_latest_qc_resolves_qc_only_unqualified(local_manifest_backend):
    """(e) `version="latest_qc"` with no `outliers` class at all resolves `qc`, with
    the same unqualified label as (a) — confirms `latest_qc` isn't a no-op alias that
    silently means something else when `outliers` is absent."""
    from bloom_mcp import experiment_utils as eu

    tmp_path = local_manifest_backend
    write_cleaned_manifest(
        tmp_path, "exp", "qc", "v1", "2026-07-06T00:00:00Z", b"a,b\n1,2\n"
    )

    path, label, err = eu._resolve_versioned_cleaned(eu.OUTPUT_DIR, "exp", "latest_qc")
    assert err is None
    assert path is not None and path.read_bytes() == b"a,b\n1,2\n"
    assert label == "v1_cleaned"


def test_latest_schema_error_on_outliers_propagates_first_iteration(
    local_manifest_backend,
):
    """(f) A schema error on `outliers` (checked first, higher priority) propagates
    immediately — it is not swallowed and does not fall through to the valid `qc`
    manifest."""
    from bloom_mcp import experiment_utils as eu

    tmp_path = local_manifest_backend
    write_cleaned_manifest(
        tmp_path, "exp", "qc", "v1", "2026-07-06T00:00:00Z", b"a,b\n1,2\n"
    )
    write_invalid_schema_manifest("exp", "outliers")

    path, label, err = eu._resolve_versioned_cleaned(eu.OUTPUT_DIR, "exp", "latest")
    assert path is None
    assert label is None
    assert err is not None and "manifest schema error for 'exp'" in err


def test_latest_schema_error_on_qc_propagates_second_iteration(local_manifest_backend):
    """(g) The mirror of (f): `outliers` has no entry at all (resolves to "no entry",
    not an error) and `qc` fails schema validation — the error must still propagate
    once the loop reaches its second iteration, not be silently dropped by an
    over-broad `except`/`continue` around the whole loop."""
    from bloom_mcp import experiment_utils as eu

    write_invalid_schema_manifest("exp", "qc")

    path, label, err = eu._resolve_versioned_cleaned(eu.OUTPUT_DIR, "exp", "latest")
    assert path is None
    assert label is None
    assert err is not None and "manifest schema error for 'exp'" in err


def test_latest_outliers_entry_exists_but_download_fails_is_a_hard_error(
    local_manifest_backend,
):
    """A schema-valid `outliers` entry names a version whose `_cleaned.csv` was
    never actually uploaded (a partial commit, or a storage hiccup) — this must
    propagate as a hard error, NOT fall through to `qc`'s otherwise-valid entry.
    Falling through here would reproduce the exact #420 silent-revert hazard,
    just triggered by a storage failure instead of a `qc_clean` re-run."""
    from bloom_mcp import experiment_utils as eu
    from bloom_mcp.manifest import (
        ExperimentBlock,
        Manifest,
        VersionEntry,
        get_code_versions,
        write_manifest,
    )

    write_cleaned_manifest(
        local_manifest_backend, "exp", "qc", "v1", "2026-07-06T00:00:00Z", b"a,b\n1,2\n"
    )

    # outliers manifest references v1, version_dir set, but its _cleaned.csv was
    # never uploaded — no `upload_file` call, unlike `write_cleaned_manifest`.
    prefix = "bloommcp_output/outliers_exp/"
    entry = VersionEntry(
        id="v1",
        created_at="2026-07-06T00:00:01Z",
        tool="remove_outliers",
        params={},
        based_on_version="v1_cleaned",
        code_versions=get_code_versions(),
        outputs={"_cleaned.csv": "_cleaned.csv"},
        version_dir="v1_2026-07-06",
    )
    manifest = Manifest(
        experiment=ExperimentBlock(filename="exp.csv", source_path="", input_sha256=""),
        versions=[entry],
        latest="v1",
    )
    write_manifest(prefix, manifest)

    path, label, err = eu._resolve_versioned_cleaned(eu.OUTPUT_DIR, "exp", "latest")
    assert path is None
    assert label is None
    assert err is not None
    assert "download from storage failed" in err


def test_latest_outliers_manifest_read_fails_is_a_hard_error(
    local_manifest_backend, monkeypatch
):
    """A storage/network failure during the manifest *lookup itself* (`get_version`'s
    `read_manifest()` call, via its own unguarded `list_prefix`) must propagate as a
    caller-safe hard error -- not an uncaught raw exception, and not a silent
    fall-through to `qc`'s otherwise-valid entry. Before this fix, `_resolve_one_class`
    only caught `ManifestSchemaError` around this call, so this exact failure escaped
    uncaught; this is the #586 regression test."""
    import bloom_mcp.manifest.manifest as manifest_mod
    from bloom_mcp import experiment_utils as eu

    write_cleaned_manifest(
        local_manifest_backend, "exp", "qc", "v1", "2026-07-06T00:00:00Z", b"a,b\n1,2\n"
    )
    real_list_prefix = manifest_mod.list_prefix

    def _boom(prefix: str):
        if prefix.startswith("bloommcp_output/outliers_"):
            raise RuntimeError("connection reset by peer at 10.0.0.5:5432")
        return real_list_prefix(prefix)

    monkeypatch.setattr(manifest_mod, "list_prefix", _boom)
    # A schema-valid `outliers` manifest that would otherwise resolve cleanly --
    # the failure is injected purely via the monkeypatched list_prefix above, not
    # via a malformed manifest, so this isolates the manifest-*read* hazard from
    # the already-covered manifest-*schema* hazard.
    write_cleaned_manifest(
        local_manifest_backend,
        "exp",
        "outliers",
        "v1",
        "2026-07-06T00:00:01Z",
        b"trim\n1\n",
    )

    path, label, err = eu._resolve_versioned_cleaned(eu.OUTPUT_DIR, "exp", "latest")
    assert path is None
    assert label is None
    assert err is not None and "could not read manifest for 'exp'" in err


def test_latest_logs_when_resolved_trim_is_stale(local_manifest_backend, caplog):
    """The resolved `outliers` trim's `based_on_version` ("v1_cleaned") no longer
    matches the current `qc` latest ("v2_cleaned") — a `qc_clean` has run since
    the trim was made (design.md Decision 4's disclosed trade-off). This is
    purely observational: the trim still correctly resolves as "latest cleaned",
    but a log line makes the staleness visible at read time."""
    from bloom_mcp import experiment_utils as eu

    write_cleaned_manifest(
        local_manifest_backend, "exp", "qc", "v1", "2026-07-06T00:00:00Z", b"a,b\n1,2\n"
    )
    write_cleaned_manifest(
        local_manifest_backend,
        "exp",
        "outliers",
        "v1",
        "2026-07-06T00:00:01Z",
        b"trim\n1\n",
    )  # based_on_version="v1_cleaned" (via write_cleaned_manifest's default)
    write_cleaned_manifest(
        local_manifest_backend,
        "exp",
        "qc",
        "v2",
        "2026-07-06T00:01:00Z",
        b"a,b\n3,4\n5,6\n",
    )  # a fresh qc_clean re-run, after the trim

    with caplog.at_level(logging.INFO, logger="bloom_mcp.experiment_utils"):
        path, label, err = eu._resolve_versioned_cleaned(eu.OUTPUT_DIR, "exp", "latest")

    assert err is None
    assert (
        path is not None and path.read_bytes() == b"trim\n1\n"
    )  # still resolves the trim
    assert label == "outliers_v1_cleaned"
    assert any("has run since this trim was made" in r.message for r in caplog.records)


def test_latest_does_not_log_when_resolved_trim_is_current(
    local_manifest_backend, caplog
):
    """The resolved `outliers` trim's `based_on_version` matches the current `qc`
    latest exactly (the natural clean-then-trim order, no re-clean since) — no
    staleness log should fire."""
    from bloom_mcp import experiment_utils as eu

    write_cleaned_manifest(
        local_manifest_backend, "exp", "qc", "v1", "2026-07-06T00:00:00Z", b"a,b\n1,2\n"
    )
    write_cleaned_manifest(
        local_manifest_backend,
        "exp",
        "outliers",
        "v1",
        "2026-07-06T00:00:01Z",
        b"trim\n1\n",
    )

    with caplog.at_level(logging.INFO, logger="bloom_mcp.experiment_utils"):
        path, label, err = eu._resolve_versioned_cleaned(eu.OUTPUT_DIR, "exp", "latest")

    assert err is None
    assert path is not None
    assert label == "outliers_v1_cleaned"
    assert not any(
        "has run since this trim was made" in r.message for r in caplog.records
    )


# ─── 5b-2. explicit version="v<N>" checks BOTH qc and outliers (#644 review) ──
#
# Before this fix, an explicit version resolved against the `qc` class only.
# `list_existing_analyses` lists `qc`/`outliers` versions separately, each with
# its own independently-numbered `v<N>` sequence — a caller pinning an id seen
# under `outliers` could silently get an unrelated `qc`-class entry of the same
# id instead (the wrong, untrimmed dataset) rather than an error.


def test_explicit_version_resolves_qc_when_only_qc_has_that_id(
    local_manifest_backend,
):
    """No behavior change for the overwhelmingly common case: only `qc` has the
    pinned id — resolves it, unqualified label, exactly as before this fix."""
    from bloom_mcp import experiment_utils as eu

    write_cleaned_manifest(
        local_manifest_backend, "exp", "qc", "v1", "2026-07-06T00:00:00Z", b"a,b\n1,2\n"
    )

    path, label, err = eu._resolve_versioned_cleaned(eu.OUTPUT_DIR, "exp", "v1")
    assert err is None
    assert path is not None and path.read_bytes() == b"a,b\n1,2\n"
    assert label == "v1_cleaned"


def test_explicit_version_resolves_outliers_when_only_outliers_has_that_id(
    local_manifest_backend,
):
    """A version id that exists ONLY under `outliers` (not `qc`) now resolves
    instead of hard-erroring not-found — closing a real coverage gap, not just
    the ambiguity hazard: `list_existing_analyses` can list a version this
    function previously could never pin by id at all."""
    from bloom_mcp import experiment_utils as eu

    write_cleaned_manifest(
        local_manifest_backend,
        "exp",
        "outliers",
        "v1",
        "2026-07-06T00:00:00Z",
        b"trim,ok\n1,1\n",
    )

    path, label, err = eu._resolve_versioned_cleaned(eu.OUTPUT_DIR, "exp", "v1")
    assert err is None
    assert path is not None and path.read_bytes() == b"trim,ok\n1,1\n"
    assert label == "outliers_v1_cleaned"


def test_explicit_version_collision_across_classes_is_ambiguous_not_silently_qc(
    local_manifest_backend,
):
    """The actual #644-review repro: `qc` and `outliers` each independently have
    their own 'v1' with DIFFERENT content. Pinning 'v1' must refuse as
    ambiguous — silently returning `qc`'s content here would be exactly the
    "caller pins the outliers-class id they saw in list_existing_analyses and
    silently gets the wrong, untrimmed dataset" bug the review flagged."""
    from bloom_mcp import experiment_utils as eu

    write_cleaned_manifest(
        local_manifest_backend,
        "exp",
        "qc",
        "v1",
        "2026-07-06T00:00:00Z",
        b"untrimmed\n9\n",
    )
    write_cleaned_manifest(
        local_manifest_backend,
        "exp",
        "outliers",
        "v1",
        "2026-07-06T00:00:01Z",
        b"trim,ok\n1,1\n",
    )

    path, label, err = eu._resolve_versioned_cleaned(eu.OUTPUT_DIR, "exp", "v1")
    assert path is None
    assert label is None
    assert err is not None
    assert "ambiguous" in err.lower()
    assert "'qc'" in err and "'outliers'" in err


def test_explicit_version_not_found_in_either_class(local_manifest_backend):
    """Neither class has the pinned id — a clear not-found error naming both
    classes checked, not just `qc` (previously the only class ever checked)."""
    from bloom_mcp import experiment_utils as eu

    write_cleaned_manifest(
        local_manifest_backend, "exp", "qc", "v1", "2026-07-06T00:00:00Z", b"a,b\n1,2\n"
    )

    path, label, err = eu._resolve_versioned_cleaned(eu.OUTPUT_DIR, "exp", "v9")
    assert path is None
    assert label is None
    assert err is not None
    assert "not found" in err.lower()
    assert "'qc'" in err and "'outliers'" in err


def test_explicit_version_infra_error_takes_priority_over_not_found_elsewhere(
    local_manifest_backend,
):
    """`outliers` fails schema validation while `qc` simply has no matching id —
    the genuine infra failure must surface, not be masked by the other class's
    plain not-found miss."""
    from bloom_mcp import experiment_utils as eu

    write_cleaned_manifest(
        local_manifest_backend, "exp", "qc", "v2", "2026-07-06T00:00:00Z", b"a,b\n1,2\n"
    )
    write_invalid_schema_manifest("exp", "outliers")

    path, label, err = eu._resolve_versioned_cleaned(eu.OUTPUT_DIR, "exp", "v1")
    assert path is None
    assert label is None
    assert err is not None and "manifest schema error for 'exp'" in err


# ─── 5c. bloom#585 — shared `trim_staleness` primitive ────────────────────────


def test_trim_staleness_none_when_never_trimmed(local_manifest_backend):
    """(a) No `outliers`-class version at all — nothing to assess."""
    from bloom_mcp import experiment_utils as eu

    write_cleaned_manifest(
        local_manifest_backend, "exp", "qc", "v1", "2026-07-06T00:00:00Z", b"a,b\n1,2\n"
    )

    assert eu.trim_staleness("exp") is None


def test_trim_staleness_false_when_trim_is_current(local_manifest_backend):
    """(b) The trim's `based_on_version` matches the current `qc` latest exactly."""
    from bloom_mcp import experiment_utils as eu

    write_cleaned_manifest(
        local_manifest_backend, "exp", "qc", "v1", "2026-07-06T00:00:00Z", b"a,b\n1,2\n"
    )
    write_cleaned_manifest(
        local_manifest_backend,
        "exp",
        "outliers",
        "v1",
        "2026-07-06T00:00:01Z",
        b"trim\n1\n",
    )

    result = eu.trim_staleness("exp")
    assert result is not None
    assert result.is_stale is False
    assert result.outliers_based_on_version == "v1_cleaned"
    assert result.current_qc_label == "v1_cleaned"


def test_trim_staleness_true_when_qc_has_moved_on(local_manifest_backend):
    """(c) A `qc_clean` has run since the trim was made."""
    from bloom_mcp import experiment_utils as eu

    write_cleaned_manifest(
        local_manifest_backend, "exp", "qc", "v1", "2026-07-06T00:00:00Z", b"a,b\n1,2\n"
    )
    write_cleaned_manifest(
        local_manifest_backend,
        "exp",
        "outliers",
        "v1",
        "2026-07-06T00:00:01Z",
        b"trim\n1\n",
    )
    write_cleaned_manifest(
        local_manifest_backend,
        "exp",
        "qc",
        "v2",
        "2026-07-06T00:01:00Z",
        b"a,b\n3,4\n5,6\n",
    )

    result = eu.trim_staleness("exp")
    assert result is not None
    assert result.is_stale is True
    assert result.outliers_based_on_version == "v1_cleaned"
    assert result.current_qc_label == "v2_cleaned"


def test_trim_staleness_true_when_no_qc_baseline_at_all(local_manifest_backend):
    """(d) An `outliers`-class version exists but the `qc`-class manifest has no
    `latest` entry at all — a new, previously-untested/unreached corner (design.md
    Decision 1): treated as stale, not as "nothing to see"."""
    from bloom_mcp import experiment_utils as eu

    write_cleaned_manifest(
        local_manifest_backend,
        "exp",
        "outliers",
        "v1",
        "2026-07-06T00:00:00Z",
        b"trim\n1\n",
    )

    result = eu.trim_staleness("exp")
    assert result is not None
    assert result.is_stale is True
    assert result.current_qc_label is None


def test_latest_logs_distinct_message_when_no_qc_baseline_at_all(
    local_manifest_backend, caplog
):
    """The new no-`qc`-baseline-at-all case (design.md Decision 1) must not
    interpolate `None` into the pre-existing "a qc_clean has run since..." message
    — it needs its own distinct wording naming that no `qc`-class version could be
    found at all."""
    from bloom_mcp import experiment_utils as eu

    write_cleaned_manifest(
        local_manifest_backend,
        "exp",
        "outliers",
        "v1",
        "2026-07-06T00:00:00Z",
        b"trim\n1\n",
    )

    with caplog.at_level(logging.INFO, logger="bloom_mcp.experiment_utils"):
        path, label, err = eu._resolve_versioned_cleaned(eu.OUTPUT_DIR, "exp", "latest")

    assert err is None
    assert path is not None and label == "outliers_v1_cleaned"
    assert not any(
        "has run since this trim was made" in r.message for r in caplog.records
    )
    assert any(
        "no qc" in r.message.lower() and "could be found" in r.message.lower()
        for r in caplog.records
    )


# ─── 5d. bloom#585 review — `safe_error_text` redaction/truncation ────────────


def test_safe_error_text_truncates_long_messages():
    from bloom_mcp.experiment_utils import safe_error_text

    text = safe_error_text(RuntimeError("x" * 500), limit=50)
    assert len(text) <= len("...<truncated>") + 50
    assert text.endswith("...<truncated>")


def test_safe_error_text_redacts_apikey_and_bearer_fragments():
    from bloom_mcp.experiment_utils import safe_error_text

    text = safe_error_text(
        RuntimeError("request failed: apikey=sk_live_deadbeef1234 (401)")
    )
    assert "sk_live_deadbeef1234" not in text
    assert "apikey=<redacted>" in text

    text2 = safe_error_text(RuntimeError("Authorization: Bearer abc.def.ghi"))
    assert "abc.def.ghi" not in text2


def test_safe_error_text_leaves_ordinary_messages_unchanged():
    from bloom_mcp.experiment_utils import safe_error_text

    text = safe_error_text(RuntimeError("manifest schema error for 'exp': boom"))
    assert text == "manifest schema error for 'exp': boom"


# ─── 6. BLOOM_LOCAL_ROOT (#479) ─────────────────────────────────────────────────


def test_local_root_supplies_output_default_when_dedicated_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("BLOOM_STORAGE_LOCAL_ROOT", raising=False)
    monkeypatch.delenv("BLOOM_OUTPUT_DIR", raising=False)
    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "local")
    monkeypatch.setenv("BLOOM_LOCAL_ROOT", str(tmp_path))
    assert sb._resolve_local_root() == tmp_path / "output"


def test_dedicated_var_wins_over_local_root(monkeypatch, tmp_path):
    dedicated = tmp_path / "dedicated"
    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "local")
    monkeypatch.setenv("BLOOM_STORAGE_LOCAL_ROOT", str(dedicated))
    monkeypatch.setenv("BLOOM_LOCAL_ROOT", str(tmp_path))
    assert sb._resolve_local_root() == dedicated


def test_local_root_inert_when_backend_not_local(monkeypatch, tmp_path):
    monkeypatch.delenv("BLOOM_STORAGE_LOCAL_ROOT", raising=False)
    monkeypatch.setenv("BLOOM_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("BLOOM_LOCAL_ROOT", str(tmp_path / "unused"))
    monkeypatch.delenv("BLOOM_STORAGE_BACKEND", raising=False)  # defaults to supabase
    assert sb._resolve_local_root() == tmp_path


def test_local_root_falls_back_to_output_dir_when_both_unset(monkeypatch, tmp_path):
    """Existing 2-tier fallback is unchanged when BLOOM_LOCAL_ROOT is also unset."""
    monkeypatch.delenv("BLOOM_STORAGE_LOCAL_ROOT", raising=False)
    monkeypatch.delenv("BLOOM_LOCAL_ROOT", raising=False)
    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "local")
    monkeypatch.setenv("BLOOM_OUTPUT_DIR", str(tmp_path))
    assert sb._resolve_local_root() == tmp_path


def test_validate_storage_backend_creates_output_subfolder(monkeypatch, tmp_path):
    monkeypatch.delenv("BLOOM_STORAGE_LOCAL_ROOT", raising=False)
    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "local")
    monkeypatch.setenv("BLOOM_LOCAL_ROOT", str(tmp_path))
    sb.reset_backend_for_tests()
    assert not (tmp_path / "output").exists()
    sb.validate_storage_backend()
    assert (tmp_path / "output").is_dir()


def test_validate_storage_backend_explicit_override_still_strict(monkeypatch, tmp_path):
    missing = tmp_path / "nope"
    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "local")
    monkeypatch.setenv("BLOOM_STORAGE_LOCAL_ROOT", str(missing))
    monkeypatch.setenv("BLOOM_LOCAL_ROOT", str(tmp_path))  # set but must not rescue
    sb.reset_backend_for_tests()
    with pytest.raises(RuntimeError, match="does not exist"):
        sb.validate_storage_backend()


def test_validate_storage_backend_output_subfolder_blocked_by_file(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("BLOOM_STORAGE_LOCAL_ROOT", raising=False)
    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "local")
    monkeypatch.setenv("BLOOM_LOCAL_ROOT", str(tmp_path))
    (tmp_path / "output").write_text("blocking file")
    sb.reset_backend_for_tests()
    with pytest.raises(RuntimeError, match="output root.*not a directory"):
        sb.validate_storage_backend()


def test_validate_storage_backend_rejects_relative_local_root(monkeypatch, tmp_path):
    """A relative BLOOM_LOCAL_ROOT resolves against the process's CWD — a
    restart from a different CWD would silently point at a different
    directory, making prior on-disk results unretrievable with no error at
    all. Caught at boot instead, before any mkdir."""
    monkeypatch.delenv("BLOOM_STORAGE_LOCAL_ROOT", raising=False)
    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "local")
    monkeypatch.setenv("BLOOM_LOCAL_ROOT", "relative/local/root")
    monkeypatch.chdir(tmp_path)
    sb.reset_backend_for_tests()
    with pytest.raises(RuntimeError, match="not an absolute path"):
        sb.validate_storage_backend()
    assert not (tmp_path / "relative").exists()  # fails before any mkdir


def test_validate_storage_backend_rejects_relative_explicit_override(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "local")
    monkeypatch.setenv("BLOOM_STORAGE_LOCAL_ROOT", "relative/output/root")
    monkeypatch.delenv("BLOOM_LOCAL_ROOT", raising=False)
    sb.reset_backend_for_tests()
    with pytest.raises(RuntimeError, match="not an absolute path"):
        sb.validate_storage_backend()


def test_validate_storage_backend_accepts_absolute_local_root(monkeypatch, tmp_path):
    monkeypatch.delenv("BLOOM_STORAGE_LOCAL_ROOT", raising=False)
    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "local")
    monkeypatch.setenv("BLOOM_LOCAL_ROOT", str(tmp_path))
    sb.reset_backend_for_tests()
    sb.validate_storage_backend()  # must not raise


# ─── 5d. bloom#593 — shared `fit_is_trustworthy` primitive ────────────────────
# Promoted from `remove_outliers.py`'s private `_fit_is_trustworthy`/
# `_UNTRUSTWORTHY_FIT` (#419) so `remove_outliers`'s live gate and
# `audit_untrustworthy_outlier_fits.py`'s retroactive scan (#593) share one
# definition. Direct unit tests here, in the primitive's new home — the same
# "test the promoted primitive directly, not only indirectly through a consumer"
# pattern `trim_staleness` (5c above) already established. A pure function of a
# dict; no manifest fixtures needed.


def test_fit_is_trustworthy_none_when_no_fit_report():
    """No `goodness_of_fit` at all (e.g. an `isolation_forest` trim) — nothing to
    trust or distrust."""
    from bloom_mcp import experiment_utils as eu

    assert eu.fit_is_trustworthy(None) is None


@pytest.mark.parametrize("fit_quality", sorted(["poor", "very_poor", "unknown"]))
def test_fit_is_trustworthy_false_for_untrustworthy_qualities(fit_quality):
    from bloom_mcp import experiment_utils as eu

    assert eu.fit_is_trustworthy({"fit_quality": fit_quality}) is False
    assert fit_quality in eu.UNTRUSTWORTHY_FIT_QUALITIES


@pytest.mark.parametrize("fit_quality", ["excellent", "good", "acceptable"])
def test_fit_is_trustworthy_true_for_acceptable_or_better(fit_quality):
    from bloom_mcp import experiment_utils as eu

    assert eu.fit_is_trustworthy({"fit_quality": fit_quality}) is True
    assert fit_quality not in eu.UNTRUSTWORTHY_FIT_QUALITIES


def test_fit_is_trustworthy_true_when_fit_quality_key_absent():
    """A dict missing the `fit_quality` key entirely reads as trustworthy (`None
    not in UNTRUSTWORTHY_FIT_QUALITIES` is `True`) — a documented, pre-existing
    corner (see the fit-gate proposal's design.md), not new behavior from this
    promotion."""
    from bloom_mcp import experiment_utils as eu

    assert eu.fit_is_trustworthy({}) is True


def test_fit_is_trustworthy_true_for_an_out_of_enum_fit_quality_value():
    """(#593 PR review) A `fit_quality` value outside the known tiers entirely —
    a typo, or a future delegate release adding a new tier this codebase doesn't
    know about yet — fails open (reads trustworthy) via the exact same membership
    check as the missing-key case above. Documented as a known false-negative
    risk (design.md Risks, `#593`'s `SCOPE_NOTE`) rather than a silent one."""
    from bloom_mcp import experiment_utils as eu

    assert eu.fit_is_trustworthy({"fit_quality": "moderate"}) is True


def test_remove_outliers_no_longer_defines_its_own_fit_trustworthy_primitives():
    """(#593) Symbol-relocation regression guard, mirroring #403's
    `test_role_pattern_lists_live_here_not_in_experiment_utils` (inverted
    direction): the whole point of promoting these to `experiment_utils` is a
    single source of truth, so a future accidental reintroduction of a local
    shadow copy in `remove_outliers.py` must be caught, not silently
    reintroducing the exact drift risk this promotion removes."""
    from bloom_mcp.sections.sleap_roots.analysis import remove_outliers

    assert not hasattr(remove_outliers, "_UNTRUSTWORTHY_FIT")
    assert not hasattr(remove_outliers, "_fit_is_trustworthy")
    # _REPORT_NAME/_TOOL_CLASS stay as local aliases (matching this file's
    # existing `_TOOL_CLASS = OUTLIERS_TOOL_CLASS` convention) — assert they
    # reference the single-sourced values, not a re-typed literal.
    from bloom_mcp import experiment_utils as eu

    assert remove_outliers._REPORT_NAME is eu.OUTLIER_REPORT_NAME
    assert remove_outliers.fit_is_trustworthy is eu.fit_is_trustworthy


# ─── 8. Signed URL generation (#581) ───────────────────────────────────────────
# Restored after a remote merge silently dropped this section (see PR #595
# review) -- extended with the review's own follow-up fixes in the same pass.


class _FakeSignClient:
    """Stand-in for the storage3 bucket client's create_signed_url method."""

    def __init__(self, response):
        self._response = response
        self.calls: list[tuple[str, int]] = []

    def create_signed_url(self, path, expires_in):
        self.calls.append((path, expires_in))
        return self._response


def test_supabase_create_signed_url_extracts_signedURL_key(monkeypatch):
    client = _FakeSignClient({"signedURL": "http://kong:8000/sign/a?token=x"})
    monkeypatch.setattr(
        "bloom_mcp.supabase_client.get_storage_client", lambda **_k: client
    )
    monkeypatch.delenv("BLOOM_PUBLIC_SUPABASE_URL", raising=False)

    url = sb.SupabaseStorageBackend().create_signed_url("k", 3600)

    assert client.calls == [("k", 3600)]
    assert url == "http://kong:8000/sign/a?token=x"


def test_supabase_create_signed_url_extracts_signedUrl_key_variant(monkeypatch):
    client = _FakeSignClient({"signedUrl": "http://kong:8000/sign/b?token=y"})
    monkeypatch.setattr(
        "bloom_mcp.supabase_client.get_storage_client", lambda **_k: client
    )
    monkeypatch.delenv("BLOOM_PUBLIC_SUPABASE_URL", raising=False)

    url = sb.SupabaseStorageBackend().create_signed_url("k", 3600)

    assert url == "http://kong:8000/sign/b?token=y"


def test_supabase_create_signed_url_extracts_signed_url_snake_case_variant(
    monkeypatch,
):
    # The third documented casing (signed_url) was never actually exercised by
    # a test before, even though the extraction fallback chain already checks
    # it -- review finding.
    client = _FakeSignClient({"signed_url": "http://kong:8000/sign/c?token=z"})
    monkeypatch.setattr(
        "bloom_mcp.supabase_client.get_storage_client", lambda **_k: client
    )
    monkeypatch.delenv("BLOOM_PUBLIC_SUPABASE_URL", raising=False)

    url = sb.SupabaseStorageBackend().create_signed_url("k", 3600)

    assert url == "http://kong:8000/sign/c?token=z"


def test_supabase_create_signed_url_raises_when_no_extractable_key(monkeypatch):
    client = _FakeSignClient({"error": "not found"})
    monkeypatch.setattr(
        "bloom_mcp.supabase_client.get_storage_client", lambda **_k: client
    )

    with pytest.raises(Exception):
        sb.SupabaseStorageBackend().create_signed_url("k", 3600)


def test_supabase_create_signed_url_raises_when_bare_string_response_is_empty(
    monkeypatch,
):
    # A bare "" response is falsy, not None -- the extraction and its caller
    # must both check falsiness, not just `is None`, or an empty string would
    # validate as a real OutputLink.url (review finding).
    client = _FakeSignClient("")
    monkeypatch.setattr(
        "bloom_mcp.supabase_client.get_storage_client", lambda **_k: client
    )

    with pytest.raises(Exception):
        sb.SupabaseStorageBackend().create_signed_url("k", 3600)


def test_supabase_create_signed_url_raises_when_dict_values_are_all_empty(
    monkeypatch,
):
    # A dict whose casing keys are all present but empty-stringed must fail
    # the same way a dict with no matching keys at all does.
    client = _FakeSignClient({"signedURL": "", "signed_url": "", "signedUrl": ""})
    monkeypatch.setattr(
        "bloom_mcp.supabase_client.get_storage_client", lambda **_k: client
    )

    with pytest.raises(Exception):
        sb.SupabaseStorageBackend().create_signed_url("k", 3600)


def test_supabase_create_signed_url_rewrites_internal_host_to_public_base(
    monkeypatch,
):
    client = _FakeSignClient(
        {"signedURL": "http://kong:8000/storage/v1/object/sign/bucket/k?token=x"}
    )
    monkeypatch.setattr(
        "bloom_mcp.supabase_client.get_storage_client", lambda **_k: client
    )
    monkeypatch.setenv("SUPABASE_URL", "http://kong:8000")
    monkeypatch.setenv("BLOOM_PUBLIC_SUPABASE_URL", "https://bloom.salk.edu/api")

    url = sb.SupabaseStorageBackend().create_signed_url("k", 3600)

    assert url == "https://bloom.salk.edu/api/storage/v1/object/sign/bucket/k?token=x"


def test_supabase_create_signed_url_rewrite_handles_trailing_slash_on_internal_host(
    monkeypatch,
):
    # The analogous local-backend case (BLOOM_STORAGE_URL with a trailing
    # slash) was already tested; this internal-host-rewrite path wasn't
    # (review finding).
    client = _FakeSignClient(
        {"signedURL": "http://kong:8000/storage/v1/object/sign/bucket/k?token=x"}
    )
    monkeypatch.setattr(
        "bloom_mcp.supabase_client.get_storage_client", lambda **_k: client
    )
    monkeypatch.setenv("SUPABASE_URL", "http://kong:8000/")  # trailing slash
    monkeypatch.setenv("BLOOM_PUBLIC_SUPABASE_URL", "https://bloom.salk.edu/api/")

    url = sb.SupabaseStorageBackend().create_signed_url("k", 3600)

    assert url == "https://bloom.salk.edu/api/storage/v1/object/sign/bucket/k?token=x"


def test_supabase_create_signed_url_no_rewrite_when_public_url_unset(monkeypatch):
    client = _FakeSignClient({"signedURL": "http://kong:8000/sign/k?token=x"})
    monkeypatch.setattr(
        "bloom_mcp.supabase_client.get_storage_client", lambda **_k: client
    )
    monkeypatch.setenv("SUPABASE_URL", "http://kong:8000")
    monkeypatch.delenv("BLOOM_PUBLIC_SUPABASE_URL", raising=False)

    url = sb.SupabaseStorageBackend().create_signed_url("k", 3600)

    assert url == "http://kong:8000/sign/k?token=x"


def test_supabase_create_signed_url_no_rewrite_when_supabase_url_unset(monkeypatch):
    client = _FakeSignClient({"signedURL": "http://kong:8000/sign/k?token=x"})
    monkeypatch.setattr(
        "bloom_mcp.supabase_client.get_storage_client", lambda **_k: client
    )
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.setenv("BLOOM_PUBLIC_SUPABASE_URL", "https://bloom.salk.edu/api")

    url = sb.SupabaseStorageBackend().create_signed_url("k", 3600)

    assert url == "http://kong:8000/sign/k?token=x"


def test_supabase_create_signed_url_no_rewrite_when_url_not_on_internal_host(
    monkeypatch,
):
    client = _FakeSignClient({"signedURL": "https://other.example/sign/k?token=x"})
    monkeypatch.setattr(
        "bloom_mcp.supabase_client.get_storage_client", lambda **_k: client
    )
    monkeypatch.setenv("SUPABASE_URL", "http://kong:8000")
    monkeypatch.setenv("BLOOM_PUBLIC_SUPABASE_URL", "https://bloom.salk.edu/api")

    url = sb.SupabaseStorageBackend().create_signed_url("k", 3600)

    assert url == "https://other.example/sign/k?token=x"


def test_supabase_create_signed_url_warns_when_internal_host_unrewritten(
    monkeypatch, caplog
):
    # A genuinely unrewritten internal-host URL is the one case where silence
    # would hide a real prod misconfiguration -- must log a warning. The
    # harmless no-op cases (URL not on the internal host, or SUPABASE_URL
    # itself unset) must NOT warn, or this would be noisy on every ordinary
    # call (review finding).
    client = _FakeSignClient({"signedURL": "http://kong:8000/sign/k?token=x"})
    monkeypatch.setattr(
        "bloom_mcp.supabase_client.get_storage_client", lambda **_k: client
    )
    monkeypatch.setenv("SUPABASE_URL", "http://kong:8000")
    monkeypatch.delenv("BLOOM_PUBLIC_SUPABASE_URL", raising=False)

    with caplog.at_level(logging.WARNING):
        sb.SupabaseStorageBackend().create_signed_url("k", 3600)

    assert any("BLOOM_PUBLIC_SUPABASE_URL" in r.getMessage() for r in caplog.records)


def test_supabase_create_signed_url_no_warning_when_url_not_on_internal_host(
    monkeypatch, caplog
):
    client = _FakeSignClient({"signedURL": "https://other.example/sign/k?token=x"})
    monkeypatch.setattr(
        "bloom_mcp.supabase_client.get_storage_client", lambda **_k: client
    )
    monkeypatch.setenv("SUPABASE_URL", "http://kong:8000")
    monkeypatch.delenv("BLOOM_PUBLIC_SUPABASE_URL", raising=False)

    with caplog.at_level(logging.WARNING):
        sb.SupabaseStorageBackend().create_signed_url("k", 3600)

    assert not caplog.records


def test_local_create_signed_url_returns_served_url(monkeypatch, tmp_path):
    monkeypatch.setenv("BLOOM_STORAGE_URL", "http://localhost:8811/output")
    url = sb.LocalStorageBackend(tmp_path).create_signed_url(
        "bloommcp_output/qc_x/v1/_cleaned.csv", 3600
    )
    assert url == "http://localhost:8811/output/bloommcp_output/qc_x/v1/_cleaned.csv"


def test_local_create_signed_url_strips_trailing_slash(monkeypatch, tmp_path):
    monkeypatch.setenv("BLOOM_STORAGE_URL", "http://localhost:8811/output/")
    url = sb.LocalStorageBackend(tmp_path).create_signed_url("k/f.csv", 3600)
    assert url == "http://localhost:8811/output/k/f.csv"


def test_local_create_signed_url_ignores_expires_in(monkeypatch, tmp_path):
    monkeypatch.setenv("BLOOM_STORAGE_URL", "http://localhost:8811/output")
    b = sb.LocalStorageBackend(tmp_path)
    assert b.create_signed_url("k", 60) == b.create_signed_url("k", 999999)


def test_local_create_signed_url_raises_when_unset_no_path_leak(monkeypatch, tmp_path):
    """Not called by the local backend's own output_links pipeline anymore
    (#642 follow-up — commit() surfaces a direct path instead), but the
    method itself still fails closed for an operator who deliberately calls
    it (or configures BLOOM_STORAGE_URL) without finishing the setup."""
    monkeypatch.delenv("BLOOM_STORAGE_URL", raising=False)
    with pytest.raises(Exception) as exc:
        sb.LocalStorageBackend(tmp_path).create_signed_url("k", 3600)
    msg = str(exc.value)
    assert str(tmp_path) not in msg
    assert "file://" not in msg


def test_storage_backend_protocol_includes_create_signed_url(tmp_path):
    assert isinstance(sb.SupabaseStorageBackend(), sb.StorageBackend)
    assert isinstance(sb.LocalStorageBackend(tmp_path), sb.StorageBackend)
    assert hasattr(sb.SupabaseStorageBackend, "create_signed_url")
    assert hasattr(sb.LocalStorageBackend, "create_signed_url")


# ─── 9. Local-mode self-serve base URL (#642) ──────────────────────────────────


def test_self_serve_base_url_defaults_to_localhost_8811(monkeypatch):
    monkeypatch.delenv("BLOOMMCP_PUBLIC_URL", raising=False)
    assert sb.self_serve_base_url() == "http://localhost:8811"


def test_self_serve_base_url_prefers_public_url(monkeypatch):
    monkeypatch.setenv("BLOOMMCP_PUBLIC_URL", "https://example.internal/")
    assert sb.self_serve_base_url() == "https://example.internal"


def test_create_signed_url_performs_no_ownership_check(monkeypatch):
    """#598: create_signed_url itself is a generic signing primitive with no
    concept of run/experiment ownership — it signs whatever syntactically
    valid key it's given. The actual scoping guarantee lives one layer up, in
    ResultStore.commit() (see test_supabase_result_store.py /
    test_fake_result_store.py's key-scoping tests) — this test documents,
    rather than changes, the primitive's existing unguarded behavior."""
    client = _FakeSignClient({"signedURL": "http://kong:8000/sign/x?token=z"})
    monkeypatch.setattr(
        "bloom_mcp.supabase_client.get_storage_client", lambda **_k: client
    )
    monkeypatch.delenv("BLOOM_PUBLIC_SUPABASE_URL", raising=False)

    # A key with no relation to any run this backend knows about — not
    # rejected on ownership/scope grounds.
    url = sb.SupabaseStorageBackend().create_signed_url(
        "bloommcp_output/qc_someone_elses_experiment/v99/secret.csv", 3600
    )

    assert url == "http://kong:8000/sign/x?token=z"
    assert client.calls == [
        ("bloommcp_output/qc_someone_elses_experiment/v99/secret.csv", 3600)
    ]


def test_supabase_client_reexports_create_signed_url(monkeypatch):
    import bloom_mcp.storage_backend as sb_module
    import bloom_mcp.supabase_client as sc

    captured = {}

    class _FakeBackend:
        def create_signed_url(self, key, expires_in):
            captured["args"] = (key, expires_in)
            return "http://x/signed"

    monkeypatch.setattr(sb_module, "active_backend", lambda: _FakeBackend())
    assert sc.create_signed_url("k", 3600) == "http://x/signed"
    assert captured["args"] == ("k", 3600)


# ─── 9. Object byte size lookup (#599) ─────────────────────────────────────────


class _FakeInfoClient:
    """Stand-in for the storage3 bucket client's `info()` method."""

    def __init__(self, response):
        self._response = response
        self.calls: list[str] = []

    def info(self, path):
        self.calls.append(path)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def test_supabase_get_object_size_extracts_nested_metadata_size(monkeypatch):
    # The real (and only typed-sibling-confirmed) shape: size nested under
    # `metadata` (design.md Decision 2).
    client = _FakeInfoClient({"name": "k", "metadata": {"size": 1234}})
    monkeypatch.setattr(
        "bloom_mcp.supabase_client.get_storage_client", lambda **_k: client
    )

    size = sb.SupabaseStorageBackend().get_object_size("k")

    assert client.calls == ["k"]
    assert size == 1234


def test_supabase_get_object_size_extracts_flat_size_fallback(monkeypatch):
    # A flat top-level `size` is accepted as a fallback in case a future
    # client version flattens the response.
    client = _FakeInfoClient({"name": "k", "size": 5678})
    monkeypatch.setattr(
        "bloom_mcp.supabase_client.get_storage_client", lambda **_k: client
    )

    size = sb.SupabaseStorageBackend().get_object_size("k")

    assert size == 5678


def test_supabase_get_object_size_raises_when_size_field_missing(monkeypatch):
    client = _FakeInfoClient({"name": "k", "metadata": {}})
    monkeypatch.setattr(
        "bloom_mcp.supabase_client.get_storage_client", lambda **_k: client
    )

    with pytest.raises(Exception):
        sb.SupabaseStorageBackend().get_object_size("k")


def test_supabase_get_object_size_raises_when_size_is_non_numeric(monkeypatch):
    client = _FakeInfoClient({"name": "k", "metadata": {"size": "not-a-number"}})
    monkeypatch.setattr(
        "bloom_mcp.supabase_client.get_storage_client", lambda **_k: client
    )

    with pytest.raises(Exception):
        sb.SupabaseStorageBackend().get_object_size("k")


def test_supabase_get_object_size_raises_when_size_is_negative(monkeypatch):
    client = _FakeInfoClient({"name": "k", "metadata": {"size": -1}})
    monkeypatch.setattr(
        "bloom_mcp.supabase_client.get_storage_client", lambda **_k: client
    )

    with pytest.raises(Exception):
        sb.SupabaseStorageBackend().get_object_size("k")


def test_supabase_get_object_size_propagates_client_raise_unmodified(monkeypatch):
    # `client.info()` itself raising for a missing/deleted object propagates
    # as-is -- matching this class's own download_file/read_json, neither of
    # which wraps a missing-key failure into a bloommcp-defined type either.
    client = _FakeInfoClient(KeyError("object not found: k"))
    monkeypatch.setattr(
        "bloom_mcp.supabase_client.get_storage_client", lambda **_k: client
    )

    with pytest.raises(KeyError):
        sb.SupabaseStorageBackend().get_object_size("k")


def test_supabase_get_object_size_performs_no_ownership_check(monkeypatch):
    # A syntactically valid key belonging to a different experiment/tool_class
    # succeeds with no authorization error -- restricting scope is the
    # caller's responsibility (identically to create_signed_url).
    client = _FakeInfoClient({"name": "other", "metadata": {"size": 42}})
    monkeypatch.setattr(
        "bloom_mcp.supabase_client.get_storage_client", lambda **_k: client
    )

    size = sb.SupabaseStorageBackend().get_object_size(
        "bloommcp_output/qc_someone_elses_experiment/v1/_cleaned.csv"
    )

    assert size == 42


def test_local_get_object_size_returns_real_stat_size(tmp_path):
    b = _local(tmp_path)
    src = _seed_file(tmp_path, b"0123456789")
    b.upload_file("bloommcp_output/x/v1/f.csv", src)

    assert b.get_object_size("bloommcp_output/x/v1/f.csv") == 10


def test_local_get_object_size_missing_key_raises_storage_key_not_found(tmp_path):
    b = _local(tmp_path)
    with pytest.raises(sb.StorageKeyNotFound) as exc:
        b.get_object_size("bloommcp_output/x/missing.csv")
    assert str(tmp_path) not in str(exc.value)
    assert "bloommcp_output/x/missing.csv" in str(exc.value)


def test_storage_backend_protocol_includes_get_object_size(tmp_path):
    assert hasattr(sb.SupabaseStorageBackend, "get_object_size")
    assert hasattr(sb.LocalStorageBackend, "get_object_size")
    assert isinstance(sb.SupabaseStorageBackend(), sb.StorageBackend)
    assert isinstance(sb.LocalStorageBackend(tmp_path), sb.StorageBackend)


def test_supabase_client_reexports_get_object_size(monkeypatch):
    import bloom_mcp.storage_backend as sb_module
    import bloom_mcp.supabase_client as sc

    captured = {}

    class _FakeBackend:
        def get_object_size(self, key):
            captured["key"] = key
            return 999

    monkeypatch.setattr(sb_module, "active_backend", lambda: _FakeBackend())
    assert sc.get_object_size("k") == 999
    assert captured["key"] == "k"


def test_get_object_size_real_dispatch_through_active_backend(monkeypatch):
    """Exercises get_object_size through genuine `active_backend()` dispatch
    (task 1.6) -- not only through the always-faked `_sc`-module fixtures,
    matching this file's existing real-dispatch convention
    (`_FakeSbStorageClient` patches only `get_storage_client`, letting the
    real `SupabaseStorageBackend` class run through the real dispatch path).
    """
    sb.reset_backend_for_tests()
    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "supabase")
    fake_client = _FakeSbStorageClient()
    fake_client.objects["bloommcp_output/qc_x/v1/f.csv"] = b"0123456789ab"
    monkeypatch.setattr(
        "bloom_mcp.supabase_client.get_storage_client", lambda **_k: fake_client
    )

    import bloom_mcp.supabase_client as sc

    size = sc.get_object_size("bloommcp_output/qc_x/v1/f.csv")

    assert size == 12
    sb.reset_backend_for_tests()
