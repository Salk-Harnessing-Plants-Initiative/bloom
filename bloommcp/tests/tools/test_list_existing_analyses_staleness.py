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
    # Discriminator fields: a caller can tell "current" from "no qc baseline at
    # all" without the server-side log line only #420/#585's own log call sees.
    assert response["trim_based_on_qc_version"] == "v1_cleaned"
    assert response["trim_current_qc_version"] == "v1_cleaned"


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
    assert response["trim_based_on_qc_version"] == "v1_cleaned"
    assert response["trim_current_qc_version"] == "v2_cleaned"


def test_no_qc_baseline_reports_stale_with_null_current_version(
    injected_ports, local_manifest_backend
):
    """The no-`qc`-baseline-at-all corner (design.md Decision 1): `trim_is_stale`
    is `True`, but `trim_current_qc_version` is `None` — a caller can tell this
    apart from ordinary "a qc_clean ran since" staleness without needing the
    server-side log line."""
    write_cleaned_manifest(
        local_manifest_backend,
        "exp",
        "outliers",
        "v1",
        "2026-07-06T00:00:00Z",
        b"trim\n1\n",
    )

    response = json.loads(
        list_existing_analyses_mod.list_existing_analyses(_EXPERIMENT)
    )

    assert response["trim_is_stale"] is True
    assert response["trim_current_qc_version"] is None


def test_trim_is_stale_and_an_unrelated_tool_class_error_both_survive_together(
    injected_ports, local_manifest_backend, monkeypatch
):
    """A `trim_staleness` result and an unrelated tool-class `list_runs` failure
    happening in the same call must both land in the response — neither should
    silently drop the other (bloom#585 review: the two failure/success paths
    populate `errors`/`trim_is_stale` independently, but only a co-occurrence
    test proves neither write clobbers the other)."""
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
    _reader, store = injected_ports

    def _boom(_experiment, _tool_class):
        raise RuntimeError("store unavailable")

    monkeypatch.setattr(store, "list_runs", _boom)

    response = json.loads(
        list_existing_analyses_mod.list_existing_analyses(_EXPERIMENT)
    )

    assert response["trim_is_stale"] is False
    assert any(e.startswith("qc_clean: ") for e in response["errors"])
    assert len(response["errors"]) == len(list_existing_analyses_mod.TOOL_CLASSES)


def test_tool_class_error_entry_is_redacted(injected_ports, monkeypatch):
    """bloom#664 item 1: the per-tool_class loop must scrub `list_runs` failures
    with `safe_error_text`, mirroring the `trim_staleness` sibling branch below
    it in the same function and `get_download_links.py`'s equivalent handling —
    not rely on `_guarded_manifest_read`'s current callers pre-redacting for it
    implicitly (#660 design.md Decision 3)."""
    _reader, store = injected_ports

    def _boom(_experiment, _tool_class):
        raise RuntimeError("apikey=sk-secret123 leaked from store")

    monkeypatch.setattr(store, "list_runs", _boom)

    response = json.loads(
        list_existing_analyses_mod.list_existing_analyses(_EXPERIMENT)
    )

    assert response["errors"]
    for entry in response["errors"]:
        assert "sk-secret123" not in entry
    assert any("apikey=<redacted>" in e for e in response["errors"])


def test_tool_class_error_entry_uses_public_tool_name(injected_ports, monkeypatch):
    """bloom#664 item 3: the aggregated error entry must be labeled with the
    public tool name an agent actually invoked (e.g. `descriptive_stats`), not
    the internal `tool_class` string (`stats`) — with an unmapped legacy
    `tool_class` (`dimred`) falling back to itself rather than being dropped."""
    _reader, store = injected_ports

    def _boom(_experiment, _tool_class):
        raise RuntimeError("boom")

    monkeypatch.setattr(store, "list_runs", _boom)

    response = json.loads(
        list_existing_analyses_mod.list_existing_analyses(_EXPERIMENT)
    )

    assert any(e.startswith("descriptive_stats: ") for e in response["errors"])
    assert not any(e.startswith("stats: ") for e in response["errors"])
    assert any(e.startswith("dimred: ") for e in response["errors"])


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
