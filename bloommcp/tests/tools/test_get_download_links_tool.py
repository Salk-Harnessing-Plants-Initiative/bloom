"""bloom#599 — the `get_download_links` core tool.

Re-signs fresh download links for an already-committed run. Mirrors
`test_qc_tools_discovery.py`'s style for the other core tools.
"""

from __future__ import annotations

import json
from typing import Optional

import pandas as pd
import pytest

from bloom_mcp.contract import Provenance
from bloom_mcp.data_access import FakeReader, SupabaseReader
from bloom_mcp.experiment_utils import safe_error_text
from bloom_mcp.result_store import (
    CorruptRunLinksError,
    FakeResultStore,
    ManifestIncompatibleError,
    ManifestReadError,
    RunNotFoundError,
    SupabaseResultStore,
)
from bloom_mcp.sections.core import get_download_links as get_download_links_mod
from bloom_mcp.storage_backend import StorageBackendError, StorageKeyNotFound
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
    try:
        yield reader, store
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())


def _commit_a_run(store: FakeResultStore, params: Optional[dict] = None) -> None:
    run = store.create_run(
        experiment=_EXPERIMENT,
        tool_class="qc",
        provenance=Provenance.stamp(
            tool="qc_clean", params=params if params is not None else {}, seed=1
        ),
    )
    (run.staging_dir / "_cleaned.csv").write_bytes(b"cleaned-bytes")
    store.commit(run, {"cleaned": "_cleaned.csv"})


def test_happy_path_returns_resolved_run_and_output_links(injected_ports):
    _reader, store = injected_ports
    _commit_a_run(store, params={"exclude": ["p0"]})

    result = get_download_links_mod.get_download_links(_EXPERIMENT, "qc", "latest")
    payload = json.loads(result)

    assert payload["experiment"] == _EXPERIMENT
    assert payload["tool_class"] == "qc"
    assert payload["run_ref"] == "v1"
    assert payload["outputs"] == {"cleaned": "_cleaned.csv"}
    link = payload["output_links"]["cleaned"]
    assert link["url"]
    assert link["sha256"]
    assert link["size_bytes"] == len(b"cleaned-bytes")
    # bloom#600, reworked per bloom#622 review (see
    # add-bloommcp-manifest-download-link's design.md Decision 5):
    # the resolved run's own params/based_on_version, scoped to this one
    # run -- not a signed link to the shared manifest.json, which would
    # have exposed every run for this (experiment, tool_class) pair.
    assert "manifest_path" not in payload
    assert "manifest_url" not in payload
    assert payload["params"] == {"exclude": ["p0"]}
    assert payload["based_on_version"] == "raw"


def test_legacy_run_response_still_carries_its_own_params(injected_ports):
    """bloom#600, reworked per bloom#622 review: a legacy run with no
    per-artifact output_keys returns output_links == {} in the JSON (as it
    already did per #599), but params/based_on_version are still
    populated -- they are never gated on the outputs' own key-presence
    check, since they were part of the manifest schema since v2 (unlike
    seed/agent/environment, v3-only fields absent from this seeded run)."""
    _reader, store = injected_ports
    store.seed_v2_run(
        _EXPERIMENT,
        "qc",
        tool="qc_clean",
        outputs={"cleaned": "_cleaned.csv"},
        params={"legacy": True},
        based_on_version="raw",
    )

    result = get_download_links_mod.get_download_links(_EXPERIMENT, "qc", "latest")
    payload = json.loads(result)

    assert payload["output_links"] == {}
    # Real-value equality (PR #622 review finding), not just key-presence --
    # a broken provenance lookup would otherwise pass unnoticed.
    assert payload["params"] == {"legacy": True}
    assert payload["based_on_version"] == "raw"


