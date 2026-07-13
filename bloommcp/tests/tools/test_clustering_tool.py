"""Contract + oracle tests for the granular ``clustering`` tool (Tier 5 / #309).

The clustering oracle is **determinism** (same seed → element-wise identical
``cluster_labels``) — a genuine invariant, asserted per method — plus a *characterization
snapshot* of the cluster metrics through the tool (a drift gate, not an independent oracle;
no external clustering oracle exists for turface_19). Around that: the 5 contract patterns,
the **polymorphic** kmeans/gmm dispatch (each routes to the right delegate + typed result),
that the resolved seed genuinely **reaches the fit** (not just the manifest), and the
consumer guarantees (``require_clean``, certified-set restriction).

The unit tests seed a cleaned version directly via ``FakeReader.add_cleaned_version`` (the
reader/store fakes are disjoint), so they do not depend on a live ``qc_clean`` run.
"""

from __future__ import annotations

import asyncio
import io
import json
import math
from pathlib import Path

import pandas as pd
import pytest

from bloom_mcp.contract import BloomMCPError
from bloom_mcp.data_access import FakeReader, SupabaseReader
from bloom_mcp.result_store import FakeResultStore, SupabaseResultStore
from bloom_mcp.tools import _ports
from bloom_mcp.tools import clustering_tool
from bloom_mcp.tools.clustering_tool import (
    ClusteringParams,
    ClusteringResult,
    clustering,
)

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_FINAL = _FIXTURES / "turface_19_final_data.csv"
_GOLDEN = json.loads((_FIXTURES / "turface_19_clustering_golden.json").read_text())

_EXPERIMENT = "turface_19.csv"
_TRAITS = _GOLDEN[
    "trait_cols"
]  # the 8 recorded PCA golden traits (a subset of ~12 certified)
# Drift-gate tolerance. Safe because the fit is deterministic at a fixed seed/sklearn build
# (same-seed→identical labels is asserted separately); not a cross-platform reproducibility
# claim. Matches tests/test_oracle.py's _VAR_TOL.
_TOL = 1e-6
_SEED = 42


def _final_df() -> pd.DataFrame:
    return pd.read_csv(_FINAL)


@pytest.fixture
def injected_ports():
    """FakeReader serving the cleaned fixture as a cleaned version + FakeResultStore."""
    reader = FakeReader()
    store = FakeResultStore()
    reader.add_cleaned_version(_EXPERIMENT, "v1", _final_df(), make_latest=True)
    _ports.configure(reader=reader, store=store)
    try:
        yield reader, store
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())


def _run(**overrides) -> ClusteringResult:
    params = {"experiment": _EXPERIMENT, "trait_columns": _TRAITS, **overrides}
    return clustering(ClusteringParams(**params))


def _labels_of(store, monkeypatch, **overrides) -> list[int]:
    """Run once and return the serialized ``cluster_labels`` captured at commit time."""
    holder: dict[str, str] = {}
    real_commit = store.commit

    def _commit(run, outputs):
        holder["json"] = (run.staging_dir / "cluster_result.json").read_text()
        return real_commit(run, outputs)

    monkeypatch.setattr(store, "commit", _commit)
    _run(**overrides)
    return json.loads(holder["json"])["cluster_labels"]


# ── 2.2 determinism oracle (north star) ─────────────────────────────────────


@pytest.mark.parametrize(
    "overrides",
    [
        {"method": "kmeans", "n_clusters": 3},
        {"method": "gmm", "n_components": 3},
    ],
)
def test_same_seed_identical_labels(injected_ports, monkeypatch, overrides):
    """Same seed + inputs → element-wise identical cluster_labels (NOT a tolerance compare)."""
    _reader, store = injected_ports
    first = _labels_of(store, monkeypatch, seed=_SEED, **overrides)
    second = _labels_of(store, monkeypatch, seed=_SEED, **overrides)
    assert first == second  # element-wise identical


# ── 2.3 characterization snapshot through the tool (drift gate) ─────────────


