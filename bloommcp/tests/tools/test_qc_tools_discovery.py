"""C3.2 behavioral coverage for the surviving core discovery tools.

`list_available_experiments`, `load_experiment_data` and `list_existing_analyses`
(now in `bloom_mcp.sections.core`, one file per tool — moved by the Phase-2
sections migration, devendor-bloommcp-analysis) are the three tools the
`bloommcp-tool-sections` spec's "Core Section for Cross-Cutting Discovery Tools"
requirement carries — none is a `sleap-roots-analyze` wrapper.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from bloom_mcp.data_access import FakeReader, SupabaseReader
from bloom_mcp.result_store import FakeResultStore, SupabaseResultStore
from bloom_mcp.sections.core import (
    list_available_experiments as list_available_experiments_mod,
    list_existing_analyses as list_existing_analyses_mod,
    load_experiment_data as load_experiment_data_mod,
)
from bloom_mcp.tools import _ports

_EXPERIMENT = "turface_19.csv"


def _raw_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Barcode": [f"p{i}" for i in range(6)],
            "Genotype": ["A", "A", "A", "B", "B", "B"],
            "Replicate": [1, 2, 3, 1, 2, 3],
            "trait_one": [1.0, 2.0, None, 4.0, 5.0, 6.0],
            "trait_two": [10.0, 20.0, 30.0, 40.0, 50.0, 60.0],
        }
    )


@pytest.fixture
def injected_ports():
    reader = FakeReader()
    reader.add_experiment(_EXPERIMENT, _raw_df())
    store = FakeResultStore()
    _ports.configure(reader=reader, store=store)
    list_existing_analyses_mod._RESPONSE_CACHE.clear()
    try:
        yield reader, store
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())
        list_existing_analyses_mod._RESPONSE_CACHE.clear()


def test_list_available_experiments_reports_the_seeded_experiment(injected_ports):
    result = list_available_experiments_mod.list_available_experiments()

    assert _EXPERIMENT in result
    assert "Samples: 6" in result
    assert "Traits: 2" in result
    assert "Genotype column: Genotype" in result


def test_list_available_experiments_empty_reader_says_none_available():
    reader = FakeReader()
    store = FakeResultStore()
    _ports.configure(reader=reader, store=store)
    try:
        assert (
            list_available_experiments_mod.list_available_experiments()
            == "No experiments available"
        )
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())


def test_load_experiment_data_summarizes_samples_traits_and_missingness(
    injected_ports,
):
    result = load_experiment_data_mod.load_experiment_data(_EXPERIMENT)

    assert "Samples: 6" in result
    assert "Genotypes: 2" in result
    assert "Replicates: 3" in result
    assert "Trait columns: 2" in result
    # One NaN out of 12 trait cells (6 samples x 2 traits).
    assert "Missing values: 1 / 12" in result
    assert "Traits with any NaN: 1 / 2" in result


def test_load_experiment_data_unknown_file_surfaces_reader_error(injected_ports):
    result = load_experiment_data_mod.load_experiment_data("does_not_exist.csv")
    assert "does_not_exist.csv" in result


def test_list_existing_analyses_reports_no_prior_runs_for_a_known_experiment(
    injected_ports,
):
    result = list_existing_analyses_mod.list_existing_analyses(_EXPERIMENT)
    payload = json.loads(result)

    assert payload["experiment"] == _EXPERIMENT
    assert payload["analyses"] == {}
    assert "No prior analyses found" in payload["message"]


def test_list_existing_analyses_unknown_experiment_is_reported(injected_ports):
    result = list_existing_analyses_mod.list_existing_analyses("not_seeded.csv")
    payload = json.loads(result)

    assert "error" in payload
    assert _EXPERIMENT in payload["available_experiments"]


def test_list_existing_analyses_response_is_cached_within_ttl(
    injected_ports, monkeypatch
):
    _reader, store = injected_ports

    first = list_existing_analyses_mod.list_existing_analyses(_EXPERIMENT)

    # Mutate the store directly (bypassing the tool) — a cached response should
    # still be returned without re-querying the store within the TTL window.
    monkeypatch.setattr(
        store,
        "list_runs",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("store queried despite a fresh cache entry")
        ),
    )
    second = list_existing_analyses_mod.list_existing_analyses(_EXPERIMENT)
    assert second == first
