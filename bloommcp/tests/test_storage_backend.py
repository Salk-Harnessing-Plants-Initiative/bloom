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

    # real files on disk, laid out by key
    out = tmp_path / "bloommcp_output" / "qc_exp"
    assert (out / "manifest.json").is_file()
    assert (out / stored.version_dir / "_cleaned.csv").read_bytes() == b"data"


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


def _write_cleaned_manifest(
    tmp_path: Path,
    stem: str,
    tool_class: str,
    version_id: str,
    created_at: str,
    content: bytes,
) -> None:
    """Write a valid one-version manifest + its cleaned CSV under `<tool_class>_<stem>/`."""
    from bloom_mcp.manifest import (
        ExperimentBlock,
        Manifest,
        VersionEntry,
        get_code_versions,
        write_manifest,
    )
    from bloom_mcp.supabase_client import upload_file

    prefix = f"bloommcp_output/{tool_class}_{stem}/"
    version_dir = f"{version_id}_2026-07-06"
    src = tmp_path / f"{tool_class}_{version_id}_seed.csv"
    src.write_bytes(content)
    upload_file(f"{prefix}{version_dir}/_cleaned.csv", src)

    entry = VersionEntry(
        id=version_id,
        created_at=created_at,
        tool="qc_clean" if tool_class == "qc" else "remove_outliers",
        params={},
        based_on_version="raw" if tool_class == "qc" else f"{version_id}_cleaned",
        code_versions=get_code_versions(),
        outputs={"_cleaned.csv": "_cleaned.csv"},
        version_dir=version_dir,
    )
    manifest = Manifest(
        experiment=ExperimentBlock(
            filename=f"{stem}.csv", source_path="", input_sha256=""
        ),
        versions=[entry],
        latest=version_id,
    )
    write_manifest(prefix, manifest)


def _write_invalid_schema_manifest(stem: str, tool_class: str) -> None:
    """Write a manifest.json whose schema version is newer than this code understands."""
    from bloom_mcp.supabase_client import write_json

    prefix = f"bloommcp_output/{tool_class}_{stem}/"
    write_json(f"{prefix}manifest.json", {"manifest_schema_version": 999})


@pytest.fixture
def _local_backend(monkeypatch, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "local")
    monkeypatch.setenv("BLOOM_STORAGE_LOCAL_ROOT", str(root))
    sb.reset_backend_for_tests()
    return tmp_path


def test_latest_resolves_qc_only_unqualified(_local_backend):
    """(a) Only `qc` has a version — `version="latest"` resolves it, today's exact
    unqualified label, unchanged. The overwhelmingly common (never-trimmed) case must
    see zero observable change from this fix."""
    from bloom_mcp import experiment_utils as eu

    tmp_path = _local_backend
    _write_cleaned_manifest(
        tmp_path, "exp", "qc", "v1", "2026-07-06T00:00:00Z", b"a,b\n1,2\n"
    )

    path, label, err = eu._resolve_versioned_cleaned(eu.OUTPUT_DIR, "exp", "latest")
    assert err is None
    assert path is not None and path.read_bytes() == b"a,b\n1,2\n"
    assert label == "v1_cleaned"


def test_latest_resolves_outliers_only_qualified(_local_backend):
    """(b) Only `outliers` has a version — `version="latest"` resolves it, with the
    tool-class-qualified label."""
    from bloom_mcp import experiment_utils as eu

    tmp_path = _local_backend
    _write_cleaned_manifest(
        tmp_path, "exp", "outliers", "v1", "2026-07-06T00:00:00Z", b"a,b\n3,4\n"
    )

    path, label, err = eu._resolve_versioned_cleaned(eu.OUTPUT_DIR, "exp", "latest")
    assert err is None
    assert path is not None and path.read_bytes() == b"a,b\n3,4\n"
    assert label == "outliers_v1_cleaned"


