"""bloom#585 — `trim_is_stale` field on `list_existing_analyses`.

`trim_staleness` reads real manifests through `AnalysisDir`/`storage_backend`,
a seam below `FakeReader`/`FakeResultStore` (see `injected_ports` in
`test_qc_tools_discovery.py`) — so these tests combine `injected_ports` (with
an empty `FakeReader`, which never rejects an experiment name — the "known
experiment" guard only fires when its `known` set is non-empty) with the
`local_manifest_backend` fixture for real, on-disk manifests.
"""

from __future__ import annotations

import json

import pytest

from bloom_mcp.data_access import FakeReader, SupabaseReader
from bloom_mcp.result_store import FakeResultStore, SupabaseResultStore
from bloom_mcp.sections.core import list_existing_analyses as list_existing_analyses_mod
from bloom_mcp.tools import _ports
from manifest_fixtures import write_cleaned_manifest

_EXPERIMENT = "exp.csv"


@pytest.fixture
def injected_ports():
    reader = FakeReader()
    store = FakeResultStore()
    _ports.configure(reader=reader, store=store)
    list_existing_analyses_mod._RESPONSE_CACHE.clear()
    try:
        yield reader, store
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())
        list_existing_analyses_mod._RESPONSE_CACHE.clear()


def test_untrimmed_experiment_has_no_new_field(injected_ports, local_manifest_backend):
    write_cleaned_manifest(
        local_manifest_backend, "exp", "qc", "v1", "2026-07-06T00:00:00Z", b"a,b\n1,2\n"
    )

    response = json.loads(
        list_existing_analyses_mod.list_existing_analyses(_EXPERIMENT)
    )

    assert "trim_is_stale" not in response
    assert "errors" not in response


def test_current_trim_reports_not_stale(injected_ports, local_manifest_backend):
    write_cleaned_manifest(
        local_manifest_backend, "exp", "qc", "v1", "2026-07-06T00:00:00Z", b"a,b\n1,2\n"
    )
    write_cleaned_manifest(
        local_manifest_backend,
        "exp",
        "outliers",
        "v1",
        "2026-07-06T00:00:01Z",
        b"trim\n1\n",
    )

    response = json.loads(
        list_existing_analyses_mod.list_existing_analyses(_EXPERIMENT)
    )

    assert response["trim_is_stale"] is False


def test_stale_trim_reports_stale(injected_ports, local_manifest_backend):
    write_cleaned_manifest(
        local_manifest_backend, "exp", "qc", "v1", "2026-07-06T00:00:00Z", b"a,b\n1,2\n"
    )
    write_cleaned_manifest(
        local_manifest_backend,
        "exp",
        "outliers",
        "v1",
        "2026-07-06T00:00:01Z",
        b"trim\n1\n",
    )
    write_cleaned_manifest(
        local_manifest_backend, "exp", "qc", "v2", "2026-07-06T00:01:00Z", b"a,b\n3,4\n"
    )

    response = json.loads(
        list_existing_analyses_mod.list_existing_analyses(_EXPERIMENT)
    )

    assert response["trim_is_stale"] is True


def test_trim_staleness_failure_is_reported_not_raised(injected_ports, monkeypatch):
    """A `trim_staleness` failure must not raise, must omit `trim_is_stale`, and
    must still return the tool's other output (`analyses`)."""

    def _boom(_stem):
        raise RuntimeError("manifest schema error for 'exp': boom")

    monkeypatch.setattr(list_existing_analyses_mod, "trim_staleness", _boom)

    response = json.loads(
        list_existing_analyses_mod.list_existing_analyses(_EXPERIMENT)
    )

    assert "trim_is_stale" not in response
    assert any(e.startswith("trim_staleness: ") for e in response["errors"])
    assert "analyses" in response


def test_no_storage_backend_configured_is_reported_not_raised(injected_ports):
    """This repo's actual test-suite default (`conftest.py` scrubs
    `SUPABASE_URL`/`BLOOM_AGENT_KEY`, and no test here sets
    `BLOOM_STORAGE_BACKEND`) must not raise out of `list_existing_analyses` —
    converts what would otherwise be an untested, accidental-pass corner into a
    verified contract."""
    response = json.loads(
        list_existing_analyses_mod.list_existing_analyses(_EXPERIMENT)
    )

    assert "trim_is_stale" not in response
    assert any(e.startswith("trim_staleness: ") for e in response["errors"])
