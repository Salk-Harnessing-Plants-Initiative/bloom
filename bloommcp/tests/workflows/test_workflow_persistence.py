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
