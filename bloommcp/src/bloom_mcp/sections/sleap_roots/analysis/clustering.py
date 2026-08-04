"""clustering — k-means / GMM / hierarchical on a cleaned experiment, delegating to sleap-roots-analyze.

The second granular **consumer** and the first **polymorphic** analysis tool (Tier 5 / #309,
fast-follow #422). It reads a *cleaned* experiment through the :class:`ExperimentReader` port
with ``require_clean=True`` and **dispatches on ``method``** to the matching tested upstream
entry point:

- ``"kmeans"`` → ``perform_kmeans_clustering`` → ``KMeansResult.from_kmeans_dict``
- ``"gmm"`` → ``perform_gmm_clustering`` → ``GMMResult.from_gmm_dict``
- ``"hierarchical"`` → ``hierarchical_cluster_labels`` → ``ClusterResult.from_hierarchical_dict``

The MCP owns no clustering math of its own and never touches the vendored ``bloom_mcp.clustering``.

**Polymorphic result, one envelope.** Each method returns a distinct typed result over the
shared ``ClusterResult`` base. Method-specific scalars: ``inertia`` for k-means; ``bic`` /
``aic`` / ``converged`` / ``covariance_type`` for GMM; ``linkage_method`` / ``distance_metric`` /
``cophenetic_correlation`` / ``cut_height`` for hierarchical.

**Stochastic (kmeans/gmm) vs deterministic (hierarchical).** k-means and GMM consume
``random_state`` — the contract resolves ``seed`` into it, forwards it to the delegate, and
records the resolved seed in provenance. Hierarchical clustering has no RNG; ``random_state``
is never forwarded and provenance records ``seed = None``, mirroring ``pca_analysis``.

**Consume, don't re-cluster raw.** The tool requires a cleaned version and restricts the
selection to the certified-clean trait set (``frame.trait_cols``) via
``_validate_trait_subset(..., require_certified=True)``, making the delegate's internal
``dropna()`` a no-op over the certified sample set. It persists a versioned run under tool
class ``clustering`` — per-sample labels **with sample identity** (``labels.csv``) and the
serialized typed result (``cluster_result.json``) — recording ``based_on_version`` = the
consumed cleaned version, and returns a cluster summary + links (never the label vector inline).

The legacy ``run_clustering_workflow`` tool and the vendored ``bloom_mcp.clustering``
module this tool once coexisted alongside were retired by ``devendor-bloommcp-analysis``.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field
from sleap_roots_analyze import (
    ClusterResult,
    GMMResult,
    KMeansResult,
    hierarchical_cluster_labels,
    perform_gmm_clustering,
    perform_kmeans_clustering,
)

from bloom_mcp.contract import BloomMCPError, Provenance, as_mcp_tool
from bloom_mcp.data_access import (
    CleanedVersionRequiredError,
    ExperimentFrame,
    ExperimentReadError,
)
from bloom_mcp.tools import _ports
from bloom_mcp.tools._qc_shared import _finite_or_none, _validate_trait_subset

_TOOL_CLASS = "clustering"
_LABELS_NAME = "labels.csv"
_RESULT_NAME = "cluster_result.json"
# Transient snapshot for input_sha256 only — intentionally not committed as an artifact.
_INPUT_SNAPSHOT_NAME = "input.csv"


class ClusteringParams(BaseModel):
    """Inputs for ``clustering``. Stochastic: the resolved ``seed`` drives the fit."""

    experiment: str = Field(
        ...,
        description="Experiment (CSV filename) to cluster. Must have a cleaned version "
        "produced by qc_clean; clustering consumes it (require_clean). Resolves the most "
        "recent outlier trim when one exists for the experiment, not merely the most "
        "recent clean.",
    )
    method: Literal["kmeans", "gmm", "hierarchical"] = Field(
        default="kmeans",
        description="Clustering algorithm. 'kmeans' (default) or 'hierarchical' use "
        "n_clusters/max_clusters; 'gmm' uses n_components/max_components/covariance_type. "
        "Set only the controls for the chosen method.",
    )
    trait_columns: list[str] | None = Field(
        default=None,
        description="Subset of cleaned trait columns to cluster on; omit to use all "
        "certified-clean traits. Each must be a cleaned trait column of the experiment. "
        "Pass at least one column with no duplicates (an empty list is rejected).",
    )
    standardize: bool = Field(
        default=True,
        description="Z-score each trait before clustering (matches the recorded snapshot).",
    )
    seed: int = Field(
        default=42,
        ge=0,
        description="Random seed for stochastic methods (kmeans/gmm). Ignored for "
        "hierarchical (deterministic); provenance records seed=None for hierarchical.",
    )
    n_clusters: int | None = Field(
        default=None,
        ge=2,
        description="kmeans/hierarchical: fixed number of clusters; omit to auto-select "
        "up to max_clusters. Do not set for gmm.",
    )
    max_clusters: int | None = Field(
        default=None,
        ge=2,
        description="kmeans/hierarchical: upper bound for automatic cluster-count selection "
        "(default 10). Do not set for gmm.",
    )
    n_components: int | None = Field(
        default=None,
        ge=1,
        description="gmm only: fixed number of mixture components; omit to auto-select up to "
        "max_components by BIC (may select 1 on weakly-clustered data). Do not set for kmeans.",
    )
    max_components: int | None = Field(
        default=None,
        ge=1,
        description="gmm only: upper bound for automatic component-count selection "
        "(default 5). Do not set for kmeans.",
    )
    covariance_type: Literal["full", "tied", "diag", "spherical"] | None = Field(
        default=None,
        description="gmm only: covariance form (default 'full'). Do not set for kmeans.",
    )
    linkage_method: Literal["ward", "complete", "average", "single"] | None = Field(
        default=None,
        description="hierarchical only: linkage criterion (default 'ward'). "
        "One of 'ward', 'complete', 'average', 'single'. Do not set for kmeans/gmm.",
    )
    distance_metric: str | None = Field(
        default=None,
        description="hierarchical only: distance metric passed to scipy pdist "
        "(default 'euclidean'). Note: 'ward' linkage only works with 'euclidean'. "
        "Do not set for kmeans/gmm.",
    )
    optimization_method: Literal["silhouette", "calinski", "davies_bouldin"] | None = (
        Field(
            default=None,
            description="hierarchical only: metric used for automatic cluster-count selection "
            "when n_clusters is omitted. One of 'silhouette' (default), 'calinski', or "
            "'davies_bouldin'. Do not set for kmeans/gmm.",
        )
    )
    user_label: str | None = Field(
        default=None,
        description="Optional slug appended to the version directory name.",
    )


class ClusteringResult(BaseModel):
    """A cluster summary + links to the persisted run (no label vector inline).

    The common ``ClusterResult`` fields are always present; the method-specific scalars are
    mutually exclusive — ``inertia`` for k-means, ``bic`` / ``aic`` / ``converged`` /
    ``covariance_type`` for GMM.
    """

    experiment: str
    source: str
    method: str
    n_samples: int
    n_features: int
    n_clusters: int
    cluster_sizes: list[int]
    silhouette_score: float
    davies_bouldin_score: float
    calinski_harabasz_score: float
    feature_names: list[str]
    # method-specific (mutually exclusive by method)
    inertia: float | None = None  # kmeans only
    bic: float | None = None  # gmm only
    aic: float | None = None  # gmm only
    converged: bool | None = None  # gmm only
    covariance_type: str | None = None  # gmm only
    linkage_method: str | None = None  # hierarchical only
    distance_metric: str | None = None  # hierarchical only
    cophenetic_correlation: float | None = None  # hierarchical only
    cut_height: float | None = None  # hierarchical only
    warnings: list[str] = Field(
        default_factory=list,
        description="Advisory messages (empty on a normal run). Non-empty when the tool surfaces "
        "a scientifically-notable outcome — e.g. GMM auto-selected a single component on data "
        "that may not have separable cluster structure.",
    )
    run_ref: str
    version_dir: str
    manifest_path: str
    outputs: dict[str, str]


def _reject_wrong_method_controls(params: ClusteringParams) -> None:
    """Reject a cluster-count control set for the other method (mirrors remove_outliers).

    Silently ignoring the other method's control (``n_components``/``max_components``/
    ``covariance_type`` on a k-means call, or ``n_clusters``/``max_clusters`` on GMM) would let
    a caller think a control took effect when it did not — and it would still land in
    ``provenance.params``, so the recorded run would claim a control that had no effect. Every
    per-method control (including the ``max_*`` bounds) is ``None``-defaulted and resolved
    internally, so an explicitly-set cross-method control is detectable here and surfaces as a
    fixable ``invalid_input`` naming the mismatch.
    """
    _hierarchical_only = (
        ("linkage_method", params.linkage_method),
        ("distance_metric", params.distance_metric),
        ("optimization_method", params.optimization_method),
    )
    _gmm_only = (
        ("n_components", params.n_components),
        ("max_components", params.max_components),
        ("covariance_type", params.covariance_type),
    )
    _kmeans_gmm_shared = (
        ("n_clusters", params.n_clusters),
        ("max_clusters", params.max_clusters),
    )
    if params.method == "kmeans":
        wrong = [n for n, v in (*_gmm_only, *_hierarchical_only) if v is not None]
    elif params.method == "gmm":
        wrong = [
            n for n, v in (*_kmeans_gmm_shared, *_hierarchical_only) if v is not None
        ]
    else:  # hierarchical
        wrong = [n for n, v in _gmm_only if v is not None]
    if wrong:
        raise BloomMCPError(
            code="invalid_input",
            message=(
                f"{', '.join(wrong)} {'is' if len(wrong) == 1 else 'are'} not a control for "
                f"method={params.method!r}."
            ),
            remedy=(
                "Use n_clusters/max_clusters for method='kmeans'/'hierarchical'; "
                "n_components/max_components/covariance_type for method='gmm'; "
                "linkage_method/distance_metric/optimization_method for method='hierarchical'."
            ),
        )


def _labels_frame(result, frame: ExperimentFrame) -> pd.DataFrame:
    """Per-sample cluster labels carrying sample identity for traceability.

    Prepends the frame's ``metadata_cols`` (e.g. Barcode/Genotype/Replicate) so a label row
    maps back to its plant by a shared key rather than fragile positional alignment — mirroring
    ``pca_analysis``'s ``scores.csv``. Sound *because* the finite-guard makes the delegate's
    internal ``dropna()`` a no-op, so ``result.cluster_labels`` is row-aligned with ``frame.df``.
    """
    labels = pd.DataFrame({"cluster": [int(c) for c in result.cluster_labels]})
    if not frame.metadata_cols:
        return labels
    identity = frame.df[frame.metadata_cols].reset_index(drop=True)
    return pd.concat([identity, labels], axis=1)


def _gmm_selected_scores(
    params: ClusteringParams, result, result_dict: dict
) -> tuple[float, float]:
    """Return the (bic, aic) of the *selected* GMM model — working around an upstream bug.

    **Upstream bug (sleap-roots-analyze 0.1.0a4):** on the GMM **auto-select** path
    (``n_components`` omitted), ``perform_gmm_clustering`` scores candidates 1..max by BIC, re-fits
    the winner, but returns the scalar ``bic``/``aic`` of the **last candidate tested** rather than
    the selected one (``bic_scores[-1]`` is never replaced after the re-fit). ``GMMResult`` is a
    blind pass-through, so ``result.bic``/``result.aic`` are then inconsistent with the reported
    cluster assignments (e.g. selected n=1 but reported n=5's BIC — even flipping sign). The
    per-candidate ``bic_scores``/``aic_scores`` arrays (candidate index i → ``n_components`` i+1)
    ARE correct, so recover the selected model's scores from them.

    The **fixed-n** path is already correct upstream (single-element score arrays), so only correct
    when we auto-selected AND the arrays line up; otherwise fall through to the pass-through value.
    Forward-compatible: once upstream fixes it, ``bic_scores[idx]`` equals the corrected scalar, so
    this is a no-op. **Re-verified on the 0.1.0a5 bump — bug still present** (clustering.py:439
    returns ``bic_scores[-1]``, not the selected model's score). Drop on the 0.1.0a6 bump.
    """
    bic, aic = float(result.bic), float(result.aic)
    if params.n_components is None:
        bic_scores = result_dict.get("bic_scores") or []
        aic_scores = result_dict.get("aic_scores") or []
        idx = int(result.n_clusters) - 1  # candidate i holds n_components = i + 1
        if 0 <= idx < len(bic_scores) and 0 <= idx < len(aic_scores):
            bic, aic = float(bic_scores[idx]), float(aic_scores[idx])
    return bic, aic


@as_mcp_tool(
    input_model=ClusteringParams,
    output_model=ClusteringResult,
    errors=(ExperimentReadError,),
)
def clustering(
    params: ClusteringParams, *, random_state: int, provenance: Provenance
) -> ClusteringResult:
    """Cluster a cleaned ``experiment`` via k-means / GMM and persist a versioned run."""
    reader = _ports.reader()
    store = _ports.store()

    # Validate method-control conflicts before any I/O — a cross-method control (e.g.
    # n_components on a kmeans call) should be rejected immediately, not after a full
    # Supabase read.
    _reject_wrong_method_controls(params)

    # Consumer: require a cleaned version. A missing one is a precondition failure with a
    # concrete remedy — caught here so it carries "run qc_clean first" rather than the
    # contract's generic tool_error message for the declared read error.
    try:
        frame = reader.load_experiment(params.experiment, require_clean=True)
    except CleanedVersionRequiredError:
        raise BloomMCPError(
            code="tool_error",
            message=(
                f"No cleaned version of {params.experiment!r} exists; clustering requires "
                f"a cleaned input."
            ),
            remedy=f"Run qc_clean on {params.experiment!r} first, then retry clustering.",
        ) from None

    if params.trait_columns is None:
        trait_cols = list(frame.trait_cols)
    else:
        _validate_trait_subset(
            frame, params.trait_columns, params.experiment, require_certified=True
        )
        trait_cols = list(params.trait_columns)
    selected = frame.df[trait_cols]

    # Defense-in-depth: the certified-clean set must be finite, so the delegate's internal
    # dropna() never silently loses a certified sample. isna() alone misses ±inf (which
    # dropna() also keeps), so check full finiteness. A mis-reporting reader is the only way
    # this fires.
    if not np.isfinite(selected.to_numpy(dtype=float)).all():
        raise BloomMCPError(
            code="assumption_violated",
            message=(
                "The cleaned experiment carries non-finite values (NaN or ±inf) in its "
                "certified trait columns."
            ),
            remedy="Re-run qc_clean to produce a finite-valued cleaned version, then retry.",
        )

    # Pre-dispatch parameter-compatibility guard for hierarchical: scipy raises a
    # ValueError("Ward's method only works with Euclidean distance") which would be caught
    # below and re-raised as the misleading "degenerate data" assumption_violated. Catch it
    # here so the remedy names the actual problem (parameter mismatch, not data quality).
    if params.method == "hierarchical":
        effective_linkage = params.linkage_method or "ward"
        effective_metric = params.distance_metric or "euclidean"
        if effective_linkage == "ward" and effective_metric != "euclidean":
            raise BloomMCPError(
                code="invalid_input",
                message=(
                    f"linkage_method='ward' requires distance_metric='euclidean'; "
                    f"got distance_metric={effective_metric!r}."
                ),
                remedy=(
                    "Either keep the default 'ward'/'euclidean' pair, or switch to "
                    "a non-ward linkage (e.g. 'complete', 'average', 'single') to "
                    "use a different distance metric."
                ),
            )

    # Delegate ALL clustering, dispatching on method. The delegates *raise* on degenerate
    # input: ValueError (fewer samples than requested clusters / too few for the method) and
    # RuntimeError ("clustering failed: No numeric columns with non-zero variance" when the
    # whole selection is constant). Map both to a self-correctable assumption_violated rather
    # than letting them fall through to the contract's opaque internal_error, and never echo
    # the raw exception text (it may carry backend internals — see the no-leak test).
    try:
        if params.method == "kmeans":
            result_dict = perform_kmeans_clustering(
                selected,
                n_clusters=params.n_clusters,
                max_clusters=params.max_clusters or 10,
                standardize=params.standardize,
                random_state=random_state,
            )
            result = KMeansResult.from_kmeans_dict(
                result_dict, random_state=random_state
            )
        elif params.method == "gmm":
            result_dict = perform_gmm_clustering(
                selected,
                n_components=params.n_components,
                max_components=params.max_components or 5,
                covariance_type=params.covariance_type or "full",
                standardize=params.standardize,
                random_state=random_state,
            )
            result = GMMResult.from_gmm_dict(result_dict, random_state=random_state)
        else:  # hierarchical
            result_dict = hierarchical_cluster_labels(
                selected,
                n_clusters=params.n_clusters,
                method=params.linkage_method or "ward",
                metric=params.distance_metric or "euclidean",
                standardize=params.standardize,
                optimization_method=params.optimization_method or "silhouette",
                max_clusters=params.max_clusters or 10,
            )
            result = ClusterResult.from_hierarchical_dict(result_dict)
    except (ValueError, RuntimeError):
        raise BloomMCPError(
            code="assumption_violated",
            message=(
                "Clustering could not fit the selected traits — the cleaned selection is "
                "degenerate (no trait with non-zero variance, fewer samples than the "
                "requested clusters, or too few samples for the method)."
            ),
            remedy=(
                "Select a broader set of varying numeric trait columns, or reduce "
                "n_clusters / n_components, then retry."
            ),
        ) from None

    # The finite-guard makes the delegate's dropna() a no-op, so labels are row-aligned with
    # the cleaned frame — but the labels.csv identity stitch is positional, so a delegate that
    # *reordered* (not just dropped) rows would mis-map labels to plants without shrinking the
    # count. The delegate reports data_indices = the surviving rows' ORIGINAL index labels
    # (df.index after its internal dropna()), NOT positional integers — so compare against
    # frame.df.index, which is exact whether or not the index is contiguous (a non-RangeIndex
    # frame from a future reader would false-reject against range(n)). Equal iff every row
    # survived in original order; a drop drops a label, a reorder permutes them. Fall back to
    # the length floor when the delegate omits data_indices.
    n_rows = len(frame.df)
    data_indices = result_dict.get("data_indices")
    if data_indices is not None:
        row_aligned = list(data_indices) == list(frame.df.index)
    else:
        row_aligned = len(result.cluster_labels) == n_rows
    if not row_aligned:
        raise BloomMCPError(
            code="assumption_violated",
            message=(
                "Clustering dropped or reordered samples from the certified-clean input; "
                "labels cannot be traced back to plants."
            ),
            remedy="Re-run qc_clean to produce a finite-valued cleaned version, then retry.",
        )

    if params.method == "kmeans":
        method_scalars: dict[str, object] = {"inertia": float(result.inertia)}
    elif params.method == "gmm":
        bic, aic = _gmm_selected_scores(params, result, result_dict)
        method_scalars = {
            "bic": bic,
            "aic": aic,
            "converged": bool(result.converged),
            "covariance_type": str(result.covariance_type),
        }
    else:  # hierarchical
        coph = float(result.cophenetic_correlation)
        cut = float(result.cut_height)
        method_scalars = {
            "linkage_method": str(result.linkage_method),
            "distance_metric": str(result.distance_metric),
            # NaN arises when all pairwise distances are 0 (all-identical data) giving a
            # 0/0 cophenet correlation or undefined cut height; convert to None so
            # to_json(allow_nan=False) doesn't raise after the run is already committed.
            "cophenetic_correlation": _finite_or_none(coph),
            "cut_height": _finite_or_none(cut),
        }

    tool_warnings: list[str] = []
    if (
        params.method == "gmm"
        and int(result.n_clusters) == 1
        and params.n_components is None
    ):
        tool_warnings.append(
            "GMM auto-selected a single component — the data may not have separable cluster "
            "structure. Consider inspecting trait distributions or increasing max_components "
            "before interpreting this result."
        )
    if params.method == "hierarchical" and params.seed != 42:
        tool_warnings.append(
            f"seed={params.seed} was provided but is ignored for hierarchical clustering "
            f"(deterministic); provenance records seed=None."
        )

    # Persist a versioned run. For hierarchical (deterministic, no RNG), override seed=None
    # in the stamped provenance — the contract resolved params.seed but we never consumed it.
    prov_update: dict[str, object] = {"based_on_version": frame.source}
    if params.method == "hierarchical":
        prov_update["seed"] = None
    prov = provenance.model_copy(update=prov_update)
    with tempfile.TemporaryDirectory(prefix="clustering_input_") as _tmp:
        source_snapshot = Path(_tmp) / _INPUT_SNAPSHOT_NAME
        frame.df.to_csv(source_snapshot, index=False)
        run = store.create_run(
            experiment=params.experiment,
            tool_class=_TOOL_CLASS,
            provenance=prov,
            user_label=params.user_label,
            source_csv=source_snapshot,
            source=frame.resolved_source,
        )
        _labels_frame(result, frame).to_csv(run.staging_dir / _LABELS_NAME, index=False)
        (run.staging_dir / _RESULT_NAME).write_text(result.to_json())
        stored = store.commit(
            run,
            {_LABELS_NAME: _LABELS_NAME, _RESULT_NAME: _RESULT_NAME},
        )

    return ClusteringResult(
        experiment=params.experiment,
        source=frame.source,
        method=params.method,
        n_samples=len(selected),
        n_features=len(result.feature_names),
        n_clusters=int(result.n_clusters),
        cluster_sizes=[int(s) for s in result.cluster_sizes],
        silhouette_score=float(result.silhouette_score),
        davies_bouldin_score=float(result.davies_bouldin_score),
        calinski_harabasz_score=float(result.calinski_harabasz_score),
        feature_names=list(result.feature_names),
        run_ref=stored.run_ref,
        version_dir=stored.version_dir,
        manifest_path=stored.manifest_path,
        outputs=dict(stored.output_keys),
        warnings=tool_warnings,
        **method_scalars,
    )
