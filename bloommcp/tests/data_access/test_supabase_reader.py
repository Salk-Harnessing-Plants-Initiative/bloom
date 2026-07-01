"""SupabaseReader adapter — exercised on the in-memory storage boundary."""

from __future__ import annotations

import warnings

import pandas as pd
import pytest

from bloom_mcp.contract import Provenance
from bloom_mcp.data_access import (
    ExperimentNotFoundError,
    ExperimentReadError,
    SupabaseReader,
)
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


def test_bucket_hit_reads_raw_without_warning(
    fake_supabase_storage, tmp_path, monkeypatch
):
    """A file in bloommcp_input/ is read as raw — and does NOT warn (bucket is
    the intended source; only the local mount is deprecated)."""
    import bloom_mcp.experiment_utils as eu

    monkeypatch.setattr(eu, "TRAITS_DIR", tmp_path)  # empty local mount
    df = pd.DataFrame({"Genotype": ["g1", "g2"], "trait": [1.0, 2.0]})
    fake_supabase_storage.put_input_csv("exp.csv", df)

    reader = SupabaseReader()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        frame = reader.load_experiment("exp.csv")

    assert frame.source == "raw"
    assert "trait" in frame.trait_cols
    assert not any(issubclass(w.category, DeprecationWarning) for w in caught)


def test_bucket_miss_falls_back_to_local_with_warning(
    fake_supabase_storage, tmp_path, monkeypatch
):
    """A file only on the local mount still resolves — with the deprecation warning."""
    import bloom_mcp.experiment_utils as eu

    monkeypatch.setattr(eu, "TRAITS_DIR", tmp_path)
    df = pd.DataFrame({"Genotype": ["g"], "trait": [1.0]})
    (tmp_path / "local_only.csv").write_text(df.to_csv(index=False))

    reader = SupabaseReader()
    with pytest.warns(DeprecationWarning):
        frame = reader.load_experiment("local_only.csv")
    assert frame.source == "raw"


def test_bucket_backend_error_is_not_a_miss(
    fake_supabase_storage, tmp_path, monkeypatch
):
    """A file listed in the bucket but unreadable (network/RLS/5xx) surfaces as
    ExperimentReadError, not a confident 'not found'."""
    import bloom_mcp.experiment_utils as eu
    import bloom_mcp.supabase_client as sc

    monkeypatch.setattr(eu, "TRAITS_DIR", tmp_path)
    monkeypatch.setattr(sc, "list_prefix", lambda _prefix: ["boom.csv"])

    def _boom(_name):
        raise RuntimeError("503 SlowDown")

    monkeypatch.setattr(sc, "read_input_csv", _boom)

    reader = SupabaseReader()
    with pytest.raises(ExperimentReadError):
        reader.load_experiment("boom.csv")


def test_list_experiments_bucket_and_local(
    fake_supabase_storage, tmp_path, monkeypatch
):
    """Bucket entries appear, non-csv is skipped, and a bucket file shadows a
    same-named local one."""
    import bloom_mcp.experiment_utils as eu

    monkeypatch.setattr(eu, "TRAITS_DIR", tmp_path)

    bucket_shared = pd.DataFrame({"Genotype": ["a", "b"], "trait": [1.0, 2.0]})  # 2 rows
    fake_supabase_storage.put_input_csv("shared.csv", bucket_shared)
    fake_supabase_storage.put_input_csv("bucket_only.csv", bucket_shared)
    fake_supabase_storage.objects["bloommcp_input/notes.txt"] = b"not a csv"

    local_shared = pd.DataFrame({"Genotype": list("cdefg"), "trait": [3.0] * 5})  # 5 rows
    (tmp_path / "shared.csv").write_text(local_shared.to_csv(index=False))
    (tmp_path / "local_only.csv").write_text(bucket_shared.to_csv(index=False))

    reader = SupabaseReader()
    summaries = {s.filename: s for s in reader.list_experiments()}

    assert "bucket_only.csv" in summaries
    assert "local_only.csv" in summaries
    assert "notes.txt" not in summaries  # non-csv skipped
    assert summaries["shared.csv"].rows == 2  # bucket copy shadows the 5-row local
