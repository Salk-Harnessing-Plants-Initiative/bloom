"""Contract + structural tests for the granular ``umap_analysis`` tool (Tier 4 / #425).

UMAP is not bit-reproducible cross-platform (numba backend), so unlike ``pca_analysis``
there is no golden embedding to pin against. The oracle here is structural (shape, no
non-finite values, sample count preserved) plus within-run determinism (same seed → same
embedding), around the same 5 contract patterns and consumer guarantees ``pca_analysis``/
``clustering`` already establish: it reads a *cleaned* experiment (``require_clean=True``),
selects only certified-clean traits, delegates ALL UMAP to
``sleap_roots_analyze.perform_umap_analysis`` (typed via ``UMAPResult.from_umap_dict``), is
stochastic (seed resolved + recorded, never ``None`` — the ``clustering`` precedent, not
``pca_analysis``'s ``seed=None``), and persists a versioned ``umap`` run — returning a
shape/seed summary + links, never the embedding matrix inline.

The unit tests seed a cleaned version directly via ``FakeReader.add_cleaned_version`` (the
reader/store fakes are disjoint), so these tests do not depend on a live ``qc_clean`` run.
``injected_ports`` defaults ``perform_umap_analysis`` to ``_fake_perform_umap_analysis`` — a
fast, structurally-valid stand-in — so guard/plumbing tests don't pay for a real
numba/umap-learn fit. A small, fixed number of tests restore the genuine delegate (via
``_REAL_PERFORM_UMAP_ANALYSIS``) where real UMAP numerics are the actual point of the test:
the structural/shape characterization test, the same-seed-determinism test, the
n_components-near-n_samples boundary test, the n_samples=3/n_neighbors=2 eigensolver-failure
test, and one plot round-trip test — not marked ``@pytest.mark.integration`` per design.md's
"Real-delegate tests run in the fast lane".
"""

from __future__ import annotations

import asyncio
import io
import json
import math
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest
from bloom_mcp.contract import BloomMCPError
from bloom_mcp.data_access import FakeReader, SupabaseReader
from bloom_mcp.result_store import FakeResultStore, RunStateError, SupabaseResultStore
from bloom_mcp.sections.sleap_roots.analysis import umap_analysis as umap_analysis_tool
from bloom_mcp.sections.sleap_roots.analysis.umap_analysis import (
    UMAPAnalysisParams,
    UMAPAnalysisResult,
    umap_analysis,
)
from bloom_mcp.tools import _ports

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_FINAL = _FIXTURES / "turface_19_final_data.csv"
_PCA_GOLDEN = json.loads((_FIXTURES / "turface_19_pca_golden.json").read_text())

_EXPERIMENT = "turface_19.csv"
_TRAITS = _PCA_GOLDEN["trait_cols"]  # the recorded 8-trait certified selection
_SEED = 42


def _final_df() -> pd.DataFrame:
    return pd.read_csv(_FINAL)


# Captured at import time, before any test monkeypatches ``umap_analysis_tool``'s module
# attribute — this is the genuine, upstream ``perform_umap_analysis``. Tests that need real
# UMAP numerics restore it explicitly via
# ``monkeypatch.setattr(umap_analysis_tool, "perform_umap_analysis", _REAL_PERFORM_UMAP_ANALYSIS)``.
_REAL_PERFORM_UMAP_ANALYSIS = umap_analysis_tool.perform_umap_analysis


def _fake_perform_umap_analysis(
    df, *, feature_cols, n_neighbors, min_dist, n_components, random_state
):
    """Deterministic, cheap stand-in for the real (numba-backed) UMAP fit.

    Matches ``perform_umap_analysis``'s return contract (embedding/n_neighbors/min_dist/
    feature_names/random_state, plus a truthy ``scaler`` so ``UMAPResult.from_umap_dict``
    derives ``standardized=True``) without paying for a real embedding — used by default so
    guard/plumbing tests don't exercise actual UMAP numerics they don't need. ``embedding``
    is a real ``np.ndarray`` (not a plain nested list): the real plotters
    (``create_umap_single_trait``/``create_umap_colored_by_top_traits``) index the raw
    delegate dict directly and call ``.shape`` on it, same as the genuine delegate's output.
    """
    n = len(df)
    embedding = np.array(
        [[float(i) + 0.1 * c for c in range(n_components)] for i in range(n)]
    )
    return {
        "embedding": embedding,
        "reducer": None,
        "scaler": object(),
        "n_neighbors": n_neighbors,
        "min_dist": min_dist,
        "feature_names": list(feature_cols),
        "random_state": random_state,
    }


@pytest.fixture
def injected_ports(monkeypatch):
    """FakeReader serving the cleaned fixture as a cleaned version + FakeResultStore.

    Defaults ``perform_umap_analysis`` to ``_fake_perform_umap_analysis``; tests that need
    the genuine delegate restore ``_REAL_PERFORM_UMAP_ANALYSIS`` explicitly.
    """
    reader = FakeReader()
    store = FakeResultStore()
    reader.add_cleaned_version(_EXPERIMENT, "v1", _final_df(), make_latest=True)
    _ports.configure(reader=reader, store=store)
    monkeypatch.setattr(
        umap_analysis_tool, "perform_umap_analysis", _fake_perform_umap_analysis
    )
    try:
        yield reader, store
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())


