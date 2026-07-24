"""Versioned-run manifest/bookkeeping primitives for phenotyping analysis artifacts.

Renamed from `storage/` to `manifest/` (#487) — this package is about versioned-run
bookkeeping (manifests, directories, code versions), not physical storage backend
selection, which lives in the sibling `bloom_mcp.storage_backend`.
"""

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

CANONICAL_TOOL_CLASSES: tuple[str, ...] = (
    "qc",
    "stats",
    "dimred",
    "clustering",
    "outlier",
    "viz",
    "correlation",
    "heritability",
    "anova",
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
