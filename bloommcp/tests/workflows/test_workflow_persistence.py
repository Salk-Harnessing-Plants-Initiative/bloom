"""End-to-end: a repointed workflow persists through the injected ports.

Proves the §4 repoint is wired correctly — `run_qc_workflow` loads via the
`ExperimentReader` and persists via the `ResultStore`, recording full v3
provenance — with no live Supabase.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from bloom_mcp.data_access import FakeReader, SupabaseReader
from bloom_mcp.result_store import FakeResultStore, SupabaseResultStore
from bloom_mcp.tools import _ports
from bloom_mcp.tools.workflows import clustering as clustering_workflow
from bloom_mcp.tools.workflows.qc import run_qc_workflow

_FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "turface_19_final_data.csv"
)


@pytest.fixture
def injected_ports():
    reader = FakeReader()
    store = FakeResultStore()
    reader.add_experiment("turface.csv", pd.read_csv(_FIXTURE))
    _ports.configure(reader=reader, store=store)
    try:
        yield reader, store
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())


def test_qc_workflow_persists_via_ports(injected_ports):
    _reader, store = injected_ports

    resp = run_qc_workflow("turface.csv")

    assert "error" not in resp, resp
    assert resp["version_id"] == "v1"
    assert resp["manifest_path"] == "bloommcp_output/qc_turface/manifest.json"
    assert set(resp["outputs"]) == {"cleaned_csv", "cleanup_log_json"}

    # Persisted through the port with full v3 provenance.
    stored = store.get_run("turface.csv", "qc", "latest")
    assert stored.run_ref == "v1"
    assert stored.tool == "run_qc_workflow"
    assert stored.agent == "bloom_agent"
    assert set(stored.output_keys) == {"_cleaned.csv", "cleanup_log.json"}
    assert stored.output_keys["_cleaned.csv"].startswith("bloommcp_output/qc_turface/")


def test_second_run_increments_version(injected_ports):
    _reader, store = injected_ports
    run_qc_workflow("turface.csv")
    resp2 = run_qc_workflow("turface.csv")
    assert resp2["version_id"] == "v2"
    assert [r.run_ref for r in store.list_runs("turface.csv", "qc")] == ["v1", "v2"]


def test_stochastic_workflow_threads_and_persists_the_real_seed(
    injected_ports, monkeypatch
):
    """B1 regression: a stochastic workflow runs its delegate at random_state=42,
    so the persisted seed must equal 42 (not null) — the field that makes the
    result reproducible. Asserts the seed reaches both the computation and the
    persisted provenance."""
    _reader, store = injected_ports
    captured: dict = {}

    def _fake_kmeans(*, data, n_clusters, max_clusters, standardize, random_state):
        captured["random_state"] = random_state
        labels = [i % 2 for i in range(len(data))]
        return {"cluster_labels": labels, "n_clusters": 2, "cluster_centers": None}

    monkeypatch.setattr(clustering_workflow, "perform_kmeans_clustering", _fake_kmeans)

    resp = clustering_workflow.run_clustering_workflow(
        "turface.csv", algorithm="kmeans"
    )

    assert "error" not in resp, resp
    assert resp["version_dir"].startswith("v1_")  # canonical dir, not a /tmp path
    assert captured["random_state"] == 42  # threaded into the computation
    stored = store.get_run("turface.csv", "clustering", "latest")
    assert stored.seed == 42  # and recorded in provenance