def _run(**overrides) -> UMAPAnalysisResult:
    params = {"experiment": _EXPERIMENT, "trait_columns": _TRAITS, **overrides}
    return umap_analysis(UMAPAnalysisParams(**params))


def _capture_staged_bytes(store, monkeypatch) -> dict[str, bytes]:
    """Read each staged PNG artifact's raw bytes at commit time."""
    captured: dict[str, bytes] = {}
    real_commit = store.commit

    def _commit(run, outputs):
        for name in outputs:
            p = run.staging_dir / name
            if p.suffix == ".png":
                captured[name] = p.read_bytes()
        return real_commit(run, outputs)

    monkeypatch.setattr(store, "commit", _commit)
    return captured


def _capture_staged_outputs(store, monkeypatch) -> dict[str, str]:
    """Read each staged text artifact's content at commit time."""
    captured: dict[str, str] = {}
    real_commit = store.commit

    def _commit(run, outputs):
        for name in outputs:
            p = run.staging_dir / name
            if p.suffix != ".png":
                captured[name] = p.read_text()
        return real_commit(run, outputs)

    monkeypatch.setattr(store, "commit", _commit)
    return captured


# ── Structural oracle (no golden — see module docstring) ────────────────────


def test_umap_through_the_tool(injected_ports, monkeypatch):
    """Real delegate: structural shape, finite values, matching feature names."""
    monkeypatch.setattr(
        umap_analysis_tool, "perform_umap_analysis", _REAL_PERFORM_UMAP_ANALYSIS
    )
    result = _run()
    assert result.n_samples == len(_final_df()) == 153
    assert result.n_components == 2
    assert result.feature_names == _TRAITS


def test_same_seed_identical_embedding(injected_ports, monkeypatch):
    """Same seed + inputs → element-wise identical embedding (real delegate)."""
    monkeypatch.setattr(
        umap_analysis_tool, "perform_umap_analysis", _REAL_PERFORM_UMAP_ANALYSIS
    )
    _reader, store = injected_ports
    captured = _capture_staged_outputs(store, monkeypatch)
    _run(seed=_SEED)
    first = json.loads(captured["umap_result.json"])["embedding"]
    _run(seed=_SEED)
    second = json.loads(captured["umap_result.json"])["embedding"]
    assert first == second


def test_no_silent_sample_loss(injected_ports):
    result = _run()
    assert result.n_samples == len(_final_df())
    assert result.n_features == len(_TRAITS) == 8


# ── tools/list registration (namespaced sleap_roots_umap_analysis) ──────────


def test_umap_analysis_registered_in_sleap_roots_section():
    """Discoverable via the combined server's real MCP transport, namespaced by its
    section (no legacy run_dimensionality_reduction_workflow — that tool was retired by
    devendor-bloommcp-analysis)."""
    from bloom_mcp import server
    from fastmcp import Client

    async def _list():
        async with Client(server.mcp) as client:
            return await client.list_tools()

    tools = {t.name: t for t in asyncio.run(_list())}
    assert "sleap_roots_umap_analysis" in tools
    assert tools["sleap_roots_umap_analysis"].inputSchema is not None


# ── delegation pinning (spy) ──────────────────────────────────────────────────


def test_delegates_once_and_never_computes_umap_itself(injected_ports, monkeypatch):
    captured = {}
    real = umap_analysis_tool.perform_umap_analysis

    def _spy(selected, **kwargs):
        captured["n"] = captured.get("n", 0) + 1
        captured["columns"] = list(selected.columns)
        captured["random_state"] = kwargs.get("random_state")
        return real(selected, **kwargs)

    monkeypatch.setattr(umap_analysis_tool, "perform_umap_analysis", _spy)
    _run(seed=7)

    assert captured["n"] == 1
    assert captured["columns"] == _TRAITS
    assert captured["random_state"] == 7


# ── stochastic seed: resolved, default, recorded in provenance ──────────────


def test_provenance_records_resolved_seed(injected_ports):
    _reader, store = injected_ports
    _run(seed=7)
    stored = store.get_run(_EXPERIMENT, "umap", "latest")
    assert stored.tool == "umap_analysis"
    assert stored.seed == 7  # never None — UMAP is always stochastic here


def test_default_seed_is_42(injected_ports):
    result = _run()
    assert result.seed == 42


# ── n_neighbors: pre-dispatch guard vs Pydantic parameter bounds ────────────


def test_n_neighbors_at_or_above_n_samples_is_assumption_violated(
    injected_ports, monkeypatch
):
    _reader, store = injected_ports
    small = pd.DataFrame({"tA": [1.0, 2.0, 3.0, 4.0], "tB": [2.0, 1.0, 4.0, 3.0]})
    _reader.add_cleaned_version("small.csv", "v1", small, make_latest=True)

    def _spy(*a, **k):  # pragma: no cover - must not run
        raise AssertionError("delegate called despite n_neighbors >= n_samples")

    monkeypatch.setattr(umap_analysis_tool, "perform_umap_analysis", _spy)

    with pytest.raises(BloomMCPError) as exc:
        umap_analysis(UMAPAnalysisParams(experiment="small.csv", n_neighbors=4))
    assert exc.value.code == "assumption_violated"
    msg = f"{exc.value.message} {exc.value.remedy}"
    assert "4" in msg and "3" in msg  # requested value and max usable (n_samples - 1)
    assert store.list_runs("small.csv", "umap") == []