def test_kmeans_snapshot_through_the_tool(injected_ports):
    g = _GOLDEN["kmeans"]
    result = _run(method="kmeans", n_clusters=3, seed=_SEED)
    assert result.n_clusters == 3
    assert result.cluster_sizes == g["cluster_sizes"]
    assert result.silhouette_score == pytest.approx(g["silhouette_score"], abs=_TOL)
    assert result.davies_bouldin_score == pytest.approx(
        g["davies_bouldin_score"], abs=_TOL
    )
    assert result.calinski_harabasz_score == pytest.approx(
        g["calinski_harabasz_score"], abs=_TOL
    )
    assert result.inertia == pytest.approx(g["inertia"], abs=_TOL)


def test_gmm_snapshot_through_the_tool(injected_ports):
    g = _GOLDEN["gmm"]
    result = _run(method="gmm", n_components=3, covariance_type="full", seed=_SEED)
    assert result.n_clusters == 3
    assert result.converged is True
    assert result.cluster_sizes == g["cluster_sizes"]
    assert result.silhouette_score == pytest.approx(g["silhouette_score"], abs=_TOL)
    assert result.davies_bouldin_score == pytest.approx(
        g["davies_bouldin_score"], abs=_TOL
    )
    assert result.calinski_harabasz_score == pytest.approx(
        g["calinski_harabasz_score"], abs=_TOL
    )
    # Fixed-n path: bic/aic are upstream-correct (not the auto-select bug) — pin them.
    assert result.bic == pytest.approx(g["bic"], abs=_TOL)
    assert result.aic == pytest.approx(g["aic"], abs=_TOL)


# ── 2.4 / 2.5 no silent sample loss + full certified default ────────────────


def test_no_silent_sample_loss(injected_ports):
    result = _run(method="kmeans", n_clusters=3)
    assert result.n_samples == len(_final_df()) == 153
    assert result.n_features == len(_TRAITS) == 8


def test_omitting_trait_columns_uses_full_certified_set(injected_ports):
    """Omit trait_columns → cluster over ALL certified traits (~12), not the golden 8."""
    reader, _store = injected_ports
    frame = reader.load_experiment(_EXPERIMENT, require_clean=True)
    result = clustering(
        ClusteringParams(experiment=_EXPERIMENT, method="kmeans", n_clusters=3)
    )
    assert set(result.feature_names) == set(frame.trait_cols)
    assert result.n_features > len(_TRAITS)  # strictly more than the golden subset
    assert result.n_samples == 153


# ── 3.1 tools/list presence ─────────────────────────────────────────────────


def test_clustering_in_tools_list_and_workflow_preserved():
    from fastmcp import Client

    from bloom_mcp import server

    async def _list():
        async with Client(server.mcp) as client:
            return await client.list_tools()

    tools = {t.name: t for t in asyncio.run(_list())}
    assert "clustering" in tools
    assert tools["clustering"].inputSchema is not None
    assert "run_clustering_workflow" in tools  # additive — legacy not removed


# ── 3.2 polymorphic delegation pinning + seed-reaches-fit ───────────────────


def test_kmeans_delegates_to_kmeans_and_seed_reaches_fit(injected_ports, monkeypatch):
    captured: dict[str, object] = {}
    real = clustering_tool.perform_kmeans_clustering

    def _spy(data, **kwargs):
        captured["n"] = captured.get("n", 0) + 1
        captured["kwargs"] = kwargs
        captured["columns"] = list(data.columns)
        return real(data, **kwargs)

    monkeypatch.setattr(clustering_tool, "perform_kmeans_clustering", _spy)

    def _boom_gmm(*a, **k):  # pragma: no cover - must not run
        raise AssertionError("kmeans call routed to the GMM delegate")

    monkeypatch.setattr(clustering_tool, "perform_gmm_clustering", _boom_gmm)

    import bloom_mcp.clustering as vendored

    monkeypatch.setattr(
        vendored,
        "perform_kmeans_clustering",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("clustering called the vendored bloom_mcp.clustering")
        ),
    )

    _run(method="kmeans", n_clusters=3, seed=_SEED)
    assert captured["n"] == 1
    assert captured["columns"] == _TRAITS
    # The resolved seed reaches the fit — not merely the manifest.
    assert captured["kwargs"]["random_state"] == _SEED
    assert captured["kwargs"]["n_clusters"] == 3