def test_latest_prefers_outliers_regardless_of_recency(_local_backend):
    """(c) The actual #420 repro: `qc`'s entry is committed (and timestamped) LATER
    than `outliers`'s, yet `version="latest"` must still resolve `outliers` — proving
    this is a fixed priority, not a recency comparison. An earlier (wrong) draft of
    this fix compared `created_at` across classes and would resolve `qc` here."""
    from bloom_mcp import experiment_utils as eu

    tmp_path = _local_backend
    _write_cleaned_manifest(
        tmp_path, "exp", "outliers", "v1", "2026-07-06T00:00:00Z", b"trim,ok\n1,1\n"
    )
    _write_cleaned_manifest(
        tmp_path, "exp", "qc", "v2", "2026-07-06T23:59:59Z", b"untrimmed\n9\n"
    )

    path, label, err = eu._resolve_versioned_cleaned(eu.OUTPUT_DIR, "exp", "latest")
    assert err is None
    assert path is not None and path.read_bytes() == b"trim,ok\n1,1\n"
    assert label == "outliers_v1_cleaned"


def test_latest_qc_resolves_qc_ignoring_outliers(_local_backend):
    """(d) `version="latest_qc"` resolves the `qc` class specifically, even when a
    newer-looking `outliers` version exists — this is what `remove_outliers` itself
    reads as its trimming input."""
    from bloom_mcp import experiment_utils as eu

    tmp_path = _local_backend
    _write_cleaned_manifest(
        tmp_path, "exp", "outliers", "v1", "2026-07-06T00:00:00Z", b"trim,ok\n1,1\n"
    )
    _write_cleaned_manifest(
        tmp_path, "exp", "qc", "v2", "2026-07-06T23:59:59Z", b"untrimmed\n9\n"
    )

    path, label, err = eu._resolve_versioned_cleaned(eu.OUTPUT_DIR, "exp", "latest_qc")
    assert err is None
    assert path is not None and path.read_bytes() == b"untrimmed\n9\n"
    assert label == "v2_cleaned"  # unqualified — same format as version="latest_qc"


def test_latest_qc_resolves_qc_only_unqualified(_local_backend):
    """(e) `version="latest_qc"` with no `outliers` class at all resolves `qc`, with
    the same unqualified label as (a) — confirms `latest_qc` isn't a no-op alias that
    silently means something else when `outliers` is absent."""
    from bloom_mcp import experiment_utils as eu

    tmp_path = _local_backend
    _write_cleaned_manifest(
        tmp_path, "exp", "qc", "v1", "2026-07-06T00:00:00Z", b"a,b\n1,2\n"
    )

    path, label, err = eu._resolve_versioned_cleaned(eu.OUTPUT_DIR, "exp", "latest_qc")
    assert err is None
    assert path is not None and path.read_bytes() == b"a,b\n1,2\n"
    assert label == "v1_cleaned"


def test_latest_schema_error_on_outliers_propagates_first_iteration(_local_backend):
    """(f) A schema error on `outliers` (checked first, higher priority) propagates
    immediately — it is not swallowed and does not fall through to the valid `qc`
    manifest."""
    from bloom_mcp import experiment_utils as eu

    tmp_path = _local_backend
    _write_cleaned_manifest(
        tmp_path, "exp", "qc", "v1", "2026-07-06T00:00:00Z", b"a,b\n1,2\n"
    )
    _write_invalid_schema_manifest("exp", "outliers")

    path, label, err = eu._resolve_versioned_cleaned(eu.OUTPUT_DIR, "exp", "latest")
    assert path is None
    assert label is None
    assert err is not None and "manifest schema error for 'exp'" in err


def test_latest_schema_error_on_qc_propagates_second_iteration(_local_backend):
    """(g) The mirror of (f): `outliers` has no entry at all (resolves to "no entry",
    not an error) and `qc` fails schema validation — the error must still propagate
    once the loop reaches its second iteration, not be silently dropped by an
    over-broad `except`/`continue` around the whole loop."""
    from bloom_mcp import experiment_utils as eu

    _write_invalid_schema_manifest("exp", "qc")

    path, label, err = eu._resolve_versioned_cleaned(eu.OUTPUT_DIR, "exp", "latest")
    assert path is None
    assert label is None
    assert err is not None and "manifest schema error for 'exp'" in err


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