def test_n_neighbors_below_n_samples_boundary_succeeds(injected_ports):
    small = pd.DataFrame(
        {
            "tA": [1.0, 2.0, 3.0, 4.0, 5.0],
            "tB": [2.0, 1.0, 4.0, 3.0, 9.0],
        }
    )
    reader, _store = injected_ports
    reader.add_cleaned_version("small.csv", "v1", small, make_latest=True)
    result = umap_analysis(
        UMAPAnalysisParams(experiment="small.csv", n_neighbors=4)  # n_samples - 1
    )
    assert result.n_samples == 5


def test_n_neighbors_one_is_invalid_input(injected_ports, monkeypatch):
    """n_neighbors=1 is never valid for any data — umap-learn itself hard-rejects it
    ("n_neighbors must be greater than 1"), independent of n_samples (verified directly:
    fails identically on 2-sample and 10-sample inputs). Caught at the Pydantic layer
    (ge=2) as a caller mistake, not left to surface as a delegate-caught
    assumption_violated for something that was never data-dependent."""

    def _spy(*a, **k):  # pragma: no cover - must not run
        raise AssertionError("delegate called with n_neighbors=1")

    monkeypatch.setattr(umap_analysis_tool, "perform_umap_analysis", _spy)
    with pytest.raises(BloomMCPError) as exc:
        umap_analysis({"experiment": _EXPERIMENT, "n_neighbors": 1})
    assert exc.value.code == "invalid_input"


def test_degenerate_small_n_neighbors_eigensolver_failure_is_assumption_violated(
    injected_ports, monkeypatch
):
    """Real delegate: n_samples=3, n_neighbors=2 (the tightest boundary the ge=2/
    n_neighbors<n_samples guards still let through) trips umap-learn's spectral-embedding
    eigensolver into raising a bare TypeError, not ValueError/KeyError/RuntimeError
    (verified directly against the installed umap-learn). Confirms the except clause's
    TypeError addition maps this to a structured, non-leaking assumption_violated rather
    than an opaque internal_error."""
    monkeypatch.setattr(
        umap_analysis_tool, "perform_umap_analysis", _REAL_PERFORM_UMAP_ANALYSIS
    )
    reader, store = injected_ports
    three = pd.DataFrame({"tA": [1.0, 2.0, 3.0], "tB": [2.0, 1.0, 4.0]})
    reader.add_cleaned_version("three.csv", "v1", three, make_latest=True)
    with pytest.raises(BloomMCPError) as exc:
        umap_analysis(UMAPAnalysisParams(experiment="three.csv", n_neighbors=2))
    assert exc.value.code == "assumption_violated"
    msg = f"{exc.value.message} {exc.value.remedy}"
    assert "scipy.linalg.eigh" not in msg and "sparse A" not in msg
    assert store.list_runs("three.csv", "umap") == []


def test_n_neighbors_non_positive_is_invalid_input(injected_ports, monkeypatch):
    def _spy(*a, **k):  # pragma: no cover - must not run
        raise AssertionError("delegate called with non-positive n_neighbors")

    monkeypatch.setattr(umap_analysis_tool, "perform_umap_analysis", _spy)
    with pytest.raises(BloomMCPError) as exc:
        umap_analysis({"experiment": _EXPERIMENT, "n_neighbors": 0})
    assert exc.value.code == "invalid_input"


def test_min_dist_negative_is_invalid_input(injected_ports, monkeypatch):
    def _spy(*a, **k):  # pragma: no cover - must not run
        raise AssertionError("delegate called with negative min_dist")

    monkeypatch.setattr(umap_analysis_tool, "perform_umap_analysis", _spy)
    with pytest.raises(BloomMCPError) as exc:
        umap_analysis({"experiment": _EXPERIMENT, "min_dist": -0.1})
    assert exc.value.code == "invalid_input"


def test_n_components_below_one_is_invalid_input(injected_ports, monkeypatch):
    def _spy(*a, **k):  # pragma: no cover - must not run
        raise AssertionError("delegate called with n_components below 1")

    monkeypatch.setattr(umap_analysis_tool, "perform_umap_analysis", _spy)
    with pytest.raises(BloomMCPError) as exc:
        umap_analysis({"experiment": _EXPERIMENT, "n_components": 0})
    assert exc.value.code == "invalid_input"


def test_n_components_above_max_is_invalid_input(injected_ports, monkeypatch):
    """n_components has an upper sanity bound (le=50) even though UMAP has no natural
    ceiling: this is an LLM-driven, low-trust input surface, and nothing else stops a
    request like n_components=10_000_000 from risking an OOM before any Python-level
    handler could intervene."""

    def _spy(*a, **k):  # pragma: no cover - must not run
        raise AssertionError("delegate called with an out-of-range n_components")

    monkeypatch.setattr(umap_analysis_tool, "perform_umap_analysis", _spy)
    with pytest.raises(BloomMCPError) as exc:
        umap_analysis({"experiment": _EXPERIMENT, "n_components": 51})
    assert exc.value.code == "invalid_input"


def test_n_components_equals_max_succeeds(injected_ports):
    """The le=50 ceiling's own boundary: 50 must succeed (only 51 is rejected, 2.8e) —
    otherwise an off-by-one in the Pydantic constraint would silently reject a legitimate
    edge value."""
    result = _run(n_components=50)
    assert result.n_components == 50


