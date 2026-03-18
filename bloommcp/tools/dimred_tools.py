"""
MCP Tool Wrappers for SLEAP Dimensionality Reduction (PCA).

Wraps functions from source/pca.py. Uses source/experiment_utils.py for dynamic
experiment discovery and column auto-detection.
"""

import numpy as np
import pandas as pd
from pathlib import Path

from source.pca import perform_pca_analysis, run_pca_and_export_artifacts, select_top_features_from_pca
from source.experiment_utils import load_experiment_data as _load_data, OUTPUT_DIR, PLOTS_DIR, PLOTS_URL


# ============================================================================
# Tool 1: Run PCA
# ============================================================================

def run_pca(
    filename: str,
    n_components: str = "",
    variance_threshold: float = 0.95,
) -> str:
    """Run PCA on a SLEAP experiment and export results.

    Performs PCA with automatic component selection based on cumulative
    variance threshold. Saves loadings, scores, variance explained, and
    trait contribution CSVs to the output directory.

    Args:
        filename: CSV filename from list_available_experiments
        n_components: Number of components (empty = auto-select by variance threshold)
        variance_threshold: Cumulative variance threshold for auto component selection (default 0.95)
    """
    df, trait_cols, config, source = _load_data(filename)
    if df is None:
        return config

    stem = Path(filename).stem

    n_comp = None
    if n_components.strip():
        try:
            n_comp = int(n_components.strip())
        except ValueError:
            return f"Invalid n_components: '{n_components}'. Must be an integer."

    analysis_dir = OUTPUT_DIR / f"pca_{stem}"

    # Build metadata_cols tuple (filter out None)
    meta_cols = tuple(
        c for c in [config["sample_id_col"], config["genotype_col"], config["replicate_col"]]
        if c is not None
    )

    try:
        results = run_pca_and_export_artifacts(
            df_traits=df,
            trait_cols=trait_cols,
            analysis_dir=analysis_dir,
            n_components=n_comp,
            explained_variance_threshold=variance_threshold,
            standardize=True,
            metadata_cols=meta_cols,
            save_csv=True,
            include_feature_metrics=True,
        )
    except Exception as e:
        return f"PCA failed: {e}"

    pca_results = results["pca_results"]
    n_selected = int(pca_results["n_components_selected"])
    evr = pca_results["explained_variance_ratio"]
    cvr = pca_results["cumulative_variance_ratio"]
    n_features = len(pca_results["feature_names"])
    n_samples = pca_results["transformed_data"].shape[0]

    lines = [
        f"PCA Results: {filename} (source: {source})",
        f"  {n_samples} samples, {n_features} traits",
        f"  Components selected: {n_selected} (threshold: {variance_threshold * 100:.0f}%)",
        f"  Total variance explained: {cvr[n_selected - 1] * 100:.1f}%\n",
        "  Per-component variance:",
    ]

    for i in range(min(n_selected, 10)):
        lines.append(
            f"    PC{i + 1}: {evr[i] * 100:.1f}% (cumulative: {cvr[i] * 100:.1f}%)"
        )

    if n_selected > 10:
        lines.append(f"    ... ({n_selected - 10} more components)")

    contrib_df = results["trait_contrib_df"]
    lines.append(f"\n  Top 5 traits by total variance contribution:")
    for _, row in contrib_df.head(5).iterrows():
        lines.append(
            f"    {row['trait']}: {row['trait_fractional_contrib'] * 100:.1f}%"
        )

    lines.append(f"\n  Saved to: {analysis_dir}/")
    lines.append(f"    - pca_loadings.csv")
    lines.append(f"    - pca_transformed_data.csv")
    lines.append(f"    - pca_variance_explained.csv")
    lines.append(f"    - trait_variance_contrib.csv")
    lines.append(f"    - feature_metrics.csv")

    return "\n".join(lines)


# ============================================================================
# Tool 2: PCA feature contributions
# ============================================================================

