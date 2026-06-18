"""SupabaseResultStore adapter — exercised on the in-memory storage boundary."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from bloom_mcp.contract import Provenance
from bloom_mcp.result_store import CommitFailedError, SupabaseResultStore
from bloom_mcp.storage import AnalysisDir
from bloom_mcp.storage.schema import CodeVersions

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
