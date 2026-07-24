"""SupabaseResultStore adapter — exercised on the in-memory storage boundary."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from bloom_mcp.contract import Provenance
from bloom_mcp.result_store import CommitFailedError, SupabaseResultStore
from bloom_mcp.manifest import AnalysisDir
from bloom_mcp.manifest.schema import CodeVersions

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _prov(seed: int = 7) -> Provenance:
    return Provenance.stamp(tool="run_qc_workflow", params={"n": 3}, seed=seed)


def _prov_full() -> Provenance:
    return Provenance(
        tool="run_qc_workflow",
        params={"n": 3},
        seed=123,
        agent="bloom_agent",
        code_versions=CodeVersions(bloommcp="0.1.0", sleap_roots_analyze="0.1.0a2"),
        environment="sha256:deadbeef",
    )


def test_commit_persists_provenance_and_hashes_uploaded_bytes(fake_supabase_storage):
    store = SupabaseResultStore()
    run = store.create_run(experiment="exp.csv", tool_class="qc", provenance=_prov())

    payload = b"a,b\n1,2\n"
    (run.staging_dir / "_cleaned.csv").write_bytes(payload)
    stored = store.commit(run, {"cleaned": "_cleaned.csv"})

    assert stored.run_ref == "v1"
    assert stored.seed == 7
    assert stored.agent == "bloom_agent"
    # Hash is over the exact uploaded bytes (not an ETag), key is logical.
    key = stored.output_keys["cleaned"]
    assert stored.output_sha256["cleaned"] == hashlib.sha256(payload).hexdigest()
    assert key == f"bloommcp_output/qc_exp/{stored.version_dir}/_cleaned.csv"
    assert fake_supabase_storage.objects[key] == payload

    # Manifest advanced and re-resolvable.
    got = store.get_run("exp.csv", "qc", "latest")
    assert got.run_ref == "v1"
    assert got.seed == 7


def test_v2_manifest_backcompat(fake_supabase_storage):
    v2 = json.loads((_FIXTURES / "manifest_v2.json").read_text())
    adir = AnalysisDir("bloommcp_output", "turface_19_final_data.csv", "qc")
    fake_supabase_storage.objects[f"{adir.path}manifest.json"] = json.dumps(v2).encode()

    store = SupabaseResultStore()
    runs = store.list_runs("turface_19_final_data.csv", "qc")
    assert runs[0].run_ref == "v1"
    assert runs[0].seed is None  # v2 had no seed
    assert runs[0].output_sha256 == {}

    # A new commit appends a v3 entry alongside the v2 one.
    run = store.create_run(
        experiment="turface_19_final_data.csv", tool_class="qc", provenance=_prov()
    )
    (run.staging_dir / "o.csv").write_bytes(b"x")
    store.commit(run, {"o": "o.csv"})
    assert [r.run_ref for r in store.list_runs("turface_19_final_data.csv", "qc")] == [
        "v1",
        "v2",
    ]


def test_full_provenance_round_trips_through_commit(fake_supabase_storage):
    store = SupabaseResultStore()
    run = store.create_run(
        experiment="exp.csv", tool_class="qc", provenance=_prov_full()
    )
    (run.staging_dir / "o.csv").write_bytes(b"x")
    store.commit(run, {"o": "o.csv"})

    got = store.get_run("exp.csv", "qc", "latest")
    assert got.seed == 123
    assert got.agent == "bloom_agent"
    assert got.environment == "sha256:deadbeef"
    assert got.code_versions["bloommcp"] == "0.1.0"
    assert got.code_versions["sleap_roots_analyze"] == "0.1.0a2"


def test_input_sha256_lands_on_experiment_block_not_version_entry(
    fake_supabase_storage, tmp_path
):
    src = tmp_path / "exp.csv"
    src.write_bytes(b"col\n1\n")
    store = SupabaseResultStore()
    run = store.create_run(
        experiment="exp.csv", tool_class="qc", provenance=_prov(), source_csv=src
    )
    (run.staging_dir / "o.csv").write_bytes(b"x")
    store.commit(run, {"o": "o.csv"})

    manifest = json.loads(
        fake_supabase_storage.objects["bloommcp_output/qc_exp/manifest.json"]
    )
    assert (
        manifest["experiment"]["input_sha256"]
        == hashlib.sha256(b"col\n1\n").hexdigest()
    )
    assert "input_sha256" not in manifest["versions"][0]


def test_empty_outputs_rejected(fake_supabase_storage):
    store = SupabaseResultStore()
    run = store.create_run(experiment="exp.csv", tool_class="qc", provenance=_prov())
    with pytest.raises(ValueError):
        store.commit(run, {})


def test_commit_failure_is_retryable_and_does_not_leak(
    fake_supabase_storage, monkeypatch
):
    import bloom_mcp.supabase_client as sc

    store = SupabaseResultStore()
    run = store.create_run(experiment="exp.csv", tool_class="qc", provenance=_prov())
    (run.staging_dir / "o.csv").write_bytes(b"x")

    real_upload = sc.upload_file
    calls = {"n": 0}

    def _flaky(key, path):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError(
                "network down: https://proj.supabase.co/storage/v1/object/secret"
            )
        return real_upload(key, path)

    monkeypatch.setattr(sc, "upload_file", _flaky)

    with pytest.raises(CommitFailedError) as excinfo:
        store.commit(run, {"o": "o.csv"})
    msg = str(excinfo.value)
    assert "supabase" not in msg.lower()
    assert "http" not in msg.lower()
    assert "network down" not in msg

    # Failure is recoverable: manifest un-advanced, staging retained, handle live.
    assert store.list_runs("exp.csv", "qc") == []
    assert run.staging_dir.exists()

    # Retry on the same handle succeeds and then cleans up.
    stored = store.commit(run, {"o": "o.csv"})
    assert stored.run_ref == "v1"
    assert store.get_run("exp.csv", "qc", "latest").run_ref == "v1"
    assert not run.staging_dir.exists()


def test_commit_failure_cleans_up_orphaned_objects_from_partial_upload(
    fake_supabase_storage, monkeypatch
):
    """#324 gap A: a 2-output run failing on the 2nd upload leaves no orphan."""
    import bloom_mcp.supabase_client as sc

    store = SupabaseResultStore()
    run = store.create_run(experiment="exp.csv", tool_class="qc", provenance=_prov())
    (run.staging_dir / "a.csv").write_bytes(b"a")
    (run.staging_dir / "b.csv").write_bytes(b"b")

    real_upload = sc.upload_file

    def _fail_second_upload(key, path):
        if Path(path).name == "b.csv":
            raise RuntimeError("network down mid-upload")
        return real_upload(key, path)

    monkeypatch.setattr(sc, "upload_file", _fail_second_upload)

    with pytest.raises(CommitFailedError):
        store.commit(run, {"a": "a.csv", "b": "b.csv"})

    assert store.list_runs("exp.csv", "qc") == []  # latest un-advanced
    # The object from the succeeded first upload was deleted — no orphan.
    assert not any(k.endswith("a.csv") for k in fake_supabase_storage.objects)


