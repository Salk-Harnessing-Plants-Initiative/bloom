"""SupabaseResultStore adapter — exercised on the in-memory storage boundary."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from bloom_mcp.contract import Provenance
from bloom_mcp.result_store import CommitFailedError, SupabaseResultStore
from bloom_mcp.storage import AnalysisDir

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _prov(seed: int = 7) -> Provenance:
    return Provenance.stamp(tool="run_qc_workflow", params={"n": 3}, seed=seed)


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


def test_commit_failure_cleans_up_and_does_not_advance_manifest(
    fake_supabase_storage, monkeypatch
):
    import bloom_mcp.supabase_client as sc

    store = SupabaseResultStore()
    run = store.create_run(experiment="exp.csv", tool_class="qc", provenance=_prov())
    (run.staging_dir / "o.csv").write_bytes(b"x")

    def _boom(*_a, **_k):
        raise RuntimeError("network down")

    monkeypatch.setattr(sc, "upload_file", _boom)
    with pytest.raises(CommitFailedError):
        store.commit(run, {"o": "o.csv"})

    assert store.list_runs("exp.csv", "qc") == []  # manifest not advanced
    assert not run.staging_dir.exists()  # staging cleaned