def test_n_components_equals_one_succeeds(injected_ports):
    result = _run(n_components=1)
    assert result.n_components == 1


def test_n_components_near_sample_count_is_handled_safely(injected_ports, monkeypatch):
    """Real delegate: n_components close to n_samples on a tiny fixture must never
    surface as an unhandled internal_error or leak backend text. Observed behavior for
    this exact fixture/seed/params is a successful, finite embedding (verified directly
    against the installed sleap_roots_analyze); pinned to that outcome specifically so a
    future dependency bump that silently changes this boundary's behavior is caught
    rather than masked by an overly permissive either-branch assertion."""
    monkeypatch.setattr(
        umap_analysis_tool, "perform_umap_analysis", _REAL_PERFORM_UMAP_ANALYSIS
    )
    reader, _store = injected_ports
    small = pd.DataFrame(
        {
            "tA": [1.0, 2.0, 3.0, 4.0, 5.0],
            "tB": [2.0, 1.0, 4.0, 3.0, 9.0],
        }
    )
    reader.add_cleaned_version("tiny.csv", "v1", small, make_latest=True)
    result = umap_analysis(
        UMAPAnalysisParams(experiment="tiny.csv", n_neighbors=2, n_components=4)
    )
    assert result.n_components == 4
    assert result.n_samples == 5


# ── trait-column validation ──────────────────────────────────────────────────


def test_unknown_trait_column_is_invalid_input_naming_it(injected_ports):
    with pytest.raises(BloomMCPError) as exc:
        _run(trait_columns=["NoSuchTrait"])
    assert exc.value.code == "invalid_input"
    assert "NoSuchTrait" in exc.value.message


def test_non_certified_numeric_column_is_rejected_not_dropped(
    injected_ports, monkeypatch
):
    def _spy(*a, **k):  # pragma: no cover - must not run
        raise AssertionError("delegate called with a non-certified column")

    monkeypatch.setattr(umap_analysis_tool, "perform_umap_analysis", _spy)
    with pytest.raises(BloomMCPError) as exc:
        _run(trait_columns=["Replicate"])
    assert exc.value.code == "invalid_input"
    assert "Replicate" in exc.value.message


def test_empty_trait_columns_is_invalid_input(injected_ports, monkeypatch):
    _reader, store = injected_ports

    def _spy(*a, **k):  # pragma: no cover - must not run
        raise AssertionError("delegate called on an empty selection")

    monkeypatch.setattr(umap_analysis_tool, "perform_umap_analysis", _spy)
    with pytest.raises(BloomMCPError) as exc:
        _run(trait_columns=[])
    assert exc.value.code == "invalid_input"
    assert store.list_runs(_EXPERIMENT, "umap") == []


def test_duplicate_trait_columns_is_invalid_input_naming_them(
    injected_ports, monkeypatch
):
    _reader, store = injected_ports

    def _spy(*a, **k):  # pragma: no cover - must not run
        raise AssertionError("delegate called with duplicate columns")

    monkeypatch.setattr(umap_analysis_tool, "perform_umap_analysis", _spy)
    with pytest.raises(BloomMCPError) as exc:
        _run(trait_columns=[_TRAITS[0], _TRAITS[0]])
    assert exc.value.code == "invalid_input"
    assert _TRAITS[0] in exc.value.message
    assert store.list_runs(_EXPERIMENT, "umap") == []


# ── finite-input guard + degenerate/non-finite-output guards ────────────────


def test_non_finite_certified_trait_is_rejected(injected_ports, monkeypatch):
    reader, store = injected_ports
    inf_df = pd.DataFrame(
        {
            "tA": [1.0, 2.0, 3.0, 4.0],
            "tB": [4.0, float("inf"), 2.0, 1.0],
        }
    )
    reader.add_cleaned_version("inf.csv", "v1", inf_df, make_latest=True)

    def _spy(*a, **k):  # pragma: no cover - must not run
        raise AssertionError("delegate called with a non-finite value")

    monkeypatch.setattr(umap_analysis_tool, "perform_umap_analysis", _spy)
    with pytest.raises(BloomMCPError) as exc:
        umap_analysis(UMAPAnalysisParams(experiment="inf.csv"))
    assert exc.value.code == "assumption_violated"
    assert store.list_runs("inf.csv", "umap") == []


@pytest.mark.parametrize(
    "exc_type,exc_msg",
    [
        (ValueError, "secret path /var/secrets/key and host db.internal"),
        (KeyError, "'embedding'"),
        (RuntimeError, "internal umap-learn state error"),
        (TypeError, "unhashable type in eigensolver"),
    ],
)
def test_degenerate_fit_does_not_leak_backend_internals(
    injected_ports, monkeypatch, exc_type, exc_msg
):
    """All four exception types the delegate call's except clause names must each map
    to a non-leaking, structured assumption_violated — not just ValueError."""
    _reader, store = injected_ports

    def _boom(*a, **k):
        raise exc_type(exc_msg)

    monkeypatch.setattr(umap_analysis_tool, "perform_umap_analysis", _boom)
    with pytest.raises(BloomMCPError) as exc:
        _run()
    assert exc.value.code == "assumption_violated"
    msg = f"{exc.value.message} {exc.value.remedy}"
    assert "/var" not in msg and "db.internal" not in msg
    assert store.list_runs(_EXPERIMENT, "umap") == []


