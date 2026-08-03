"""FakeReader ↔ SupabaseReader behave equivalently for observers.

Companion to `result_store/test_store_parity.py`: proves the #586 fix (a
mid-read storage failure during cleaned-tier resolution must convert to a
caller-safe `ExperimentReadError`, never a raw exception) holds identically
on both adapters, via one shared scenario body and two backend-specific
injection techniques.
"""

from __future__ import annotations

import pandas as pd
import pytest

from bloom_mcp.contract import Provenance
from bloom_mcp.data_access import ExperimentReadError, FakeReader, SupabaseReader
from bloom_mcp.result_store import SupabaseResultStore


def _seed_cleaned_supabase(experiment: str) -> None:
    """Commit a versioned cleaned output through the real store, against
    `fake_supabase_storage`'s in-memory boundary."""
    store = SupabaseResultStore()
    run = store.create_run(
        experiment=experiment,
        tool_class="qc",
        provenance=Provenance.stamp(tool="run_qc_workflow", params={}),
    )
    (run.staging_dir / "_cleaned.csv").write_text("a,b\n1,2\n")
    store.commit(run, {"_cleaned.csv": "_cleaned.csv"})


def _inject_load_failure(kind, reader, monkeypatch, *, name):
    """Force the next `load_experiment(name)` call to raise a mid-read
    storage failure -- one shared scenario body, two structurally different
    injection techniques per backend. Returns the real `list_prefix` (for the
    "supabase" kind only) so the caller can restore it without an outright
    `monkeypatch.undo()`, which would also undo `fake_supabase_storage`'s own
    patches shared through the same `monkeypatch` fixture instance."""
    if kind == "fake":
        reader.fail_next_load(name, version="latest")
        return None

    import bloom_mcp.manifest.manifest as manifest_mod

    real_list_prefix = manifest_mod.list_prefix

    def _boom(prefix):
        raise RuntimeError("connection reset by peer at 10.0.0.5:5432")

    monkeypatch.setattr(manifest_mod, "list_prefix", _boom)
    return real_list_prefix


@pytest.mark.parametrize("kind", ["fake", "supabase"])
def test_mid_read_storage_failure_is_caller_safe_and_retryable(
    kind, fake_supabase_storage, monkeypatch
):
    if kind == "fake":
        reader = FakeReader()
        reader.add_cleaned_version("exp.csv", "v1", pd.DataFrame({"a": [1], "b": [2]}))
    else:
        reader = SupabaseReader()
        _seed_cleaned_supabase("exp.csv")

    real_list_prefix = _inject_load_failure(kind, reader, monkeypatch, name="exp.csv")

    with pytest.raises(ExperimentReadError) as exc_info:
        reader.load_experiment("exp.csv")
    # No raw backend detail leaked into the caller-facing message.
    assert "10.0.0.5" not in str(exc_info.value)

    if kind == "supabase":
        # The injected failure is a static monkeypatch, not a one-shot hook --
        # restore just this one patch (not monkeypatch.undo(), which would also
        # revert fake_supabase_storage's own patches) to simulate the outage
        # recovering, then prove a retry succeeds.
        import bloom_mcp.manifest.manifest as manifest_mod

        monkeypatch.setattr(manifest_mod, "list_prefix", real_list_prefix)

    frame = reader.load_experiment("exp.csv")
    assert frame.source == "v1_cleaned"
