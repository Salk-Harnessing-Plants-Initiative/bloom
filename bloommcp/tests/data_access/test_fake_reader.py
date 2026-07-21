"""FakeReader oracle + edge cases — the read port with no Supabase."""

from __future__ import annotations

import pandas as pd
import pytest

from bloom_mcp.data_access import (
    CleanedVersionRequiredError,
    ExperimentFrame,
    ExperimentNotFoundError,
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