def test_gmm_delegates_to_gmm_and_seed_reaches_fit(injected_ports, monkeypatch):
    captured: dict[str, object] = {}
    real = clustering_tool.perform_gmm_clustering

    def _spy(data, **kwargs):
        captured["n"] = captured.get("n", 0) + 1
        captured["kwargs"] = kwargs
        return real(data, **kwargs)

    monkeypatch.setattr(clustering_tool, "perform_gmm_clustering", _spy)

    def _boom_kmeans(*a, **k):  # pragma: no cover - must not run
        raise AssertionError("gmm call routed to the k-means delegate")

    monkeypatch.setattr(clustering_tool, "perform_kmeans_clustering", _boom_kmeans)

    _run(method="gmm", n_components=3, seed=_SEED)
    assert captured["n"] == 1
    assert captured["kwargs"]["random_state"] == _SEED
    assert captured["kwargs"]["n_components"] == 3


# ── 3.3 provenance records the resolved seed (new-for-this-tier) ────────────


def test_provenance_records_resolved_seed(injected_ports):
    _reader, store = injected_ports
    _run(method="kmeans", n_clusters=3, seed=_SEED)
    stored = store.get_run(_EXPERIMENT, "clustering", "latest")
    assert stored.tool == "clustering"
    assert stored.seed == _SEED  # the resolved seed that produced the labels (not None)


# ── 3.4 schema round-trip (both methods) ────────────────────────────────────


@pytest.mark.parametrize(
    "overrides",
    [{"method": "kmeans", "n_clusters": 3}, {"method": "gmm", "n_components": 3}],
)
def test_input_output_round_trip(injected_ports, overrides):
    result = _run(**overrides)
    again = ClusteringResult.model_validate(json.loads(result.model_dump_json()))
    assert again.cluster_sizes == result.cluster_sizes
    assert again.method == result.method


# ── 3.5 method-specific scalars are mutually exclusive ──────────────────────


def test_method_specific_scalars_are_mutually_exclusive(injected_ports):
    km = _run(method="kmeans", n_clusters=3)
    assert km.inertia is not None
    assert km.bic is None and km.aic is None
    assert km.converged is None and km.covariance_type is None

    gm = _run(method="gmm", n_components=3)
    assert gm.bic is not None and gm.aic is not None
    assert gm.converged is not None and gm.covariance_type == "full"
    assert gm.inertia is None


# ── 3.6 cluster-count override + auto-select (incl GMM collapse) ────────────


def test_kmeans_cluster_count_override_and_autoselect(injected_ports):
    assert _run(method="kmeans", n_clusters=4).n_clusters == 4
    auto = _run(method="kmeans", n_clusters=None).n_clusters
    assert 2 <= auto <= 10


def test_gmm_autoselect_may_collapse_to_one_component(injected_ports):
    """gmm with n_components omitted lets BIC pick — on this data it collapses to 1,
    surfaced honestly (n_clusters==1, silhouette 0.0) rather than raising or hiding it.
    All three internal-validation metrics must be finite (not NaN/±inf) even for n=1.
    An advisory warning is added to the result so the scientist is informed.
    """
    result = _run(method="gmm", n_components=None)
    assert result.n_clusters == 1
    assert result.silhouette_score == pytest.approx(0.0, abs=_TOL)
    assert math.isfinite(result.davies_bouldin_score)
    assert math.isfinite(result.calinski_harabasz_score)
    assert len(result.warnings) == 1
    assert "single component" in result.warnings[0].lower()