def test_undeclared_delegate_raise_is_scrubbed(injected_ports, monkeypatch):
    """bloom#664 item 2: an exception type outside the `(ValueError, KeyError,
    RuntimeError, TypeError)` except clause above falls through undeclared to
    `internal_error` — pinned, not just "doesn't leak" (mirrors the #660
    qc_inspect/qc_clean/remove_outliers pattern, closing the coverage gap for
    this tool)."""
    _reader, store = injected_ports

    def _boom(*a, **k):
        raise Exception("secret path /var/secrets/key and host db.internal")

    monkeypatch.setattr(umap_analysis_tool, "perform_umap_analysis", _boom)
    with pytest.raises(BloomMCPError) as exc:
        _run()
    assert exc.value.code == "internal_error"
    msg = f"{exc.value.message} {exc.value.remedy}"
    assert "/var" not in msg and "db.internal" not in msg
    assert store.list_runs(_EXPERIMENT, "umap") == []


def test_delegate_failure_logs_original_exception_at_debug_level(
    injected_ports, monkeypatch, caplog
):
    """The original exception type/message is captured server-side (debug level) before
    translation to assumption_violated — not leaked to the caller (see the no-leak test
    above), but not silently discarded either, so a genuinely new upstream failure mode is
    diagnosable without reproducing the call."""

    def _boom(*a, **k):
        raise ValueError("secret path /var/secrets/key and host db.internal")

    monkeypatch.setattr(umap_analysis_tool, "perform_umap_analysis", _boom)
    with (
        caplog.at_level(
            "DEBUG", logger="bloom_mcp.sections.sleap_roots.analysis.umap_analysis"
        ),
        pytest.raises(BloomMCPError),
    ):
        _run()
    assert any(
        "ValueError" in r.message and "db.internal" in r.message for r in caplog.records
    )


def test_non_finite_embedding_is_assumption_violated_before_persistence(
    injected_ports, monkeypatch
):
    _reader, store = injected_ports

    def _fake(selected, **kwargs):
        n = len(selected)
        return {
            "embedding": [[math.nan, 0.0]] + [[0.0, 0.0]] * (n - 1),
            "n_neighbors": kwargs.get("n_neighbors", 15),
            "min_dist": kwargs.get("min_dist", 0.1),
            "feature_names": list(selected.columns),
            "random_state": kwargs.get("random_state"),
        }

    monkeypatch.setattr(umap_analysis_tool, "perform_umap_analysis", _fake)
    with pytest.raises(BloomMCPError) as exc:
        _run()
    assert exc.value.code == "assumption_violated"
    assert store.list_runs(_EXPERIMENT, "umap") == []  # no orphaned staging dir either


# ── ResultStore write-path failures surface as tool_error, not a bare internal_error ref
# (#640: umap_analysis's declared errors=(ExperimentReadError,) swallowed a CommitFailedError/
# ManifestReadError from store.create_run()/commit() into a generic internal_error ref) ──


def test_commit_failure_surfaces_as_tool_error(injected_ports):
    _reader, store = injected_ports
    store.fail_next_commit(_EXPERIMENT, "umap")
    with pytest.raises(BloomMCPError) as exc:
        _run()
    assert exc.value.code == "tool_error"
    assert "commit failed for umap" in exc.value.message


def test_manifest_read_failure_surfaces_as_tool_error(injected_ports):
    _reader, store = injected_ports
    store.fail_next_read(_EXPERIMENT, "umap")
    with pytest.raises(BloomMCPError) as exc:
        _run()
    assert exc.value.code == "tool_error"
    assert "manifest read failure" in exc.value.message


def test_run_state_error_from_commit_still_maps_to_internal_error(
    injected_ports, monkeypatch
):
    """RunStateError (a handle-misuse/wiring bug, never triggerable via tool input) must
    stay internal_error even after declaring CommitFailedError/ManifestReadError — proves
    the errors= tuple wasn't accidentally widened to the full ResultStoreError base
    (design.md Decision 1; #660 review: only qc_inspect had this test)."""
    _reader, store = injected_ports

    def _boom(run, outputs):
        raise RunStateError("commit() on an unknown or already-committed run")

    monkeypatch.setattr(store, "commit", _boom)
    with pytest.raises(BloomMCPError) as exc:
        _run()
    assert exc.value.code == "internal_error"


# ── require_clean consumption ────────────────────────────────────────────────


def test_raw_only_experiment_is_rejected_with_qc_clean_remedy():
    reader = FakeReader()
    store = FakeResultStore()
    reader.add_experiment("rawonly.csv", _final_df())  # raw only, no cleaned version
    _ports.configure(reader=reader, store=store)
    try:
        with pytest.raises(BloomMCPError) as exc:
            umap_analysis(UMAPAnalysisParams(experiment="rawonly.csv"))
        assert "qc_clean" in exc.value.remedy.lower()
        assert store.list_runs("rawonly.csv", "umap") == []
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())


def test_consumes_cleaned_version_source(injected_ports):
    result = _run()
    assert result.source == "v1_cleaned"
    assert result.source != "raw"


# ── persistence: links not blobs, identity, versioning, lineage ─────────────


