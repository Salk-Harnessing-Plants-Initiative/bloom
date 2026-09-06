"""Versioned-run manifest/bookkeeping primitives for phenotyping analysis artifacts.

Renamed from `storage/` to `manifest/` (#487) — this package is about versioned-run
bookkeeping (manifests, directories, code versions), not physical storage backend
selection, which lives in the sibling `bloom_mcp.storage_backend`.
"""

from bloom_mcp.experiment_utils import OUTLIERS_TOOL_CLASS, QC_TOOL_CLASS

from .analysis_dir import AnalysisDir
from .code_versions import get_code_versions
from .manifest import (
    KNOWN_SCHEMA_VERSION,
    ManifestSchemaError,
    read_manifest,
    validate_schema,
    write_manifest,
)
from .schema import (
    CURRENT_SCHEMA_VERSION,
    CodeVersions,
    ExperimentBlock,
    Manifest,
    VersionEntry,
)
from .versioning import next_version_id, slugify, version_dir_name

# `qc`/`outliers` reference the single-sourced constants in `experiment_utils`
# (the producers, `qc_clean.py`/`remove_outliers.py`, do too) rather than
# re-typing the literal — see `list_existing_analyses.TOOL_CLASSES` for the
# same convention and its rationale (#420).
#
# This tuple SHALL remain a superset of `list_existing_analyses.TOOL_CLASSES` —
# every tool class that discovery loop iterates must also appear here. `pca`,
# `umap`, `qc_inspect` are added as plain re-typed literals rather than
# imported constants: `manifest` is foundational, versioned-run bookkeeping
# infrastructure that `sections/sleap_roots/analysis`'s tools (where each
# producer's own `_TOOL_CLASS` — `pca_analysis.py`, `umap_analysis.py`,
# `qc_inspect.py` — is defined) depend on, not the reverse; importing from
# there into here would invert that dependency direction, not merely cross a
# naming convention (bloom#669).
CANONICAL_TOOL_CLASSES: tuple[str, ...] = (
    QC_TOOL_CLASS,
    "stats",
    "dimred",
    "clustering",
    "outlier",
    OUTLIERS_TOOL_CLASS,
    "viz",
    "correlation",
    "heritability",
    "anova",
    "pca",
    "umap",
    "qc_inspect",
    # #466: plot_trait_histograms/_boxplots/plot_correlation_matrix converged onto
    # @as_mcp_tool + ResultStore persistence, each under its own class rather than the
    # shared (and still-unclaimed) "viz" slot above — see
    # openspec/changes/converge-bloommcp-viz-tools/design.md for why one shared class
    # would interleave 3 independent, non-composing producers' version history.
    "trait_histograms",
    "trait_boxplots",
    "correlation_matrix",
)

__all__ = [
    "AnalysisDir",
    "CANONICAL_TOOL_CLASSES",
    "CURRENT_SCHEMA_VERSION",
    "CodeVersions",
    "ExperimentBlock",
    "KNOWN_SCHEMA_VERSION",
    "Manifest",
    "ManifestSchemaError",
    "VersionEntry",
    "get_code_versions",
    "next_version_id",
    "read_manifest",
    "slugify",
    "validate_schema",
    "version_dir_name",
    "write_manifest",
]
