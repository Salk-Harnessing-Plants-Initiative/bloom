"""FakeResultStore ↔ SupabaseResultStore behave equivalently for observers."""

from __future__ import annotations

import hashlib

import pytest

from bloom_mcp.contract import Provenance
from bloom_mcp.result_store import (
    FakeResultStore,
    RunNotFoundError,
    RunStateError,
    SupabaseResultStore,
)


def _prov() -> Provenance:
    return Provenance.stamp(tool="t", params={"a": 1}, seed=5)


@pytest.fixture
def stores(fake_supabase_storage):
    # fake_supabase_storage makes the Supabase adapter run in-memory.
    return {"fake": FakeResultStore(), "supabase": SupabaseResultStore()}


@pytest.mark.parametrize("kind", ["fake", "supabase"])
def test_create_commit_get_parity(kind, stores):
    store = stores[kind]
    run = store.create_run(
        experiment="exp.csv", tool_class="qc", provenance=_prov(), user_label="lbl"
    )
    (run.staging_dir / "_cleaned.csv").write_bytes(b"data")
    stored = store.commit(run, {"cleaned": "_cleaned.csv"})

    assert stored.run_ref == "v1"
    assert stored.seed == 5
    assert (
        set(stored.outputs)
        == set(stored.output_keys)
        == set(stored.output_sha256)
        == {"cleaned"}
    )
    assert stored.output_sha256["cleaned"] == hashlib.sha256(b"data").hexdigest()
    # Logical keys use forward slashes on every OS.
    assert "\\" not in stored.output_keys["cleaned"]
    assert stored.output_keys["cleaned"].startswith("bloommcp_output/qc_exp/")
    assert store.get_run("exp.csv", "qc", "latest").run_ref == "v1"


@pytest.mark.parametrize("kind", ["fake", "supabase"])
def test_not_found_and_lifecycle_parity(kind, stores):
    store = stores[kind]

    # Unknown run → not found (both backends).
    with pytest.raises(RunNotFoundError):
        store.get_run("x.csv", "qc", "latest")

    # Multi-commit increments and resolves latest identically.
    for _ in range(2):
        run = store.create_run(experiment="x.csv", tool_class="qc", provenance=_prov())
        (run.staging_dir / "o.csv").write_bytes(b"d")
        store.commit(run, {"o": "o.csv"})
    assert [r.run_ref for r in store.list_runs("x.csv", "qc")] == ["v1", "v2"]
    assert store.get_run("x.csv", "qc", "latest").run_ref == "v2"

    # Double-commit is rejected on both.
    run = store.create_run(experiment="x.csv", tool_class="qc", provenance=_prov())
    (run.staging_dir / "o.csv").write_bytes(b"d")
    store.commit(run, {"o": "o.csv"})
    with pytest.raises(RunStateError):
        store.commit(run, {"o": "o.csv"})

    # Empty outputs rejected on both.
    run2 = store.create_run(experiment="x.csv", tool_class="qc", provenance=_prov())
    with pytest.raises(ValueError):
        store.commit(run2, {})