def test_gmm_autoselect_bic_aic_reflect_the_selected_model(injected_ports, monkeypatch):
    """Upstream sleap-roots-analyze 0.1.0a4 returns the LAST-candidate BIC/AIC on the GMM
    auto-select path, not the selected model's. The tool corrects both to the selected
    model's scores (bic_scores[n-1]) — otherwise the reported BIC/AIC would be inconsistent
    with the reported cluster assignments."""
    captured: dict = {}
    real = clustering_tool.perform_gmm_clustering

    def _spy(data, **kwargs):
        d = real(data, **kwargs)
        captured["dict"] = d
        return d

    monkeypatch.setattr(clustering_tool, "perform_gmm_clustering", _spy)
    result = _run(method="gmm", n_components=None)

    d = captured["dict"]
    idx = result.n_clusters - 1
    # On this dataset auto-select collapses to n=1 out of the default max_components=5
    # candidates — making the negative assertion unconditional (selected ≠ last candidate).
    assert (
        result.n_clusters == 1
    ), f"expected auto-collapse to n=1, got {result.n_clusters}"
    assert len(d["bic_scores"]) == 5  # default max_components=5
    # Corrected values == the selected candidate's per-candidate scores.
    assert result.bic == pytest.approx(d["bic_scores"][idx], abs=_TOL)
    assert result.aic == pytest.approx(d["aic_scores"][idx], abs=_TOL)
    # The corrected values are NOT the buggy last-candidate scalar (d["bic"] == bic_scores[-1]).
    assert result.bic != pytest.approx(d["bic"], abs=_TOL)
    assert result.aic != pytest.approx(d["aic"], abs=_TOL)


# ── 3.7 out-of-range + wrong-method controls ────────────────────────────────


def test_n_clusters_below_two_is_invalid_input(injected_ports):
    with pytest.raises(BloomMCPError) as exc:
        clustering({"experiment": _EXPERIMENT, "method": "kmeans", "n_clusters": 1})
    assert exc.value.code == "invalid_input"


def test_n_components_below_one_is_invalid_input(injected_ports):
    with pytest.raises(BloomMCPError) as exc:
        clustering({"experiment": _EXPERIMENT, "method": "gmm", "n_components": 0})
    assert exc.value.code == "invalid_input"


def test_gmm_control_on_kmeans_is_rejected(injected_ports):
    # Every gmm-only control — including the max_components bound — is rejected on a kmeans
    # call rather than silently ignored (and left mis-recorded in provenance.params).
    for bad in (
        {"n_components": 3},
        {"max_components": 8},
        {"covariance_type": "full"},
    ):
        with pytest.raises(BloomMCPError) as exc:
            _run(method="kmeans", **bad)
        assert exc.value.code == "invalid_input"
        assert list(bad)[0] in exc.value.message


def test_kmeans_control_on_gmm_is_rejected(injected_ports):
    # Every kmeans-only control — including the max_clusters bound — is rejected on a gmm call.
    for bad in ({"n_clusters": 3}, {"max_clusters": 8}):
        with pytest.raises(BloomMCPError) as exc:
            _run(method="gmm", **bad)
        assert exc.value.code == "invalid_input"
        assert list(bad)[0] in exc.value.message


def test_max_bounds_forwarded_to_the_right_delegate(injected_ports, monkeypatch):
    """max_clusters / max_components take effect when set on their own method, and resolve
    to the delegate defaults (10 / 5) when omitted — never silently dropped."""
    km_kwargs: dict = {}
    real_km = clustering_tool.perform_kmeans_clustering

    def _spy_km(data, **kwargs):
        km_kwargs.update(kwargs)
        return real_km(data, **kwargs)

    monkeypatch.setattr(clustering_tool, "perform_kmeans_clustering", _spy_km)
    _run(method="kmeans", n_clusters=None, max_clusters=4)
    assert km_kwargs["max_clusters"] == 4
    _run(method="kmeans", n_clusters=None)  # omitted → default
    assert km_kwargs["max_clusters"] == 10

    gm_kwargs: dict = {}
    real_gm = clustering_tool.perform_gmm_clustering

    def _spy_gm(data, **kwargs):
        gm_kwargs.update(kwargs)
        return real_gm(data, **kwargs)

    monkeypatch.setattr(clustering_tool, "perform_gmm_clustering", _spy_gm)
    _run(method="gmm", n_components=None, max_components=4)
    assert gm_kwargs["max_components"] == 4
    _run(method="gmm", n_components=None)  # omitted → default
    assert gm_kwargs["max_components"] == 5


# ── 3.8 trait-column validation (via _qc_shared require_certified=True) ─────


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

    monkeypatch.setattr(clustering_tool, "perform_kmeans_clustering", _spy)
    with pytest.raises(BloomMCPError) as exc:
        _run(trait_columns=["Replicate"])
    assert exc.value.code == "invalid_input"
    assert "Replicate" in exc.value.message