def test_persists_embedding_and_returns_links_not_the_vector(injected_ports):
    _reader, store = injected_ports
    result = _run()

    stored = store.get_run(_EXPERIMENT, "umap", "latest")
    assert set(stored.output_keys) == {"embedding_coords.csv", "umap_result.json"}
    assert set(result.outputs) == {"embedding_coords.csv", "umap_result.json"}
    assert result.run_ref == stored.run_ref
    assert result.manifest_path == stored.manifest_path

    # bloom#581: a signed link + hash + size per output.
    assert set(result.output_links) == set(result.outputs)
    for name, key in result.outputs.items():
        link = result.output_links[name]
        assert link.key == key
        assert link.url
        assert link.sha256 == stored.output_sha256[name]
        assert link.size_bytes >= 0
    assert stored.output_links == {}

    assert not hasattr(result, "embedding")
    dumped = result.model_dump()
    assert not any(
        isinstance(v, (list, dict)) and len(str(v)) > 5000 for v in dumped.values()
    )


def test_embedding_csv_carries_sample_identity(injected_ports, monkeypatch):
    _reader, store = injected_ports
    captured = _capture_staged_outputs(store, monkeypatch)
    _run()

    embedding = pd.read_csv(io.StringIO(captured["embedding_coords.csv"]))
    assert list(embedding.columns[:3]) == ["Barcode", "Genotype", "Replicate"]
    umap_cols = [c for c in embedding.columns if c.startswith("UMAP")]
    assert umap_cols == ["UMAP1", "UMAP2"]
    final = _final_df()
    assert embedding["Barcode"].tolist() == final["Barcode"].tolist()
    assert len(embedding) == len(final) == 153


def test_second_run_increments_version(injected_ports):
    _reader, store = injected_ports
    _run()
    _run()
    assert [r.run_ref for r in store.list_runs(_EXPERIMENT, "umap")] == ["v1", "v2"]
    assert store.get_run(_EXPERIMENT, "umap", "latest").run_ref == "v2"


def test_passes_source_csv_for_input_lineage(injected_ports, monkeypatch):
    _reader, store = injected_ports
    captured: dict[str, object] = {}
    real_create = store.create_run

    def _spy(**kwargs):
        src = kwargs.get("source_csv")
        captured["source_csv"] = src
        captured["exists"] = src is not None and Path(src).exists()
        return real_create(**kwargs)

    monkeypatch.setattr(store, "create_run", _spy)
    _run()

    assert captured["source_csv"] is not None
    assert captured["exists"]


def test_standardized_is_always_true(injected_ports):
    result = _run()
    assert result.standardized is True


def test_valid_input_output_round_trip(injected_ports):
    result = _run()
    again = UMAPAnalysisResult.model_validate(json.loads(result.model_dump_json()))
    assert again.n_samples == result.n_samples
    assert again.feature_names == result.feature_names


# ── Plots ─────────────────────────────────────────────────────────────────────

_ALL_PLOT_KEYS = {"create_umap_single_trait", "create_umap_colored_by_top_traits"}


def test_default_no_plots_outputs_unchanged(injected_ports):
    result = _run()
    assert set(result.outputs) == {"embedding_coords.csv", "umap_result.json"}
    assert not any(k.endswith(".png") for k in result.outputs)


def test_default_path_never_executes_an_import_matplotlib_statement(
    injected_ports, monkeypatch
):
    """Regression guard, corrected framing: this does NOT prove matplotlib is absent from
    ``sys.modules`` — it already is resident by the time this test runs, via this module's
    own transitive ``sleap_roots_analyze`` import (see the module docstring). What this
    proves is narrower but still real: the ``include_plots=False`` code path never itself
    executes a fresh ``import matplotlib`` statement. Setting the ``sys.modules`` entry to
    ``None`` makes any such statement raise ``ImportError`` immediately, so a passing test
    here means no such statement was reached."""
    import sys

    monkeypatch.setitem(sys.modules, "matplotlib", None)
    _run()  # must not raise ImportError


def test_unknown_plot_key_invalid_input_no_run_committed(injected_ports):
    _reader, store = injected_ports
    with pytest.raises(BloomMCPError) as exc:
        _run(include_plots=True, plots=["not_a_real_plot"])
    assert exc.value.code == "invalid_input"
    assert "not_a_real_plot" in exc.value.message
    assert store.list_runs(_EXPERIMENT, "umap") == []


def test_duplicate_plot_key_invalid_input_no_run_committed(injected_ports):
    _reader, store = injected_ports
    with pytest.raises(BloomMCPError) as exc:
        _run(
            include_plots=True,
            plots=["create_umap_single_trait", "create_umap_single_trait"],
        )
    assert exc.value.code == "invalid_input"
    assert "create_umap_single_trait" in exc.value.message
    assert store.list_runs(_EXPERIMENT, "umap") == []


def test_empty_plots_list_is_invalid_input(injected_ports):
    _reader, store = injected_ports
    with pytest.raises(BloomMCPError) as exc:
        _run(include_plots=True, plots=[])
    assert exc.value.code == "invalid_input"
    assert store.list_runs(_EXPERIMENT, "umap") == []


def test_include_plots_false_with_plots_param_is_silently_ignored(injected_ports):
    result = _run(include_plots=False, plots=["create_umap_single_trait"])
    assert not any(k.endswith(".png") for k in result.outputs)
    assert set(result.outputs) == {"embedding_coords.csv", "umap_result.json"}


