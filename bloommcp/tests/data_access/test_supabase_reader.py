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


def _seed_uploaded_parquet(store, name: str, df: pd.DataFrame) -> None:
    import io

    buf = io.BytesIO()
    df.to_parquet(buf)
    store.objects[f"bloommcp_input/{name}"] = buf.getvalue()


def test_resolves_uploaded_input_multi_format(
    fake_supabase_storage, tmp_path, monkeypatch
):
    import bloom_mcp.experiment_utils as eu

    monkeypatch.setattr(eu, "TRAITS_DIR", tmp_path)  # empty local raw
    df = pd.DataFrame({"Genotype": ["g0", "g1"], "trait_x": [1.0, 2.0]})
    _seed_uploaded_parquet(fake_supabase_storage, "counts.parquet", df)

    frame = SupabaseReader().load_experiment("counts.parquet")
    assert frame.source == "uploaded"
    assert "trait_x" in frame.trait_cols
    assert list(frame.df.columns) == ["Genotype", "trait_x"]


def test_lists_uploaded_inputs(fake_supabase_storage, tmp_path, monkeypatch):
    import bloom_mcp.experiment_utils as eu

    monkeypatch.setattr(eu, "TRAITS_DIR", tmp_path)
    _seed_uploaded_parquet(
        fake_supabase_storage,
        "counts.parquet",
        pd.DataFrame({"Genotype": ["g"], "trait": [1.0]}),
    )

    names = [s.filename for s in SupabaseReader().list_experiments()]
    assert "counts.parquet" in names
