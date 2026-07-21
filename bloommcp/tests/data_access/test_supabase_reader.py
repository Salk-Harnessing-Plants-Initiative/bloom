"""SupabaseReader adapter — exercised on the in-memory storage boundary."""

from __future__ import annotations

import pandas as pd
import pytest

from bloom_mcp.contract import Provenance
from bloom_mcp.data_access import ExperimentNotFoundError, SupabaseReader
from bloom_mcp.result_store import SupabaseResultStore


def test_resolves_versioned_cleaned_then_raw(
    fake_supabase_storage, tmp_path, monkeypatch
):
    import bloom_mcp.experiment_utils as eu

    monkeypatch.setattr(eu, "TRAITS_DIR", tmp_path)
    raw = pd.DataFrame({"Genotype": ["g"], "trait": [1.0]})
    (tmp_path / "exp.csv").write_text(raw.to_csv(index=False))

    reader = SupabaseReader()

    # No cleaned version yet → raw, with the local-read deprecation warning.
    with pytest.warns(DeprecationWarning):
        frame = reader.load_experiment("exp.csv")
    assert frame.source == "raw"

    # Commit a cleaned version through the store, then it resolves first.
    store = SupabaseResultStore()
    run = store.create_run(
        experiment="exp.csv",
        tool_class="qc",
        provenance=Provenance.stamp(tool="run_qc_workflow", params={}),
    )
    (run.staging_dir / "_cleaned.csv").write_text(raw.to_csv(index=False))
    store.commit(run, {"_cleaned.csv": "_cleaned.csv"})

    resolved = reader.load_experiment("exp.csv")
    assert resolved.source.endswith("_cleaned")
    assert "trait" in resolved.trait_cols


def test_unknown_experiment_raises_not_found(
    fake_supabase_storage, tmp_path, monkeypatch
):
    import bloom_mcp.experiment_utils as eu

    monkeypatch.setattr(eu, "TRAITS_DIR", tmp_path)
    reader = SupabaseReader()
    with pytest.raises(ExperimentNotFoundError):
        reader.load_experiment("nope.csv")


def test_raw_source_path_rejects_path_traversal(
    fake_supabase_storage, tmp_path, monkeypatch
):
    """raw_source_path honours only a bare filename. ``name`` is LLM-controlled
    (via ``_ports.start_run``), so a crafted name that would resolve outside
    TRAITS_DIR is refused — no out-of-root file's bytes get hashed into run
    provenance."""
    import bloom_mcp.experiment_utils as eu

    traits = tmp_path / "traits"
    traits.mkdir()
    monkeypatch.setattr(eu, "TRAITS_DIR", traits)
    reader = SupabaseReader()

    # A legit bare filename resolves to the file under TRAITS_DIR.
    (traits / "exp.csv").write_text("Genotype,trait\ng,1.0\n")
    assert reader.raw_source_path("exp.csv") == traits / "exp.csv"

    # A real file exists one level up; a traversal name must NOT resolve to it.
    (tmp_path / "secrets.csv").write_text("secret")
    assert reader.raw_source_path("../secrets.csv") is None
    assert reader.raw_source_path("/etc/passwd") is None
    assert reader.raw_source_path("sub/exp.csv") is None
    assert reader.raw_source_path("absent.csv") is None