def test_umap_single_trait_plot_png_round_trip(injected_ports, monkeypatch):
    """Real delegate: one genuine embedding through one real plotter, guarding against
    silent plotter-API drift (design.md's "Plotter API drift" risk)."""
    monkeypatch.setattr(
        umap_analysis_tool, "perform_umap_analysis", _REAL_PERFORM_UMAP_ANALYSIS
    )
    _reader, store = injected_ports
    staged = _capture_staged_bytes(store, monkeypatch)
    result = _run(include_plots=True, plots=["create_umap_single_trait"])

    assert set(result.outputs) == {
        "embedding_coords.csv",
        "umap_result.json",
        "create_umap_single_trait.png",
    }
    assert staged["create_umap_single_trait.png"][:4] == b"\x89PNG"


def test_umap_colored_by_top_traits_plot_png_round_trip(injected_ports, monkeypatch):
    _reader, store = injected_ports
    staged = _capture_staged_bytes(store, monkeypatch)

    captured = {}
    real_pca = umap_analysis_tool.perform_pca_analysis

    def _spy(selected, *a, **k):
        captured["n"] = captured.get("n", 0) + 1
        captured["columns"] = list(selected.columns)
        return real_pca(selected, *a, **k)

    monkeypatch.setattr(umap_analysis_tool, "perform_pca_analysis", _spy)

    result = _run(include_plots=True, plots=["create_umap_colored_by_top_traits"])

    assert staged["create_umap_colored_by_top_traits.png"][:4] == b"\x89PNG"
    assert captured["n"] == 1
    assert store.list_runs(_EXPERIMENT, "pca") == []  # no second run committed
    assert "create_umap_colored_by_top_traits.png" in result.outputs


def test_top_traits_plot_internal_pca_uses_same_trait_selection(
    injected_ports, monkeypatch
):
    captured = {}
    real_pca = umap_analysis_tool.perform_pca_analysis

    def _spy(selected, *a, **k):
        captured["columns"] = list(selected.columns)
        return real_pca(selected, *a, **k)

    monkeypatch.setattr(umap_analysis_tool, "perform_pca_analysis", _spy)
    _run(include_plots=True, plots=["create_umap_colored_by_top_traits"])
    assert captured["columns"] == _TRAITS


@pytest.mark.parametrize(
    "exc_type",
    [ValueError, KeyError, RuntimeError, TypeError],
)
def test_top_traits_internal_pca_failure_is_assumption_violated(
    injected_ports, monkeypatch, exc_type
):
    """The internal, non-persisted perform_pca_analysis call gets the same
    assumption_violated translation (and the same widened exception tuple) as the main
    UMAP delegate call — not left to surface as an opaque internal_error."""
    _reader, store = injected_ports

    def _boom(*a, **k):
        raise exc_type("secret backend detail /var/lib/whatever")

    monkeypatch.setattr(umap_analysis_tool, "perform_pca_analysis", _boom)
    with pytest.raises(BloomMCPError) as exc:
        _run(include_plots=True, plots=["create_umap_colored_by_top_traits"])
    assert exc.value.code == "assumption_violated"
    msg = f"{exc.value.message} {exc.value.remedy}"
    assert "/var" not in msg
    assert store.list_runs(_EXPERIMENT, "umap") == []


def test_top_traits_internal_pca_failure_logs_original_exception(
    injected_ports, monkeypatch, caplog
):
    """Same server-side diagnosability guarantee as the main delegate call (see
    test_delegate_failure_logs_original_exception_at_debug_level), applied to the
    internal, non-persisted perform_pca_analysis call."""

    def _boom(*a, **k):
        raise ValueError("secret backend detail /var/lib/whatever")

    monkeypatch.setattr(umap_analysis_tool, "perform_pca_analysis", _boom)
    with (
        caplog.at_level(
            "DEBUG", logger="bloom_mcp.sections.sleap_roots.analysis.umap_analysis"
        ),
        pytest.raises(BloomMCPError),
    ):
        _run(include_plots=True, plots=["create_umap_colored_by_top_traits"])
    assert any(
        "ValueError" in r.message and "/var/lib/whatever" in r.message
        for r in caplog.records
    )


def test_plots_subset_generates_only_requested(injected_ports, monkeypatch):
    _reader, store = injected_ports
    staged = _capture_staged_bytes(store, monkeypatch)
    result = _run(include_plots=True, plots=["create_umap_single_trait"])

    png_keys = {k for k in result.outputs if k.endswith(".png")}
    assert png_keys == {"create_umap_single_trait.png"}
    for key in png_keys:
        assert staged[key][:4] == b"\x89PNG"


def test_figure_cleanup_get_fignums_empty_on_success(injected_ports):
    import matplotlib.pyplot as plt

    _run(include_plots=True, plots=None)
    assert plt.get_fignums() == []


def test_figure_cleanup_get_fignums_empty_on_invalid_key(injected_ports):
    import matplotlib.pyplot as plt

    with pytest.raises(BloomMCPError):
        _run(include_plots=True, plots=["bad_key"])
    assert plt.get_fignums() == []


