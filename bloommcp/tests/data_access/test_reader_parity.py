"""FakeReader ↔ SupabaseReader behave equivalently for observers.

Companion to `result_store/test_store_parity.py`: proves the #586 fix (a
mid-read storage failure during cleaned-tier resolution must convert to a
caller-safe `ExperimentReadError`, never a raw exception) holds identically
on both adapters, via one shared scenario body and two backend-specific
injection techniques.
"""

from __future__ import annotations

import contextlib

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


class _SupabaseFailureScope:
    """Scopes the injected `list_prefix` failure to one `with` block via its
    own `monkeypatch.context()` -- an independent `MonkeyPatch` instance, so
    it reverts automatically on exit without disturbing `fake_supabase_storage`'s
    own patches, which share the outer `monkeypatch` fixture instance. Avoids
    a manual capture-then-setattr-back restore, which is ordering-fragile."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._ctx = monkeypatch.context()

    def __enter__(self) -> None:
        mp = self._ctx.__enter__()
        import bloom_mcp.manifest.manifest as manifest_mod

        def _boom(prefix):
            raise RuntimeError("connection reset by peer at 10.0.0.5:5432")

        mp.setattr(manifest_mod, "list_prefix", _boom)

    def __exit__(self, *exc_info) -> None:
        self._ctx.__exit__(*exc_info)


def _load_failure_scope(kind, reader, monkeypatch, *, name):
    """A context manager: for its duration, the next `load_experiment(name)`
    call raises a mid-read storage failure -- one shared scenario body, two
    structurally different injection techniques per backend."""
    if kind == "fake":
        reader.fail_next_load(name, version="latest")
        return contextlib.nullcontext()
    return _SupabaseFailureScope(monkeypatch)


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

    with _load_failure_scope(kind, reader, monkeypatch, name="exp.csv"):
        with pytest.raises(ExperimentReadError) as exc_info:
            reader.load_experiment("exp.csv")
        # No raw backend detail leaked into the caller-facing message -- this
        # checks the pre-existing load_experiment-layer redaction, not this
        # fix's own (unredacted) intermediate error string; see design.md.
        assert "10.0.0.5" not in str(exc_info.value)

    # Scope exited: the fake's hook already cleared itself (one-shot); the
    # supabase patch is reverted by monkeypatch.context()'s own __exit__.
    frame = reader.load_experiment("exp.csv")
    assert frame.source == "v1_cleaned"
