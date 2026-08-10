"""core_list_experiment_sources — discovery tool for #626."""

from __future__ import annotations

import pytest

from bloom_mcp.data_access import FakeReader, LocalReader, SupabaseReader
from bloom_mcp.sections.core.list_experiment_sources import list_experiment_sources
from bloom_mcp.tools import _ports


@pytest.fixture(autouse=True)
def _restore_default_reader():
    yield
    _ports.configure(reader=SupabaseReader())


def test_multiple_sources_are_listed(
    fake_supabase_storage, fake_supabase_db, seed_multi_source_experiment
):
    experiment_id = seed_multi_source_experiment(fake_supabase_db, 42, [9, 10, 11])
    _ports.configure(reader=SupabaseReader())

    result = list_experiment_sources(str(experiment_id))

    assert "9" in result
    assert "10" in result
    assert "11" in result
    assert "run-9" in result
    assert "p9" in result


def test_single_source_experiment_gets_a_clear_message_not_a_list(
    fake_supabase_storage, fake_supabase_db, seed_multi_source_experiment
):
    experiment_id = seed_multi_source_experiment(fake_supabase_db, 43, [9])
    _ports.configure(reader=SupabaseReader())

    result = list_experiment_sources(str(experiment_id))

    assert "no meaningful" in result.lower() or "only one source" in result.lower()


def test_zero_source_experiment_gets_a_clear_message(
    fake_supabase_storage, fake_supabase_db
):
    fake_supabase_db.seed_experiment(44, "legacy only")
    _ports.configure(reader=SupabaseReader())

    result = list_experiment_sources("44")

    assert "no meaningful" in result.lower() or "no source" in result.lower()


def test_non_source_selectable_backend_gets_not_applicable_message():
    reader = FakeReader()
    reader.add_experiment("exp.csv", _raw())
    _ports.configure(reader=reader)

    result = list_experiment_sources("exp.csv")

    assert "not applicable" in result.lower()
    assert "SourceSelectable" not in result  # no internal type name leaked


def test_local_reader_backend_gets_not_applicable_message(monkeypatch, tmp_path):
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
        _ports.configure(reader=LocalReader())
        result = list_experiment_sources("exp.csv")
        assert "not applicable" in result.lower()
    finally:
        sb.reset_backend_for_tests()


def _raw():
    import pandas as pd

    return pd.DataFrame({"Genotype": ["g1"], "trait_x": [1.0]})