def test_cleanup_failure_does_not_mask_original_error(
    fake_supabase_storage, monkeypatch, caplog
):
    """#324 gap A: a failed delete-on-cleanup still surfaces the original error."""
    import logging

    import bloom_mcp.supabase_client as sc

    store = SupabaseResultStore()
    run = store.create_run(experiment="exp.csv", tool_class="qc", provenance=_prov())
    (run.staging_dir / "a.csv").write_bytes(b"a")
    (run.staging_dir / "b.csv").write_bytes(b"b")

    real_upload = sc.upload_file

    def _fail_second_upload(key, path):
        if Path(path).name == "b.csv":
            raise RuntimeError("network down mid-upload")
        return real_upload(key, path)

    def _boom_delete(keys):
        raise RuntimeError("delete also failed: https://proj.supabase.co/secret")

    monkeypatch.setattr(sc, "upload_file", _fail_second_upload)
    monkeypatch.setattr(sc, "delete_files", _boom_delete)

    with caplog.at_level(logging.WARNING):
        with pytest.raises(CommitFailedError) as excinfo:
            store.commit(run, {"a": "a.csv", "b": "b.csv"})

    msg = str(excinfo.value)
    assert "delete also failed" not in msg
    assert "supabase" not in msg.lower()
    assert any("cleanup failed" in r.getMessage().lower() for r in caplog.records)


def test_interleaved_commits_get_distinct_ids_with_consistent_provenance(
    fake_supabase_storage,
):
    """#324 gap B: colliding commits never clobber each other's stored bytes."""
    store = SupabaseResultStore()
    run1 = store.create_run(
        experiment="exp.csv", tool_class="qc", provenance=_prov(seed=1)
    )
    run2 = store.create_run(
        experiment="exp.csv", tool_class="qc", provenance=_prov(seed=2)
    )
    assert (
        run1.version_id == run2.version_id == "v1"
    )  # both saw the same empty manifest

    (run1.staging_dir / "o.csv").write_bytes(b"first")
    (run2.staging_dir / "o.csv").write_bytes(b"second")

    stored1 = store.commit(run1, {"o": "o.csv"})  # lands first, fully completes
    stored2 = store.commit(run2, {"o": "o.csv"})  # collides, reallocates to v2

    assert stored1.run_ref == "v1"
    assert stored2.run_ref == "v2"
    assert stored1.version_dir != stored2.version_dir
    assert [r.run_ref for r in store.list_runs("exp.csv", "qc")] == ["v1", "v2"]

    for stored, expected in ((stored1, b"first"), (stored2, b"second")):
        key = stored.output_keys["o"]
        assert stored.version_dir in key  # id/version_dir/key stay in lockstep
        assert fake_supabase_storage.objects[key] == expected  # not overwritten
        assert stored.output_sha256["o"] == hashlib.sha256(expected).hexdigest()


