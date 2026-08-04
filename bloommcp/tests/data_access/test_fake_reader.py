"""FakeReader oracle + edge cases — the read port with no Supabase."""

from __future__ import annotations

import pandas as pd
import pytest

from bloom_mcp.data_access import (
    CleanedVersionRequiredError,
    ExperimentFrame,
    ExperimentNotFoundError,
    ExperimentReadError,
    FakeReader,
)


def _raw() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Barcode": ["a", "b", "c"],
            "Genotype": ["g1", "g1", "g2"],
            "Replicate": [1, 2, 1],
            "trait_x": [1.0, 2.0, 3.0],
            "trait_y": [4.0, 5.0, 6.0],
        }
    )


def test_load_experiment_returns_frame_with_declared_roles():
    reader = FakeReader()
    reader.add_experiment("exp.csv", _raw())

    frame = reader.load_experiment("exp.csv")

    assert isinstance(frame, ExperimentFrame)
    assert frame.source == "raw"
    assert set(frame.trait_cols) == {"trait_x", "trait_y"}
    assert "Barcode" in frame.metadata_cols
    assert frame.genotype_col == "Genotype"
    assert frame.replicate_col == "Replicate"
    assert len(frame.df) == 3


def test_latest_resolves_cleaned_then_falls_back_to_raw():
    reader = FakeReader()
    reader.add_experiment("exp.csv", _raw())

    # No cleaned version yet → latest falls through to raw.
    assert reader.load_experiment("exp.csv", version="latest").source == "raw"

    reader.add_cleaned_version("exp.csv", "v3", _raw().iloc[:2])
    frame = reader.load_experiment("exp.csv", version="latest")
    assert frame.source == "v3_cleaned"
    assert len(frame.df) == 2


def test_latest_qc_is_aliased_to_latest():
    """FakeReader has no tool-class model (#420) — version="latest_qc" resolves
    identically to version="latest" rather than 404ing as a literal-but-nonexistent
    version id, so remove_outliers's own call-site switch doesn't break FakeReader
    -seeded tests."""
    reader = FakeReader()
    reader.add_experiment("exp.csv", _raw())
    reader.add_cleaned_version("exp.csv", "v3", _raw().iloc[:2])

    frame = reader.load_experiment("exp.csv", version="latest_qc")
    assert frame.source == "v3_cleaned"
    assert len(frame.df) == 2


def test_explicit_version_miss_raises_not_found():
    reader = FakeReader()
    reader.add_experiment("exp.csv", _raw())
    with pytest.raises(ExperimentNotFoundError):
        reader.load_experiment("exp.csv", version="v9")


def test_require_clean_without_clean_raises():
    reader = FakeReader()
    reader.add_experiment("exp.csv", _raw())
    with pytest.raises(CleanedVersionRequiredError):
        reader.load_experiment("exp.csv", require_clean=True)


def test_unknown_experiment_raises_not_found():
    reader = FakeReader()
    with pytest.raises(ExperimentNotFoundError):
        reader.load_experiment("missing.csv")


def test_fail_next_load_raises_once_then_clears():
    """One-shot: the next matching load_experiment() call raises
    ExperimentReadError, then a retry for the same (name, version) succeeds
    normally -- mirrors FakeResultStore.fail_next_commit's contract."""
    reader = FakeReader()
    reader.add_experiment("exp.csv", _raw())

    reader.fail_next_load("exp.csv", version="raw")
    with pytest.raises(ExperimentReadError):
        reader.load_experiment("exp.csv", version="raw")

    # One-shot: cleared after raising, so a retry for the same key succeeds.
    frame = reader.load_experiment("exp.csv", version="raw")
    assert frame.source == "raw"


def test_fail_next_load_is_scoped_to_name_and_version():
    """The injected failure only fires for the exact (name, version) it was
    registered for -- a different version for the same name is unaffected."""
    reader = FakeReader()
    reader.add_experiment("exp.csv", _raw())
    reader.add_cleaned_version("exp.csv", "v3", _raw().iloc[:2])

    reader.fail_next_load("exp.csv", version="v3")
    # A different, non-failing version for the same name resolves normally.
    frame = reader.load_experiment("exp.csv", version="raw")
    assert frame.source == "raw"

    with pytest.raises(ExperimentReadError):
        reader.load_experiment("exp.csv", version="v3")


def test_fail_next_load_fires_even_for_an_unseeded_experiment():
    """The injected failure is checked before any resolution logic -- including
    the not-found check -- so it takes priority even over an experiment that
    was never seeded at all. Pins that ordering against a future regression
    that might move the check after the not-found path."""
    reader = FakeReader()
    reader.fail_next_load("missing.csv", version="raw")
    with pytest.raises(ExperimentReadError):
        reader.load_experiment("missing.csv", version="raw")


def test_list_experiments_empty_then_populated():
    reader = FakeReader()
    assert reader.list_experiments() == []

    reader.add_experiment("exp.csv", _raw(), experiment_name="My Exp")
    summaries = reader.list_experiments()
    assert len(summaries) == 1
    assert summaries[0].filename == "exp.csv"
    assert summaries[0].experiment_name == "My Exp"
    assert summaries[0].trait_columns == 2
    assert summaries[0].genotype_col == "Genotype"