def get_pca_feature_contributions(
    filename: str,
    pc: str = "1",
    top_n: int = 10,
) -> str:
    """List traits ranked by their contribution to a specified principal component.

    Args:
        filename: CSV filename from list_available_experiments
        pc: Which PC to inspect (e.g., "1" for PC1, "2" for PC2)
        top_n: Number of top traits to show (default 10)
    """
    df, trait_cols, config, source = _load_data(filename)
    if df is None:
        return config

    try:
        pc_idx = int(pc.strip()) - 1
    except ValueError:
        return f"Invalid PC number: '{pc}'. Must be an integer (e.g., 1, 2, 3)."

    if pc_idx < 0:
        return "PC number must be >= 1."

    try:
        pca_results = perform_pca_analysis(data=df[trait_cols], standardize=True)
    except Exception as e:
        return f"PCA failed: {e}"

    n_selected = int(pca_results["n_components_selected"])
    if pc_idx >= n_selected:
        return f"PC{pc_idx + 1} not available. Only {n_selected} components selected."

    loadings = pca_results["loadings"]
    eigenvalue = pca_results["eigenvalues"][pc_idx]
    evr = pca_results["explained_variance_ratio"][pc_idx]
    feature_names = pca_results["feature_names"]

    pc_loadings = loadings[:, pc_idx]
    abs_loadings = np.abs(pc_loadings)
    sorted_idx = np.argsort(abs_loadings)[::-1]

    lines = [
        f"Feature Contributions to PC{pc_idx + 1}: {filename} (source: {source})",
        f"  PC{pc_idx + 1} explains {evr * 100:.1f}% of variance (eigenvalue: {eigenvalue:.2f})\n",
        f"  {'Rank':<6s} {'Trait':<40s} {'Loading':>8s} {'|Loading|':>10s} {'Contribution':>12s}",
        f"  {'---' * 27}",
    ]

    for rank, idx in enumerate(sorted_idx[:top_n]):
        name = feature_names[idx]
        loading = pc_loadings[idx]
        abs_load = abs_loadings[idx]
        contrib = eigenvalue * loading ** 2
        lines.append(
            f"  {rank + 1:<6d} {name:<40s} {loading:>8.4f} {abs_load:>10.4f} {contrib:>12.4f}"
        )

    pos_count = np.sum(pc_loadings[sorted_idx[:top_n]] > 0)
    neg_count = top_n - pos_count
    lines.append(f"\n  Direction: {pos_count} positive, {neg_count} negative loadings in top {top_n}")

    return "\n".join(lines)


# ============================================================================
# Tool 3: PCA scree plot
# ============================================================================

def plot_pca_scree(filename: str) -> str:
    """Generate a PCA scree plot showing variance explained per component.

    Args:
        filename: CSV filename from list_available_experiments
    """
    df, trait_cols, config, source = _load_data(filename)
    if df is None:
        return config

    stem = Path(filename).stem

    try:
        pca_results = perform_pca_analysis(data=df[trait_cols], standardize=True)
    except Exception as e:
        return f"PCA failed: {e}"

    evr = pca_results["explained_variance_ratio"]
    cvr = pca_results["cumulative_variance_ratio"]
    n_components = len(evr)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax1 = plt.subplots(figsize=(10, 6))
    x = range(1, n_components + 1)
    ax1.bar(x, evr * 100, alpha=0.6, color="steelblue", label="Individual")
    ax1.set_xlabel("Principal Component")
    ax1.set_ylabel("Variance Explained (%)")
    ax1.set_title(f"PCA Scree Plot: {stem}")

    ax2 = ax1.twinx()
    ax2.plot(x, cvr * 100, "ro-", markersize=4, label="Cumulative")
    ax2.set_ylabel("Cumulative Variance (%)")
    ax2.axhline(y=95, color="gray", linestyle="--", alpha=0.5, label="95% threshold")

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="center right")

    plot_name = f"pca_scree_{stem}.png"
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(PLOTS_DIR / plot_name, dpi=150, bbox_inches="tight")
    plt.close(fig)

    lines = [
        f"PCA Scree Plot: {filename} (source: {source})",
        f"  {n_components} components shown",
        f"  PC1: {evr[0] * 100:.1f}%, PC2: {evr[1] * 100:.1f}%" if n_components >= 2 else f"  PC1: {evr[0] * 100:.1f}%",
        f"  95% variance reached at PC{int(np.argmax(cvr >= 0.95)) + 1}" if np.any(cvr >= 0.95) else f"  95% variance not reached ({cvr[-1] * 100:.1f}% with all {n_components} components)",
        f"\n  Plot saved: {PLOTS_URL}/{plot_name}",
    ]

    return "\n".join(lines)


# ============================================================================
# Tool 4: PCA biplot
# ============================================================================

