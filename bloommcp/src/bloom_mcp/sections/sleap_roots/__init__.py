"""sleap_roots section — umbrella for the sleap-roots pipeline family.

Bends the one-section-per-package convention deliberately: these tools wrap
``sleap-roots-analyze``, but the user's mental model is the root-phenotyping
*pipeline* as a whole (extraction via ``sleap-roots`` feeding analysis via
``sleap-roots-analyze``), so the section is named after the family, not the
one package it happens to populate today. See
``bloommcp/docs/2026-06-29-bloom-mcp-contributor-namespacing.md`` for the full
rationale (D3 in this change's ``design.md``).

Two subgroups:
  - ``analysis/`` — the 8 granular ``sleap-roots-analyze`` consumers
    (``pca_analysis``, ``qc_clean``, ``qc_inspect``, ``remove_outliers``,
    ``clustering``, ``umap_analysis``, ``descriptive_stats``,
    ``cross_experiment_correlations``) + the 5 surviving plotting tools.
    Populated here.
  - ``extraction/`` — reserved for future ``sleap-roots`` trait-extraction
    tools. Empty; not built in this change.

The server mounts this section into the combined ``/mcp`` surface (tools appear
namespaced ``sleap_roots_<name>``) and serves it at its own
``/sleap_roots/mcp`` URL — no ``server.py`` edit needed per tool.
"""

from fastmcp import FastMCP

from bloom_mcp.auth import auth_provider
from bloom_mcp.contract import register

from .analysis import (
    clustering,
    cross_experiment_correlations,
    descriptive_stats,
    pca_analysis,
    plot_correlation_matrix,
    plot_heritability_bar,
    plot_trait_boxplots,
    plot_trait_histograms,
    plot_variance_decomposition,
    qc_clean,
    qc_inspect,
    remove_outliers,
    umap_analysis,
)

section = FastMCP("sleap-roots", auth=auth_provider)

# Register every tool in this section. Add new tools here.
register(
    section,
    pca_analysis.pca_analysis,
    qc_clean.qc_clean,
    qc_inspect.qc_inspect,
    remove_outliers.remove_outliers,
    clustering.clustering,
    umap_analysis.umap_analysis,
    descriptive_stats.descriptive_stats,
    cross_experiment_correlations.cross_experiment_correlations,
    plot_trait_histograms.plot_trait_histograms,
    plot_trait_boxplots.plot_trait_boxplots,
    plot_correlation_matrix.plot_correlation_matrix,
    plot_heritability_bar.plot_heritability_bar,
    plot_variance_decomposition.plot_variance_decomposition,
)