def test_retry_exhaustion_before_upload_raises_with_no_uploads(
    fake_supabase_storage, monkeypatch
):
    """#324 gap B: every reallocation attempt colliding fails cheaply, pre-upload."""
    import bloom_mcp.result_store.supabase_store as _store_mod
    import bloom_mcp.supabase_client as sc

    store = SupabaseResultStore()
    run = store.create_run(experiment="exp.csv", tool_class="qc", provenance=_prov())
    (run.staging_dir / "o.csv").write_bytes(b"x")

    adir = run._backend.adir
    fake_supabase_storage.objects[f"{adir.path}manifest.json"] = json.dumps(
        {
            "manifest_schema_version": 3,
            "experiment": {
                "filename": "exp.csv",
                "source_path": "",
                "input_sha256": "",
            },
            "versions": [
                {
                    "id": run.version_id,
                    "created_at": "2026-01-01T00:00:00Z",
                    "tool": "other",
                    "params": {},
                    "based_on_version": "raw",
                    "code_versions": {},
                    "outputs": {},
                }
            ],
            "latest": run.version_id,
        }
    ).encode()

    # Every reallocation attempt still resolves to the same, still-colliding
    # id — simulates a burst of writers claiming every id this commit tries.
    monkeypatch.setattr(_store_mod, "next_version_id", lambda manifest: run.version_id)

    upload_calls = {"n": 0}
    real_upload = sc.upload_file

    def _counting_upload(key, path):
        upload_calls["n"] += 1
        return real_upload(key, path)

    monkeypatch.setattr(sc, "upload_file", _counting_upload)

    with pytest.raises(CommitFailedError):
        store.commit(run, {"o": "o.csv"})

    assert upload_calls["n"] == 0  # exhausted before ever uploading
    assert store.list_runs("exp.csv", "qc")[0].run_ref == run.version_id  # untouched


def test_prewrite_collision_cleans_up_and_retry_succeeds(
    fake_supabase_storage, monkeypatch
):
    """#324 gap B: a collision that appears mid-upload fails safely and retries clean."""
    from bloom_mcp.manifest.schema import ExperimentBlock, Manifest, VersionEntry

    store = SupabaseResultStore()
    run = store.create_run(experiment="exp.csv", tool_class="qc", provenance=_prov())
    (run.staging_dir / "o.csv").write_bytes(b"x")

    adir = run._backend.adir
    real_read_manifest = adir.read_manifest
    calls = {"n": 0}

    def _flaky_read():
        calls["n"] += 1
        result = real_read_manifest()
        if calls["n"] == 2:
            # Simulate a second writer's commit landing between this commit's
            # pre-upload and pre-write reads, claiming the same version_id.
            interloper = VersionEntry(
                id=run.version_id,
                created_at="2026-01-01T00:00:00Z",
                tool="other",
                params={},
                based_on_version="raw",
                code_versions=CodeVersions(),
                outputs={},
                version_dir="interloper_dir",
            )
            if result is None:
                result = Manifest(
                    experiment=ExperimentBlock(
                        filename=adir.experiment_filename,
                        source_path="",
                        input_sha256="",
                    ),
                    versions=[interloper],
                    latest=interloper.id,
                )
            else:
                result.versions.append(interloper)
                result.latest = interloper.id
        return result

    monkeypatch.setattr(adir, "read_manifest", _flaky_read)

    with pytest.raises(CommitFailedError):
        store.commit(run, {"o": "o.csv"})

    # Nothing this attempt uploaded survives the collision.
    assert not any(k.endswith("o.csv") for k in fake_supabase_storage.objects)

    # The injected interloper only ever existed in the stub's return value —
    # the real (fake) manifest was never touched, so a retry finds a genuinely
    # free id and succeeds.
    stored = store.commit(run, {"o": "o.csv"})
    assert stored.run_ref == run.version_id
    assert store.get_run("exp.csv", "qc", "latest").run_ref == run.version_id


def test_noncolliding_commit_reads_manifest_twice_with_no_reallocation(
    fake_supabase_storage, monkeypatch
):
    """The non-colliding path costs exactly two manifest reads, zero retries."""
    import bloom_mcp.result_store.supabase_store as _store_mod

    store = SupabaseResultStore()
    run = store.create_run(experiment="exp.csv", tool_class="qc", provenance=_prov())
    (run.staging_dir / "o.csv").write_bytes(b"x")

    adir = run._backend.adir
    real_read_manifest = adir.read_manifest
    read_calls = {"n": 0}

    def _counting_read():
        read_calls["n"] += 1
        return real_read_manifest()

    monkeypatch.setattr(adir, "read_manifest", _counting_read)

    real_next_version_id = _store_mod.next_version_id
    reallocations = {"n": 0}

    def _counting_next(manifest):
        reallocations["n"] += 1
        return real_next_version_id(manifest)

    monkeypatch.setattr(_store_mod, "next_version_id", _counting_next)

    stored = store.commit(run, {"o": "o.csv"})

    assert stored.run_ref == run.version_id
    assert read_calls["n"] == 2  # pre-upload check + pre-write check, nothing more
    assert reallocations["n"] == 0