def test_enumerate_via_list_existing_analyses_then_loop_reconstructs_every_runs_params(
    injected_ports,
):
    """bloom#622 review (design.md Decision 6): a single get_download_links
    call is scoped to exactly one run's params -- but list_existing_analyses
    (unauthenticated relative to any per-experiment permission, and
    always-included) already enumerates every historical run_ref for an
    experiment with no opt-in required. Looping get_download_links once per
    enumerated ref reconstructs every run's params, not just the one a
    single call resolves.

    This is accepted, not fixed -- add-bloommcp-manifest-download-link's
    design.md Decision 6 explains why: this composition (enumerate via
    list_existing_analyses, then fetch per ref) already applies to
    output_links -- the actual analysis output data -- since #599; params
    (analysis configuration metadata, not raw data) doesn't cross a new
    trust boundary by riding the same, already-accepted composition. This
    test exists to make that boundary explicit and regression-visible
    rather than requiring cross-file verification (review Suggestion),
    not to assert that the composition is somehow prevented.
    """
    from bloom_mcp.sections.core import list_existing_analyses as lea_mod

    _reader, store = injected_ports
    lea_mod._RESPONSE_CACHE.clear()

    for i in range(3):
        run = store.create_run(
            experiment=_EXPERIMENT,
            tool_class="qc",
            provenance=Provenance.stamp(
                tool="qc_clean", params={"run_index": i}, seed=i
            ),
        )
        (run.staging_dir / "_cleaned.csv").write_bytes(f"cleaned-{i}".encode())
        store.commit(run, {"cleaned": "_cleaned.csv"})

    listing = json.loads(lea_mod.list_existing_analyses(_EXPERIMENT))
    run_refs = [entry["run_ref"] for entry in listing["analyses"]["qc"]]
    # Every historical run_ref is enumerable in one call, no opt-in required --
    # this is the pre-existing capability the composition below builds on.
    assert len(run_refs) == 3

    reconstructed_params = {
        ref: json.loads(
            get_download_links_mod.get_download_links(_EXPERIMENT, "qc", ref)
        )["params"]
        for ref in run_refs
    }
    # Every run's own params, individually scoped per call, is nonetheless
    # fully reconstructable in aggregate across the N calls.
    assert {p["run_index"] for p in reconstructed_params.values()} == {0, 1, 2}


def test_unknown_experiment_reports_available_experiments(injected_ports):
    result = get_download_links_mod.get_download_links(
        "does_not_exist.csv", "qc", "latest"
    )
    payload = json.loads(result)

    assert "error" in payload
    assert _EXPERIMENT in payload["available_experiments"]


@pytest.mark.parametrize(
    "exc",
    [
        RunNotFoundError("no such run"),
        ManifestReadError("read failed"),
        ManifestIncompatibleError("schema unsupported"),
        CorruptRunLinksError("key outside scope"),
        StorageKeyNotFound("storage object not found: k"),
        StorageBackendError("storage I/O error for key: k"),
        RuntimeError("some other backend-specific failure"),
    ],
)
def test_every_caught_exception_type_surfaces_as_clean_error_json(
    injected_ports, monkeypatch, exc
):
    """Every one of the ResultStore-level types, the local-backend storage
    types, and an arbitrary backend-specific exception (the Supabase
    backend's live lookups aren't type-safe the way the local backend's
    are) all surface as {"error": ...} -- never a raw traceback, and never
    exc's raw, unredacted text (PR #611 review finding: an earlier version
    returned str(exc) verbatim)."""
    _reader, store = injected_ports

    def _boom(*_a, **_k):
        raise exc

    monkeypatch.setattr(store, "get_download_links", _boom)

    result = get_download_links_mod.get_download_links(_EXPERIMENT, "qc", "latest")
    payload = json.loads(result)

    assert payload == {"error": safe_error_text(exc)}


def test_exception_text_is_redacted_not_returned_raw(injected_ports, monkeypatch):
    """A credential-shaped fragment in a live storage failure's text (fully
    plausible for a real storage3/httpx exception, unlike the local
    backend's typed errors) must not reach the caller verbatim."""
    _reader, store = injected_ports

    def _boom(*_a, **_k):
        raise RuntimeError("upstream said: Authorization: Bearer sk-live-abc123")

    monkeypatch.setattr(store, "get_download_links", _boom)

    result = get_download_links_mod.get_download_links(_EXPERIMENT, "qc", "latest")
    payload = json.loads(result)

    assert "sk-live-abc123" not in payload["error"]
    assert "<redacted>" in payload["error"]


def test_empty_experiment_string_produces_clean_error_not_a_raw_exception(
    injected_ports,
):
    result = get_download_links_mod.get_download_links("", "qc", "latest")
    payload = json.loads(result)

    assert "error" in payload


def test_dispatches_through_fastmcp_by_keyword(injected_ports):
    """Every other test here calls `get_download_links` as a raw Python
    function — none proves FastMCP's own schema-derived dispatch resolves
    correctly through the actual registered tool."""
    import asyncio

    from fastmcp import Client

    from bloom_mcp import server

    _reader, store = injected_ports
    _commit_a_run(store)

    async def _call():
        async with Client(server.mcp) as client:
            result = await client.call_tool(
                "core_get_download_links",
                {"experiment": _EXPERIMENT, "tool_class": "qc"},
            )
            return result.data

    payload = json.loads(asyncio.run(_call()))
    assert payload["experiment"] == _EXPERIMENT
    assert payload["output_links"]["cleaned"]["url"]