def plot_pca_biplot(
    filename: str,
    pc_x: int = 1,
    pc_y: int = 2,
    n_features: int = 10,
) -> str:
    """Generate a PCA biplot showing samples and feature arrows.

    Args:
        filename: CSV filename from list_available_experiments
        pc_x: PC for x-axis (default 1)
        pc_y: PC for y-axis (default 2)
        n_features: Number of top feature arrows to show (default 10)
    """
    df, trait_cols, config, source = _load_data(filename)
    if df is None:
        return config

    stem = Path(filename).stem

    if pc_x < 1 or pc_y < 1:
        return "PC numbers must be >= 1."

    try:
        pca_results = perform_pca_analysis(data=df[trait_cols], standardize=True)
    except Exception as e:
        return f"PCA failed: {e}"

    n_selected = int(pca_results["n_components_selected"])
    pc_x_idx = pc_x - 1
    pc_y_idx = pc_y - 1

    if pc_x_idx >= n_selected or pc_y_idx >= n_selected:
        return f"PC{max(pc_x, pc_y)} not available. Only {n_selected} components selected."

    scores = pca_results["transformed_data"]
    loadings = pca_results["loadings"]
    evr = pca_results["explained_variance_ratio"]
    feature_names = pca_results["feature_names"]

    top_idx = select_top_features_from_pca(
        loadings=loadings, eigenvalues=pca_results["eigenvalues"],
        n_features_total=len(feature_names), n_features_to_select=n_features,
        method="vector_length", pc_indices=[pc_x_idx, pc_y_idx],
    )

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(12, 10))

    genotype_col = config["genotype_col"]
    df_clean = df[trait_cols].dropna()
    geno_values = df.loc[df_clean.index, genotype_col].values if genotype_col and genotype_col in df.columns else None

    if geno_values is not None:
        unique_genos = sorted(set(geno_values))
        cmap = plt.cm.get_cmap("tab20", len(unique_genos))
        for i, geno in enumerate(unique_genos):
            mask = geno_values == geno
            ax.scatter(scores[mask, pc_x_idx], scores[mask, pc_y_idx], c=[cmap(i)], label=str(geno), alpha=0.5, s=15)
        if len(unique_genos) <= 20:
            ax.legend(bbox_to_anchor=(1.15, 1), loc="upper left", fontsize=7)
    else:
        ax.scatter(scores[:, pc_x_idx], scores[:, pc_y_idx], alpha=0.5, s=15)

    score_range_x = scores[:, pc_x_idx].max() - scores[:, pc_x_idx].min()
    score_range_y = scores[:, pc_y_idx].max() - scores[:, pc_y_idx].min()
    max_loading = max(np.abs(loadings[top_idx, pc_x_idx]).max(), np.abs(loadings[top_idx, pc_y_idx]).max())
    scale = 0.4 * max(score_range_x, score_range_y) / max_loading if max_loading > 0 else 1

    for idx in top_idx:
        dx = loadings[idx, pc_x_idx] * scale
        dy = loadings[idx, pc_y_idx] * scale
        ax.annotate("", xy=(dx, dy), xytext=(0, 0), arrowprops=dict(arrowstyle="->", color="red", lw=1.2, alpha=0.7))
        ax.text(dx * 1.08, dy * 1.08, feature_names[idx], fontsize=6, color="red", alpha=0.8)

    ax.set_xlabel(f"PC{pc_x} ({evr[pc_x_idx] * 100:.1f}%)")
    ax.set_ylabel(f"PC{pc_y} ({evr[pc_y_idx] * 100:.1f}%)")
    ax.set_title(f"PCA Biplot: {stem} (top {n_features} features)")
    ax.axhline(0, color="gray", linewidth=0.5)
    ax.axvline(0, color="gray", linewidth=0.5)

    plot_name = f"pca_biplot_{stem}_PC{pc_x}_PC{pc_y}.png"
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(PLOTS_DIR / plot_name, dpi=150, bbox_inches="tight")
    plt.close(fig)

    lines = [
        f"PCA Biplot: {filename} (source: {source})",
        f"  Axes: PC{pc_x} ({evr[pc_x_idx] * 100:.1f}%) vs PC{pc_y} ({evr[pc_y_idx] * 100:.1f}%)",
        f"  {scores.shape[0]} samples, {n_features} feature arrows shown",
        f"\n  Top features (by vector length in PC{pc_x}/PC{pc_y} plane):",
    ]

    for idx in top_idx[:5]:
        name = feature_names[idx]
        lx = loadings[idx, pc_x_idx]
        ly = loadings[idx, pc_y_idx]
        vec_len = np.sqrt(lx**2 + ly**2)
        lines.append(f"    {name}: loading=({lx:.3f}, {ly:.3f}), |v|={vec_len:.3f}")

    lines.append(f"\n  Plot saved: {PLOTS_URL}/{plot_name}")

    return "\n".join(lines)


# ============================================================================
# Registration
# ============================================================================

def register(mcp):
    """Register all PCA dimensionality reduction tools with the MCP server."""
    mcp.tool()(run_pca)
    mcp.tool()(get_pca_feature_contributions)
    mcp.tool()(plot_pca_scree)
    mcp.tool()(plot_pca_biplot)
