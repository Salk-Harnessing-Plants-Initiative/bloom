"""run_clustering_workflow — k-means / GMM / hierarchical clustering in one MCP call.

Clubs together the 4 clustering primitives that used to be separate MCP tools
(run_kmeans_clustering, run_gmm_clustering, run_hierarchical_clustering,
get_cluster_quality) into one workflow with an `algorithm` parameter.

Wraps `source.clustering` (a faithful port of Elizabeth's
`sleap_roots_analyze/clustering.py` — `perform_kmeans_clustering`,
`perform_gmm_clustering`, `perform_hierarchical_clustering`, `cut_dendrogram`,
`calculate_cluster_quality_metrics`). All 7 source functions match her repo
1-for-1.

Each call writes one versioned `clustering_<stem>/v<N>_<date>/` via
AnalysisWriter (one `cluster_labels.csv` + optional `cluster_centers.csv`)
plus a 2D PCA-projected scatter colored by cluster, served from
BLOOM_PLOTS_URL.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Optional

from bloom_mcp.clustering import (
    calculate_cluster_quality_metrics,
    cut_dendrogram,
    perform_gmm_clustering,
    perform_hierarchical_clustering,
    perform_kmeans_clustering,
)
from bloom_mcp.experiment_utils import (
    PLOTS_DIR,
    PLOTS_URL,
    TRAITS_DIR,
    load_experiment_data as _load_data,
)

from ._helpers import build_writer

_TOOL_NAME = "run_clustering_workflow"
_TOOL_CLASS = "clustering"
VALID_ALGORITHMS = ("kmeans", "gmm", "hierarchical")


def _plot_path_and_url(stem: str, version_id: str, algorithm: str) -> tuple[Path, str]:
    filename = f"clustering_{stem}_{version_id}_{algorithm}_{uuid.uuid4().hex[:8]}.png"
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    base_url = (PLOTS_URL or "").rstrip("/")
    return PLOTS_DIR / filename, f"{base_url}/{filename}"


def _render_cluster_scatter(
    data, labels, stem: str, algorithm: str, k: int, out_path: Path
) -> None:
    """Render a 2D scatter of samples colored by cluster. PCA-projects if traits > 2."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    arr = np.asarray(data)
    if arr.ndim == 2 and arr.shape[1] > 2:
        from sklearn.decomposition import PCA

        proj = PCA(n_components=2).fit_transform(arr)
        x_label, y_label = "PC1", "PC2"
    else:
        proj = arr
        x_label, y_label = "x", "y"

    fig, ax = plt.subplots(figsize=(8, 8))
    scatter = ax.scatter(
        proj[:, 0], proj[:, 1], c=labels, cmap="tab10", s=20, alpha=0.8
    )
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    ax.set_title(f"{algorithm.upper()} clusters (k={k}): {stem}")
    plt.colorbar(scatter, ax=ax, label="cluster")
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def run_clustering_workflow(
    filename: str,
    algorithm: str = "kmeans",
    k: Optional[int] = None,
    max_k: int = 10,
    linkage_method: str = "ward",
    user_label: Optional[str] = None,
) -> dict:
    """Cluster samples and write a versioned label CSV + scatter plot.

    Args:
        filename: CSV filename from `list_available_experiments`.
        algorithm: `kmeans` (default), `gmm`, or `hierarchical`.
        k: Number of clusters. For kmeans/gmm, `None` auto-selects via silhouette.
            For hierarchical, this is the cut level (defaults to 3 when `None`).
        max_k: Auto-selection ceiling for kmeans/gmm (default 10).
        linkage_method: Linkage strategy for hierarchical only (default `ward`).
        user_label: Optional slug appended to the version directory name.

    Returns:
        WorkflowResponse dict — `version_id`, `version_dir`, `manifest_path`,
        `summary` (algorithm, k_used, cluster_sizes, silhouette, etc.),
        `outputs` (cluster_labels.csv + optional cluster_centers.csv),
        `plot_url`, `plot_layout`. On error returns `{"error": <message>}`.
    """
    if algorithm not in VALID_ALGORITHMS:
        return {
            "error": f"Unknown algorithm '{algorithm}'. Valid: {', '.join(VALID_ALGORITHMS)}"
        }

    if k is not None and k < 2:
        return {"error": "k must be >= 2"}

    df, trait_cols, config, source_label = _load_data(filename)
    if df is None:
        return {"error": source_label}

    stem = Path(filename).stem
    data = df[trait_cols]

    try:
        if algorithm == "kmeans":
            result = perform_kmeans_clustering(
                data=data,
                n_clusters=k,
                max_clusters=max_k,
                standardize=True,
            )
            labels = result["cluster_labels"]
            k_used = result["n_clusters"]
            centers = result.get("cluster_centers")

        elif algorithm == "gmm":
            result = perform_gmm_clustering(
                data=data,
                n_components=k,
                max_components=max_k,
                standardize=True,
            )
            labels = result["cluster_labels"]
            k_used = (
                result["n_clusters"]
                if "n_clusters" in result
                else result.get("n_components")
            )
            centers = result.get("cluster_centers")

        else:  # hierarchical
            tree_result = perform_hierarchical_clustering(
                data=data,
                method=linkage_method,
                standardize=True,
            )
            k_cut = k if k is not None else 3
            cut_result = cut_dendrogram(tree_result, n_clusters=k_cut)
            labels = cut_result["cluster_labels"]
            k_used = k_cut
            centers = None
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{algorithm} clustering failed: {exc}"}

    import numpy as np

    label_array = np.asarray(labels)
    quality = calculate_cluster_quality_metrics(np.asarray(data), label_array)

    src_csv = TRAITS_DIR / filename
    writer = build_writer(
        filename,
        _TOOL_CLASS,
        source_csv=src_csv if src_csv.exists() else None,
    )
    version_dir = writer.create_version(
        tool_name=_TOOL_NAME,
        params={
            "algorithm": algorithm,
            "k": k,
            "k_used": k_used,
            "max_k": max_k,
            "linkage_method": linkage_method if algorithm == "hierarchical" else None,
        },
        user_label=user_label,
    )
    version_id = writer.version_id

    import pandas as pd

    labels_df = pd.DataFrame(
        {"sample_index": range(len(label_array)), "cluster": label_array}
    )
    labels_df.to_csv(version_dir / "cluster_labels.csv", index=False)
    outputs: dict[str, str] = {"cluster_labels.csv": "cluster_labels.csv"}

    if centers is not None:
        centers_arr = np.asarray(centers)
        centers_df = pd.DataFrame(
            centers_arr,
            columns=[
                f"feature_{i}" if i >= len(trait_cols) else trait_cols[i]
                for i in range(centers_arr.shape[1])
            ],
        )
        centers_df.insert(0, "cluster", range(centers_arr.shape[0]))
        centers_df.to_csv(version_dir / "cluster_centers.csv", index=False)
        outputs["cluster_centers.csv"] = "cluster_centers.csv"

    plot_path, plot_url = _plot_path_and_url(stem, version_id, algorithm)
    try:
        _render_cluster_scatter(data, label_array, stem, algorithm, k_used, plot_path)
        plot_layout = "scatter_by_cluster"
    except Exception:  # noqa: BLE001
        plot_url = None
        plot_layout = None

    cluster_sizes = {
        int(c): int((label_array == c).sum()) for c in sorted(set(label_array.tolist()))
    }

    summary: dict = {
        "algorithm": algorithm,
        "source": source_label,
        "n_samples": int(label_array.shape[0]),
        "k_used": int(k_used),
        "auto_selected_k": k is None and algorithm in ("kmeans", "gmm"),
        "cluster_sizes": cluster_sizes,
    }
    summary.update({k_metric: float(v) for k_metric, v in quality.items()})

    entry = writer.commit(outputs)

    response: dict = {
        "version_id": entry.id,
        "version_dir": str(version_dir),
        "manifest_path": f"{writer.analysis_dir.path}manifest.json",
        "summary": summary,
        "outputs": outputs,
    }
    if plot_url:
        response["plot_url"] = plot_url
        response["plot_layout"] = plot_layout
    return response


def register(mcp):
    """Register run_clustering_workflow with the MCP server."""
    mcp.tool()(run_clustering_workflow)
