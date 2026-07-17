"""Tests for the object-storage backend seam (bloommcp-storage-backend).

Covers backend selection (`BLOOM_STORAGE_BACKEND`), the local filesystem backend
(key→path mapping, listing, escape guard, overwrite, verbatim bytes, atomic
writes, redacted errors), root resolution + boot-time validation, and
parity/integrity (byte-identical manifest, hash-equality on disk, a workflow
round-trip under `local`, the default-writes-no-local-files guard, and
legacy-fallback disjointness). No live Supabase.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from bloom_mcp import storage_backend as sb

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "turface_19_final_data.csv"


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
        "bloom_mcp.supabase_client.get_storage_client", lambda: _FakeClient()
    )

    backend = sb.SupabaseStorageBackend()
    backend.delete_files(
        ["bloommcp_output/qc_x/v1/a.csv", "bloommcp_output/qc_x/v1/b.csv"]
    )
    assert calls == [["bloommcp_output/qc_x/v1/a.csv", "bloommcp_output/qc_x/v1/b.csv"]]


def test_supabase_backend_delete_files_empty_list_skips_client(monkeypatch):
    def _boom():
        raise AssertionError("get_storage_client called for an empty delete")

    monkeypatch.setattr("bloom_mcp.supabase_client.get_storage_client", _boom)
    sb.SupabaseStorageBackend().delete_files([])  # must not raise / not call the client


# ─── 3. Root resolution + startup validation ──────────────────────────────────


def test_root_prefers_dedicated_var(monkeypatch, tmp_path):
    monkeypatch.setenv("BLOOM_STORAGE_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setenv("BLOOM_OUTPUT_DIR", str(tmp_path / "other"))
    assert sb._resolve_local_root() == tmp_path


def test_root_falls_back_to_output_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("BLOOM_STORAGE_LOCAL_ROOT", raising=False)
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


@pytest.fixture
def local_workflow_env(monkeypatch, tmp_path):
    """Real reader/store + local backend + a seeded raw input for a full workflow."""
    from bloom_mcp import experiment_utils as eu
    from bloom_mcp.data_access import SupabaseReader
    from bloom_mcp.result_store import SupabaseResultStore
    from bloom_mcp.tools import _ports

    root = tmp_path / "store"
    root.mkdir()
    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "local")
    monkeypatch.setenv("BLOOM_STORAGE_LOCAL_ROOT", str(root))
    sb.reset_backend_for_tests()

    traits = tmp_path / "traits"
    traits.mkdir()
    shutil.copy(_FIXTURE, traits / "turface.csv")
    monkeypatch.setattr(eu, "TRAITS_DIR", traits)

    _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())
    try:
        yield root
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())


def test_qc_workflow_local_roundtrip_with_hash_equality(local_workflow_env):
    """A qc workflow under BLOOM_STORAGE_BACKEND=local writes real files, reads
    back through _resolve_versioned_cleaned, and each on-disk hash matches the
    recorded output_sha256."""
    from bloom_mcp.data_access import SupabaseReader
    from bloom_mcp.tools.workflows.qc import run_qc_workflow

    root = local_workflow_env
    resp = run_qc_workflow("turface.csv")
    assert "error" not in resp, resp
    assert resp["version_id"] == "v1"

    out = root / "bloommcp_output" / "qc_turface"
    assert (out / "manifest.json").is_file()
    manifest = json.loads((out / "manifest.json").read_bytes())
    entry = manifest["versions"][-1]
    assert (out / entry["version_dir"] / "_cleaned.csv").is_file()

    # hash-equality: the bytes on disk match the recorded provenance hash
    for name, sha in entry["output_sha256"].items():
        key = entry["output_keys"][name]  # logical bloommcp_output/... key
        on_disk = (root / key).read_bytes()
        assert hashlib.sha256(on_disk).hexdigest() == sha

    # read-back exercises the download_file local leg via _resolve_versioned_cleaned
    frame = SupabaseReader().load_experiment("turface.csv", version="latest")
    assert frame.source.startswith("v1")
    assert len(frame.df) > 0


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
    from bloom_mcp.storage import (
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