def test_empty_trait_columns_is_invalid_input(injected_ports, monkeypatch):
    _reader, store = injected_ports

    def _spy(*a, **k):  # pragma: no cover - must not run
        raise AssertionError("delegate called on an empty selection")

    monkeypatch.setattr(clustering_tool, "perform_kmeans_clustering", _spy)
    with pytest.raises(BloomMCPError) as exc:
        _run(trait_columns=[])
    assert exc.value.code == "invalid_input"
    assert store.list_runs(_EXPERIMENT, "clustering") == []


def test_duplicate_trait_columns_is_invalid_input_naming_them(injected_ports):
    with pytest.raises(BloomMCPError) as exc:
        _run(trait_columns=[_TRAITS[0], _TRAITS[0]])
    assert exc.value.code == "invalid_input"
    assert _TRAITS[0] in exc.value.message


# ── 3.9 degenerate fit → structured, not internal; no leak; no run ──────────


def test_degenerate_selection_is_assumption_violated(injected_ports):
    reader, store = injected_ports
    const = pd.DataFrame({"tA": [5.0] * 6, "tB": [5.0] * 6})
    reader.add_cleaned_version("const.csv", "v1", const, make_latest=True)
    with pytest.raises(BloomMCPError) as exc:
        clustering(
            ClusteringParams(experiment="const.csv", method="kmeans", n_clusters=2)
        )
    assert exc.value.code == "assumption_violated"
    msg = f"{exc.value.message} {exc.value.remedy}"
    assert "degenerate" in msg.lower()
    assert store.list_runs("const.csv", "clustering") == []


def test_degenerate_fit_does_not_leak_backend_internals(injected_ports, monkeypatch):
    _reader, store = injected_ports

    def _boom(*a, **k):
        raise RuntimeError("secret path /var/secrets/key and host db.internal")

    monkeypatch.setattr(clustering_tool, "perform_kmeans_clustering", _boom)
    with pytest.raises(BloomMCPError) as exc:
        _run(method="kmeans", n_clusters=3)
    assert exc.value.code == "assumption_violated"
    msg = f"{exc.value.message} {exc.value.remedy}"
    assert "/var" not in msg and "db.internal" not in msg
    assert store.list_runs(_EXPERIMENT, "clustering") == []


# ── 3.10 non-finite guard ───────────────────────────────────────────────────


def test_non_finite_certified_trait_is_rejected(injected_ports, monkeypatch):
    reader, store = injected_ports
    inf_df = pd.DataFrame(
        {"tA": [1.0, 2.0, 3.0, 4.0], "tB": [4.0, float("inf"), 2.0, 1.0]}
    )
    reader.add_cleaned_version("inf.csv", "v1", inf_df, make_latest=True)

    def _spy(*a, **k):  # pragma: no cover - must not run
        raise AssertionError("delegate called with a non-finite value")

    monkeypatch.setattr(clustering_tool, "perform_kmeans_clustering", _spy)
    with pytest.raises(BloomMCPError) as exc:
        clustering(
            ClusteringParams(experiment="inf.csv", method="kmeans", n_clusters=2)
        )
    assert exc.value.code == "assumption_violated"
    assert store.list_runs("inf.csv", "clustering") == []


def test_reordered_rows_are_assumption_violated(injected_ports, monkeypatch):
    """A delegate that reordered rows (without dropping any) would mis-map labels to plants;
    the data_indices identity guard catches it before persisting, not just a length mismatch.
    """
    _reader, store = injected_ports
    real = clustering_tool.perform_kmeans_clustering

    def _reorder(data, **kwargs):
        d = real(data, **kwargs)
        d["data_indices"] = list(
            reversed(list(d["data_indices"]))
        )  # reordered, same count
        return d

    monkeypatch.setattr(clustering_tool, "perform_kmeans_clustering", _reorder)
    with pytest.raises(BloomMCPError) as exc:
        _run(method="kmeans", n_clusters=3)
    assert exc.value.code == "assumption_violated"
    assert "reordered" in exc.value.message.lower()
    assert store.list_runs(_EXPERIMENT, "clustering") == []


