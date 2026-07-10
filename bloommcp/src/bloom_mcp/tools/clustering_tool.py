"""clustering — k-means / GMM on a cleaned experiment, delegating to sleap-roots-analyze.

The second granular **consumer** and the first **polymorphic** + first genuinely
**stochastic** analysis tool (Tier 5 / #309). It reads a *cleaned* experiment through the
:class:`ExperimentReader` port with ``require_clean=True`` and **dispatches on ``method``** to
the matching tested upstream entry point — ``perform_kmeans_clustering`` →
``KMeansResult.from_kmeans_dict`` or ``perform_gmm_clustering`` → ``GMMResult.from_gmm_dict``.
The MCP owns no clustering math — no standardization, distance computation, EM/Lloyd
iteration, or metric computation of its own — and never touches the vendored
``bloom_mcp.clustering``.

**Polymorphic result, one envelope.** k-means and GMM return two distinct typed results over
a shared ``ClusterResult`` base (labels, sizes, the three internal-validation scores). The
tool surfaces the common core inline plus the method-specific scalars — ``inertia`` for
k-means; ``bic`` / ``aic`` / ``converged`` / ``covariance_type`` for GMM — proving the contract
surface generalizes past PCA's single shape.

**Stochastic — the resolved seed is real here.** Unlike ``pca_analysis`` (deterministic,
``seed = None``), k-means and GMM consume ``random_state`` and their labels depend on it. So
the tool declares a ``random_state`` parameter and a ``seed`` input; the contract resolves the
requested seed into ``random_state``, forwards it to the delegate, and records **that resolved
seed** in provenance. The correctness oracle is *determinism*: same seed → identical labels.

**Consume, don't re-cluster raw.** The delegates standardize and fit over whatever numeric
columns they are handed, dropping NaN-bearing rows internally. So — like ``pca_analysis`` — the
tool requires a cleaned version and restricts the selection to the certified-clean trait set
(``frame.trait_cols``) via the shared ``_validate_trait_subset(..., require_certified=True)``,
and asserts the selection finite before fitting, making the delegate's internal ``dropna()`` a
no-op over the sample set ``qc_clean`` certified. It persists a versioned run under tool class
``clustering`` — the per-sample labels **with sample identity** (``labels.csv``) and the
serialized typed result (``cluster_result.json``) — recording ``based_on_version`` = the
consumed cleaned version and content-addressing the frame via ``source_csv``, and returns a
cluster summary + links (never the N-length label vector inline).

**Coexists with the legacy ``run_clustering_workflow``.** That older workflow tool + the
vendored ``bloom_mcp.clustering`` stay in place; this adds granularity alongside. Retirement of
``source/*`` is deferred to after Stage 1 (deleting ``clustering.py`` breaks server boot).

**Hierarchical is a deferred fast-follow.** ``perform_hierarchical_clustering`` returns only a
linkage matrix (no labels/scores, no ``from_hierarchical_dict``), so it cannot be thin-delegated
yet; it drops in as one ``method`` member once an upstream labeled entry point ships (#309).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field
from sleap_roots_analyze import (
    GMMResult,
    KMeansResult,
    perform_gmm_clustering,
    perform_kmeans_clustering,
)

from bloom_mcp.contract import BloomMCPError, Provenance, as_mcp_tool
from bloom_mcp.contract import register as _contract_register
from bloom_mcp.data_access import (
    CleanedVersionRequiredError,
    ExperimentFrame,
    ExperimentReadError,
)
from bloom_mcp.tools import _ports
from bloom_mcp.tools._qc_shared import _validate_trait_subset

_TOOL_CLASS = "clustering"
_LABELS_NAME = "labels.csv"
_RESULT_NAME = "cluster_result.json"
_INPUT_SNAPSHOT_NAME = "input.csv"


class ClusteringParams(BaseModel):
    """Inputs for ``clustering``. Stochastic: the resolved ``seed`` drives the fit."""

    experiment: str = Field(
        ...,
        description="Experiment (CSV filename) to cluster. Must have a cleaned version "
        "produced by qc_clean; clustering consumes it (require_clean).",
    )
    method: Literal["kmeans", "gmm"] = Field(
        default="kmeans",
        description="Clustering algorithm. 'kmeans' (default) partitions into n_clusters; "
        "'gmm' fits a Gaussian mixture of n_components. Set only that method's controls.",
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
        description="Random seed for the fit (recorded in provenance for reproducibility). "
        "Same seed + inputs → identical cluster labels.",
    )
    n_clusters: int | None = Field(
        default=None,
        ge=2,
        description="kmeans only: fixed number of clusters; omit to auto-select up to "
        "max_clusters by silhouette. Do not set for gmm.",
    )
    max_clusters: int = Field(
        default=10,
        ge=2,
        description="kmeans only: upper bound for automatic cluster-count selection.",
    )
    n_components: int | None = Field(
        default=None,
        ge=1,
        description="gmm only: fixed number of mixture components; omit to auto-select up to "
        "max_components by BIC (may select 1 on weakly-clustered data). Do not set for kmeans.",
    )
    max_components: int = Field(
        default=5,
        ge=1,
        description="gmm only: upper bound for automatic component-count selection.",
    )
    covariance_type: Literal["full", "tied", "diag", "spherical"] | None = Field(
        default=None,
        description="gmm only: covariance form (default 'full'). Do not set for kmeans.",
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
    inertia: float | None = None
    bic: float | None = None
    aic: float | None = None
    converged: bool | None = None
    covariance_type: str | None = None
    run_ref: str
    version_dir: str
    manifest_path: str
    outputs: dict[str, str]


def _reject_wrong_method_controls(params: ClusteringParams) -> None:
    """Reject a cluster-count control set for the other method (mirrors remove_outliers).

    Silently ignoring ``n_components`` on a k-means call (or ``n_clusters`` on GMM) would let
    a caller think a control took effect when it did not — a reproducibility hazard. Surface
    it as a fixable ``invalid_input`` naming the mismatch.
    """
    if params.method == "kmeans":
        wrong = [
            name
            for name, val in (
                ("n_components", params.n_components),
                ("covariance_type", params.covariance_type),
            )
            if val is not None
        ]
    else:  # gmm
        wrong = ["n_clusters"] if params.n_clusters is not None else []
    if wrong:
        raise BloomMCPError(
            code="invalid_input",
            message=(
                f"{wrong} {'is' if len(wrong) == 1 else 'are'} not a control for "
                f"method={params.method!r}."
            ),
            remedy=(
                "Use n_clusters/max_clusters for method='kmeans' and "
                "n_components/max_components/covariance_type for method='gmm'."
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

    _reject_wrong_method_controls(params)

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
                max_clusters=params.max_clusters,
                standardize=params.standardize,
                random_state=random_state,
            )
            result = KMeansResult.from_kmeans_dict(
                result_dict, random_state=random_state
            )
        else:
            result_dict = perform_gmm_clustering(
                selected,
                n_components=params.n_components,
                max_components=params.max_components,
                covariance_type=params.covariance_type or "full",
                standardize=params.standardize,
                random_state=random_state,
            )
            result = GMMResult.from_gmm_dict(result_dict, random_state=random_state)
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
    # the cleaned frame — assert it before stitching identity, so a future delegate that drops
    # rows surfaces here rather than mis-mapping labels to plants.
    if len(result.cluster_labels) != len(frame.df):
        raise BloomMCPError(
            code="assumption_violated",
            message=(
                "Clustering returned fewer labels than samples in the certified-clean input; "
                "labels cannot be traced back to plants."
            ),
            remedy="Re-run qc_clean to produce a finite-valued cleaned version, then retry.",
        )

    if params.method == "kmeans":
        method_scalars: dict[str, object] = {"inertia": float(result.inertia)}
    else:
        method_scalars = {
            "bic": float(result.bic),
            "aic": float(result.aic),
            "converged": bool(result.converged),
            "covariance_type": str(result.covariance_type),
        }

    # Persist a versioned run, recording the cleaned-source lineage on a *copy* of the stamped
    # provenance (model_copy — the non-proliferating pattern; not remove_outliers' in-place
    # mutation). Snapshot the consumed frame to a temp CSV passed as source_csv so the manifest
    # content-addresses the exact input (input_sha256), not just the mutable v<N>_cleaned label.
    prov = provenance.model_copy(update={"based_on_version": frame.source})
    with tempfile.TemporaryDirectory(prefix="clustering_input_") as _tmp:
        source_snapshot = Path(_tmp) / _INPUT_SNAPSHOT_NAME
        frame.df.to_csv(source_snapshot, index=False)
        run = store.create_run(
            experiment=params.experiment,
            tool_class=_TOOL_CLASS,
            provenance=prov,
            user_label=params.user_label,
            source_csv=source_snapshot,
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
        **method_scalars,
    )


def register(mcp):
    """Register clustering with the MCP server."""
    return _contract_register(mcp, clustering)
