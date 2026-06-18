"""FakeResultStore oracle + edge cases — the write port with no Supabase."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from bloom_mcp.contract import Provenance
from bloom_mcp.result_store import (
    FakeResultStore,
    RunNotFoundError,
    RunStateError,
)


def _prov(tool: str = "run_qc_workflow", seed: int = 42) -> Provenance:
    return Provenance.stamp(tool=tool, params={"k": 1}, seed=seed)


def _write(staging: Path, name: str, data: bytes) -> None:
    (Path(staging) / name).write_bytes(data)


def test_create_commit_records_versioned_run_with_provenance():
    store = FakeResultStore()
    run = store.create_run(
        experiment="exp.csv", tool_class="qc", provenance=_prov(), user_label="first"
    )
    assert run.version_id == "v1"

    payload = b"a,b\n1,2\n"
    _write(run.staging_dir, "_cleaned.csv", payload)
    stored = store.commit(run, {"cleaned": "_cleaned.csv"})

    assert stored.run_ref == "v1"
    assert stored.tool == "run_qc_workflow"
    assert stored.seed == 42
    assert stored.agent == "bloom_agent"
    # outputs / keys / hashes share one key-set.
    assert (
        set(stored.outputs)
        == set(stored.output_keys)
        == set(stored.output_sha256)
        == {"cleaned"}
    )
    assert stored.output_sha256["cleaned"] == hashlib.sha256(payload).hexdigest()
    assert stored.output_keys["cleaned"].startswith("bloommcp_output/qc_exp/")
    assert stored.output_keys["cleaned"].endswith("/_cleaned.csv")

    assert store.get_run("exp.csv", "qc", "latest").run_ref == "v1"
    assert [r.run_ref for r in store.list_runs("exp.csv", "qc")] == ["v1"]


def test_get_run_resolves_latest_across_commits():
    store = FakeResultStore()
    for i in range(2):
        run = store.create_run(
            experiment="e.csv", tool_class="stats", provenance=_prov(tool="t")
        )
        _write(run.staging_dir, "out.csv", f"v{i}".encode())
        store.commit(run, {"o": "out.csv"})

    assert [r.run_ref for r in store.list_runs("e.csv", "stats")] == ["v1", "v2"]
    assert store.get_run("e.csv", "stats", "latest").run_ref == "v2"
    assert store.get_run("e.csv", "stats", "v1").run_ref == "v1"


def test_unknown_run_raises_not_found():
    store = FakeResultStore()
    with pytest.raises(RunNotFoundError):
        store.get_run("e.csv", "qc", "latest")
    store2 = FakeResultStore()
    run = store2.create_run(experiment="e.csv", tool_class="qc", provenance=_prov())
    _write(run.staging_dir, "o.csv", b"x")
    store2.commit(run, {"o": "o.csv"})
    with pytest.raises(RunNotFoundError):
        store2.get_run("e.csv", "qc", "v9")


def test_double_commit_rejected():
    store = FakeResultStore()
    run = store.create_run(experiment="e.csv", tool_class="qc", provenance=_prov())
    _write(run.staging_dir, "o.csv", b"x")
    store.commit(run, {"o": "o.csv"})
    with pytest.raises(RunStateError):
        store.commit(run, {"o": "o.csv"})
