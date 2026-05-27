"""run_dimensionality_reduction_workflow — PCA / UMAP dim reduction in one MCP call.

Clubs together the 4 PCA-related primitives that used to be separate MCP
tools (run_pca, get_pca_feature_contributions, plot_pca_scree, plot_pca_biplot)
into one workflow with a `method` parameter. Also brings UMAP into the MCP
surface for the first time — bloommcp had `source/umap_embedding.py` with a
complete `perform_umap_analysis` function but no MCP tool wired to it.

Maps to Elizabeth's `pca_analysis.py` + `umap_analysis.py` DAG steps from
sleap-roots-analyze. t-SNE is intentionally NOT included: it has no source
implementation in bloommcp or Elizabeth's repo.

Each call writes one versioned `dimred_<stem>/v<N>_<date>/` via AnalysisWriter
(5 CSVs for PCA, 1 CSV for UMAP) plus a chart PNG served from BLOOM_PLOTS_URL.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Optional

from source.experiment_utils import (
    PLOTS_DIR,
    PLOTS_URL,
    TRAITS_DIR,
    load_experiment_data as _load_data,
)
from source.pca import run_pca_and_export_artifacts
from source.umap_embedding import UMAP_AVAILABLE, perform_umap_analysis

from ._helpers import build_writer

_TOOL_NAME = "run_dimensionality_reduction_workflow"
_TOOL_CLASS = "dimred"
VALID_METHODS = ("pca", "umap")


def _plot_path_and_url(stem: str, version_id: str, method: str, suffix: str) -> tuple[Path, str]:
    """Build a unique plot path under PLOTS_DIR + the public URL for it."""
    filename = f"dimred_{stem}_{version_id}_{method}_{suffix}_{uuid.uuid4().hex[:8]}.png"
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    base_url = (PLOTS_URL or "").rstrip("/")
    return PLOTS_DIR / filename, f"{base_url}/{filename}"


def _render_pca_scree(evr: list[float], cvr: list[float], stem: str, threshold: float, out_path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax1 = plt.subplots(figsize=(10, 6))
    x = list(range(1, len(evr) + 1))
    ax1.bar(x, [v * 100 for v in evr], alpha=0.6, color="steelblue", label="Individual")
    ax1.set_xlabel("Principal Component")
    ax1.set_ylabel("Variance Explained (%)")
    ax1.set_title(f"PCA Scree: {stem}")

    ax2 = ax1.twinx()
    ax2.plot(x, [v * 100 for v in cvr], "ro-", markersize=4, label="Cumulative")
    ax2.set_ylabel("Cumulative Variance (%)")
    ax2.axhline(y=threshold * 100, color="gray", linestyle="--", alpha=0.5, label=f"{threshold*100:.0f}% threshold")

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="center right")
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def _render_umap_scatter(embedding, stem: str, out_path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(8, 8))
    ax.scatter(embedding[:, 0], embedding[:, 1], s=18, alpha=0.7, color="darkslateblue")
    ax.set_xlabel("UMAP 1")
    ax.set_ylabel("UMAP 2")
    ax.set_title(f"UMAP: {stem}")
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def run_dimensionality_reduction_workflow(
    filename: str,
    method: str = "pca",
    n_components: Optional[int] = None,
    variance_threshold: float = 0.95,
    n_neighbors: int = 15,
    min_dist: float = 0.1,
    user_label: Optional[str] = None,
) -> dict:
    """Run PCA or UMAP on a SLEAP experiment, save a versioned dir + chart.

    Args:
        filename: CSV filename from `list_available_experiments`.
        method: `pca` (default) or `umap`.
        n_components: PCA — explicit number of components. Default `None`
            means auto-select by `variance_threshold`. UMAP — fixed at 2.
        variance_threshold: PCA auto-selection threshold for cumulative
            variance (default 0.95).
        n_neighbors: UMAP — size of local neighborhood (default 15).
        min_dist: UMAP — minimum embedding distance (default 0.1).
        user_label: Optional slug appended to the version directory name.

    Returns:
        WorkflowResponse dict — `version_id`, `version_dir`, `manifest_path`,
        `summary` (method, n_components_used, variance metrics for PCA, n_samples for both),
        `outputs` (file paths relative to `version_dir`), `plot_url`, `plot_layout`.
        On load failure, invalid method, or missing UMAP dep, returns
        `{"error": <message>}` with no version created.
    """
    if method not in VALID_METHODS:
        return {"error": f"Unknown method '{method}'. Valid: {', '.join(VALID_METHODS)}"}

    if method == "umap" and not UMAP_AVAILABLE:
        return {
            "error": (
                "UMAP is not installed in the bloommcp container. "
                "Install the optional `umap-learn` Python package to enable this method."
            ),
        }

    df, trait_cols, config, source_label = _load_data(filename)
    if df is None:
        return {"error": source_label}

    stem = Path(filename).stem
    src_csv = TRAITS_DIR / filename
    writer = build_writer(
        filename,
        _TOOL_CLASS,
        source_csv=src_csv if src_csv.exists() else None,
    )
    version_dir = writer.create_version(
        tool_name=_TOOL_NAME,
        params={
            "method": method,
            "n_components": n_components,
            "variance_threshold": variance_threshold,
            "n_neighbors": n_neighbors,
            "min_dist": min_dist,
        },
        user_label=user_label,
    )
    version_id = version_dir.name.split("_", 1)[0]

    outputs: dict[str, str] = {}
    summary: dict = {"method": method, "source": source_label, "n_samples": len(df)}

    if method == "pca":
        try:
            pca_out = run_pca_and_export_artifacts(
                df, trait_cols,
                analysis_dir=version_dir,
                n_components=n_components,
                explained_variance_threshold=variance_threshold,
                save_csv=True,
                save_prefix="",
                include_feature_metrics=True,
            )
        except Exception as exc:  # noqa: BLE001
            return {"error": f"PCA failed: {exc}"}

        variance_df = pca_out["variance_df"]
        evr = variance_df["explained_variance_ratio"].tolist()
        cvr = variance_df["cumulative_variance_ratio"].tolist()

        summary.update({
            "n_components_used": len(evr),
            "variance_explained_per_pc": [round(v, 4) for v in evr[:10]],
            "cumulative_variance_explained": round(cvr[-1], 4) if cvr else None,
        })

        outputs.update({
            "pca_loadings.csv": f"{version_dir.name}/pca_loadings.csv",
            "pca_variance_explained.csv": f"{version_dir.name}/pca_variance_explained.csv",
            "pca_transformed_data.csv": f"{version_dir.name}/pca_transformed_data.csv",
            "trait_variance_contrib.csv": f"{version_dir.name}/trait_variance_contrib.csv",
            "feature_metrics.csv": f"{version_dir.name}/feature_metrics.csv",
        })

        plot_path, plot_url = _plot_path_and_url(stem, version_id, "pca", "scree")
        _render_pca_scree(evr, cvr, stem, variance_threshold, plot_path)
        plot_layout = "scree"

    else:  # method == "umap"
        try:
            umap_out = perform_umap_analysis(
                df=df,
                feature_cols=trait_cols,
                n_neighbors=n_neighbors,
                min_dist=min_dist,
                n_components=2,
            )
        except Exception as exc:  # noqa: BLE001
            return {"error": f"UMAP failed: {exc}"}

        embedding = umap_out["embedding"]
        summary.update({
            "n_components_used": 2,
            "n_neighbors": umap_out.get("n_neighbors", n_neighbors),
            "min_dist": umap_out.get("min_dist", min_dist),
        })

        import pandas as pd
        emb_df = pd.DataFrame(embedding, columns=["UMAP1", "UMAP2"])
        emb_csv = version_dir / "umap_embedding.csv"
        emb_df.to_csv(emb_csv, index=False)
        outputs["umap_embedding.csv"] = f"{version_dir.name}/umap_embedding.csv"

        plot_path, plot_url = _plot_path_and_url(stem, version_id, "umap", "scatter")
        _render_umap_scatter(embedding, stem, plot_path)
        plot_layout = "scatter"

    entry = writer.commit(outputs)

    return {
        "version_id": entry.id,
        "version_dir": str(version_dir),
        "manifest_path": str(writer.analysis_dir.path / "manifest.json"),
        "summary": summary,
        "outputs": outputs,
        "plot_url": plot_url,
        "plot_layout": plot_layout,
    }


def register(mcp):
    """Register run_dimensionality_reduction_workflow with the MCP server."""
    mcp.tool()(run_dimensionality_reduction_workflow)
