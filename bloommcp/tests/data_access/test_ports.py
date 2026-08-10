"""ExperimentReader Protocol-level source-pin contract (#626).

Verifies the contract generically through the Protocol type, not only against
one concrete adapter: every adapter must accept ``source_id``/``run_id``, and
either honor a pin, reject ambiguity/mismatch, or reject the pin outright when
it has no source concept.
"""

from __future__ import annotations

import inspect

import pandas as pd
import pytest

from bloom_mcp.data_access import (
    AmbiguousSourceSelectionError,
    ExperimentReader,
    FakeReader,
    LocalReader,
    SourcePinningUnsupportedError,
    SupabaseReader,
)


def test_protocol_declares_source_id_and_run_id():
    """ExperimentReader.load_experiment declares source_id/run_id (defaulting
    to None), not just SupabaseReader's concrete implementation."""
    sig = inspect.signature(ExperimentReader.load_experiment)
    assert sig.parameters["source_id"].default is None
    assert sig.parameters["run_id"].default is None


@pytest.mark.parametrize("reader_factory", [FakeReader])
def test_source_id_alone_is_rejected_as_unsupported(reader_factory):
    reader = reader_factory()
    reader.add_experiment("exp.csv", _raw())
    with pytest.raises(SourcePinningUnsupportedError):
        reader.load_experiment("exp.csv", source_id=7)


@pytest.mark.parametrize("reader_factory", [FakeReader])
def test_run_id_alone_is_rejected_as_unsupported(reader_factory):
    reader = reader_factory()
    reader.add_experiment("exp.csv", _raw())
    with pytest.raises(SourcePinningUnsupportedError):
        reader.load_experiment("exp.csv", run_id="run-1")


@pytest.mark.parametrize("reader_factory", [FakeReader])
def test_both_given_is_unsupported_not_ambiguous_on_adapters_without_source_concept(
    reader_factory,
):
    """An adapter with no source concept at all raises SourcePinningUnsupportedError
    even when both kwargs are given — it never reaches SupabaseReader's ambiguity
    check, so AmbiguousSourceSelectionError is the wrong signal here."""
    reader = reader_factory()
    reader.add_experiment("exp.csv", _raw())
    with pytest.raises(SourcePinningUnsupportedError):
        reader.load_experiment("exp.csv", source_id=7, run_id="run-1")


def test_local_reader_rejects_source_id(monkeypatch, tmp_path):
    import bloom_mcp.storage_backend as sb

    inp = tmp_path / "input"
    inp.mkdir()
    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "local")
    monkeypatch.setenv("BLOOM_STORAGE_LOCAL_ROOT", str(store))
    monkeypatch.setenv("BLOOM_EXPERIMENT_LOCAL_ROOT", str(inp))
    monkeypatch.setenv("BLOOM_STORAGE_URL", "http://localhost/output")
    sb.reset_backend_for_tests()
    try:
        (inp / "exp.csv").write_text("Genotype,trait_a\ng1,1.0\n")
        reader = LocalReader()
        with pytest.raises(SourcePinningUnsupportedError):
            reader.load_experiment("exp.csv", source_id=7)
    finally:
        sb.reset_backend_for_tests()


def test_supabase_reader_still_satisfies_the_protocol_after_the_signature_extension():
    """Extending the Protocol must not narrow it out from under SupabaseReader —
    the ambiguous-pin / pin-not-found cases (test_supabase_reader.py) already
    cover SupabaseReader's own behavior; this is the Protocol-conformance guard
    that the two coexist."""
    reader: ExperimentReader = SupabaseReader()
    assert isinstance(reader, ExperimentReader)


def test_ambiguous_source_selection_is_a_distinct_class_from_unsupported():
    """Sanity check the two error classes are not accidentally aliased."""
    assert not issubclass(SourcePinningUnsupportedError, AmbiguousSourceSelectionError)
    assert not issubclass(AmbiguousSourceSelectionError, SourcePinningUnsupportedError)


def _raw() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Barcode": ["a", "b", "c"],
            "Genotype": ["g1", "g1", "g2"],
            "trait_x": [1.0, 2.0, 3.0],
        }
    )
