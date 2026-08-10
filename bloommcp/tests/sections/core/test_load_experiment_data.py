"""load_experiment_data's source_id/run_id kwargs (#626)."""

from __future__ import annotations

import inspect

import pandas as pd
import pytest

from bloom_mcp.data_access import FakeReader, SupabaseReader
from bloom_mcp.sections.core.load_experiment_data import load_experiment_data
from bloom_mcp.tools import _ports

_EXPERIMENT = "exp.csv"


@pytest.fixture(autouse=True)
def _restore_default_reader():
    yield
    _ports.configure(reader=SupabaseReader())


def test_accepts_source_id_and_run_id_kwargs():
    sig = inspect.signature(load_experiment_data)
    assert "source_id" in sig.parameters
    assert "run_id" in sig.parameters


def test_omitting_both_preserves_todays_summary():
    reader = FakeReader()
    reader.add_experiment(_EXPERIMENT, _raw())
    _ports.configure(reader=reader)

    result = load_experiment_data(_EXPERIMENT)

    assert "Samples: 2" in result


def test_both_source_id_and_run_id_returns_the_error_string_not_a_crash():
    reader = FakeReader()
    reader.add_experiment(_EXPERIMENT, _raw())
    _ports.configure(reader=reader)

    # FakeReader has no source concept, so this is SourcePinningUnsupportedError
    # surfaced through the existing string-error contract — not a crash.
    result = load_experiment_data(_EXPERIMENT, source_id=9, run_id="p10")

    assert "Samples:" not in result


def _raw() -> pd.DataFrame:
    return pd.DataFrame({"Genotype": ["g1", "g2"], "trait_x": [1.0, 2.0]})