def test_figure_cleanup_get_fignums_empty_on_partial_plotter_failure(
    injected_ports, monkeypatch
):
    import matplotlib.pyplot as plt

    real = umap_analysis_tool._umap_plot_calls

    def _boom(*a, **k):
        raise RuntimeError("second plotter blew up")

    def _patched(result_dict, frame, trait_cols):
        calls = real(result_dict, frame, trait_cols)
        calls["create_umap_colored_by_top_traits"] = _boom
        return calls

    monkeypatch.setattr(umap_analysis_tool, "_umap_plot_calls", _patched)

    with pytest.raises(BloomMCPError):
        _run(
            include_plots=True,
            plots=["create_umap_single_trait", "create_umap_colored_by_top_traits"],
        )
    assert plt.get_fignums() == []


def test_plot_outputs_included_in_schema_round_trip(injected_ports):
    result = _run(include_plots=True, plots=["create_umap_single_trait"])
    again = UMAPAnalysisResult.model_validate(json.loads(result.model_dump_json()))
    assert "create_umap_single_trait.png" in again.outputs


# ── Font-style override (#661) ───────────────────────────────────────────────


def test_plot_font_family_and_size_forwarded_and_applied(injected_ports, monkeypatch):
    """plot_font_family/plot_font_size flow from UMAPAnalysisParams through
    generate_figures and are applied to the generated figure before it's saved."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    captured = {}

    def _fake_calls(result_dict, frame, trait_cols):
        def _make():
            fig, ax = plt.subplots()
            ax.set_title("t")
            ax.set_xlabel("x")
            captured["fig"] = fig
            return fig

        return {"create_umap_single_trait": _make}

    monkeypatch.setattr(umap_analysis_tool, "_umap_plot_calls", _fake_calls)

    _run(
        include_plots=True,
        plots=["create_umap_single_trait"],
        plot_font_family="serif",
        plot_font_size=22,
    )

    fig = captured["fig"]
    assert fig.axes[0].title.get_fontfamily() == ["serif"]
    assert fig.axes[0].title.get_fontsize() == 22


def test_plot_font_size_non_positive_is_invalid_input(injected_ports):
    _reader, store = injected_ports
    with pytest.raises(BloomMCPError) as exc:
        umap_analysis({"experiment": _EXPERIMENT, "plot_font_size": -1})
    assert exc.value.code == "invalid_input"
    assert store.list_runs(_EXPERIMENT, "umap") == []


def test_plot_font_fields_ignored_when_include_plots_false(injected_ports):
    result = _run(include_plots=False, plot_font_family="serif", plot_font_size=22)
    assert not any(k.endswith(".png") for k in result.outputs)


def test_plot_font_size_just_above_zero_is_accepted():
    assert (
        UMAPAnalysisParams(experiment="x.csv", plot_font_size=0.01).plot_font_size
        == 0.01
    )


def test_plots_subset_with_font_override_never_generates_non_requested_plots(
    injected_ports, monkeypatch
):
    """A plots=[subset] request must generate — and therefore only font-style — the
    requested catalog plot(s); a non-requested plotter must never even be called, so
    it can't be affected by the override either."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    called = {"single_trait": 0, "top_traits": 0}

    def _fake_calls(result_dict, frame, trait_cols):
        def _single_trait():
            called["single_trait"] += 1
            fig, ax = plt.subplots()
            ax.set_title("single trait")
            return fig

        def _top_traits():  # pragma: no cover - must not run
            called["top_traits"] += 1
            raise AssertionError("non-requested plotter was called")

        return {
            "create_umap_single_trait": _single_trait,
            "create_umap_colored_by_top_traits": _top_traits,
        }

    monkeypatch.setattr(umap_analysis_tool, "_umap_plot_calls", _fake_calls)

    result = _run(
        include_plots=True,
        plots=["create_umap_single_trait"],
        plot_font_family="serif",
    )

    assert called == {"single_trait": 1, "top_traits": 0}
    png_keys = {k for k in result.outputs if k.endswith(".png")}
    assert png_keys == {"create_umap_single_trait.png"}


# ── explicit cleaned-version selector (#626) ────────────────────────────────


def test_version_field_exists():
    assert "version" in UMAPAnalysisParams.model_fields


def test_omitting_version_preserves_todays_exact_call(injected_ports):
    reader, _store = injected_ports
    reader.load_experiment = MagicMock(wraps=reader.load_experiment)

    _run()

    reader.load_experiment.assert_called_once_with(_EXPERIMENT, require_clean=True)


def test_explicit_version_is_passed_through(injected_ports):
    reader, _store = injected_ports
    reader.add_cleaned_version(_EXPERIMENT, "v2", _final_df(), make_latest=False)
    reader.load_experiment = MagicMock(wraps=reader.load_experiment)

    _run(version="v2")

    reader.load_experiment.assert_called_once_with(
        _EXPERIMENT, require_clean=True, version="v2"
    )


# ── discoverable via list_existing_analyses (bloom#669) ─────────────────────


def test_discoverable_via_list_existing_analyses(injected_ports):
    """Live discoverability, mirroring the same pattern
    remove_outliers/cross_experiment_correlations use for their own registered class."""
    from bloom_mcp.sections.core import (
        list_existing_analyses as list_existing_analyses_mod,
    )

    list_existing_analyses_mod._RESPONSE_CACHE.clear()
    try:
        _run()
        response = json.loads(
            list_existing_analyses_mod.list_existing_analyses(_EXPERIMENT)
        )
    finally:
        list_existing_analyses_mod._RESPONSE_CACHE.clear()

    assert "umap" in response["analyses"]