def test_fallback_length_check_catches_wrong_count(injected_ports, monkeypatch):
    """When data_indices is absent the fallback floor still catches a label-count mismatch."""
    _reader, store = injected_ports
    real = clustering_tool.perform_kmeans_clustering

    def _no_index_extra_label(data, **kwargs):
        d = real(data, **kwargs)
        d.pop("data_indices", None)  # force the len-fallback path
        d["cluster_labels"] = list(d["cluster_labels"]) + [0]  # 154, not 153
        return d

    monkeypatch.setattr(
        clustering_tool, "perform_kmeans_clustering", _no_index_extra_label
    )
    with pytest.raises(BloomMCPError) as exc:
        _run(method="kmeans", n_clusters=3)
    assert exc.value.code == "assumption_violated"
    assert store.list_runs(_EXPERIMENT, "clustering") == []


def test_warnings_empty_on_normal_fixed_n_run(injected_ports):
    """Normal fixed-n runs (both methods) carry no advisory warnings."""
    km = _run(method="kmeans", n_clusters=3)
    assert km.warnings == []
    gm = _run(method="gmm", n_components=3)
    assert gm.warnings == []


# ── 3.11 require_clean consumption (property / invariant) ───────────────────


def test_raw_only_experiment_is_rejected_with_qc_clean_remedy():
    reader = FakeReader()
    store = FakeResultStore()
    reader.add_experiment("rawonly.csv", _final_df())
    _ports.configure(reader=reader, store=store)
    try:
        with pytest.raises(BloomMCPError) as exc:
            clustering(
                ClusteringParams(
                    experiment="rawonly.csv", method="kmeans", n_clusters=3
                )
            )
        assert "qc_clean" in exc.value.remedy.lower()
        assert store.list_runs("rawonly.csv", "clustering") == []
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())


def test_consumes_cleaned_version_source(injected_ports):
    result = _run(method="kmeans", n_clusters=3)
    assert result.source == "v1_cleaned"
    assert result.source != "raw"


# ── 5.2 persistence: links (not the vector) + identity + version increment ──


def test_persists_labels_and_returns_links_not_the_vector(injected_ports):
    _reader, store = injected_ports
    result = _run(method="kmeans", n_clusters=3)

    stored = store.get_run(_EXPERIMENT, "clustering", "latest")
    assert set(stored.output_keys) == {"labels.csv", "cluster_result.json"}
    assert set(result.outputs) == {"labels.csv", "cluster_result.json"}
    assert result.run_ref == stored.run_ref
    # No field carries the N-length label vector inline (cluster_sizes/feature_names are short).
    dumped = result.model_dump()
    assert not any(isinstance(v, list) and len(v) > 50 for v in dumped.values())


def test_labels_csv_carries_sample_identity(injected_ports, monkeypatch):
    _reader, store = injected_ports
    captured: dict[str, str] = {}
    real_commit = store.commit

    def _commit(run, outputs):
        for name in outputs:
            captured[name] = (run.staging_dir / name).read_text()
        return real_commit(run, outputs)

    monkeypatch.setattr(store, "commit", _commit)
    _run(method="kmeans", n_clusters=3)

    labels = pd.read_csv(io.StringIO(captured["labels.csv"]))
    assert list(labels.columns[:3]) == ["Barcode", "Genotype", "Replicate"]
    assert labels.columns[3] == "cluster"
    final = _final_df()
    assert labels["Barcode"].tolist() == final["Barcode"].tolist()
    assert len(labels) == len(final) == 153


def test_second_run_increments_version(injected_ports):
    _reader, store = injected_ports
    _run(method="kmeans", n_clusters=3)
    _run(method="kmeans", n_clusters=3)
    assert [r.run_ref for r in store.list_runs(_EXPERIMENT, "clustering")] == [
        "v1",
        "v2",
    ]
    assert store.get_run(_EXPERIMENT, "clustering", "latest").run_ref == "v2"


def test_passes_source_csv_for_input_lineage(injected_ports, monkeypatch):
    _reader, store = injected_ports
    captured: dict[str, object] = {}
    real_create = store.create_run

    def _spy(**kwargs):
        src = kwargs.get("source_csv")
        captured["exists"] = src is not None and Path(src).exists()
        return real_create(**kwargs)

    monkeypatch.setattr(store, "create_run", _spy)
    _run(method="kmeans", n_clusters=3)
    assert captured["exists"]
