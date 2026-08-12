"""Guard: read/write consumers depend only on the ports, not Supabase/storage.

After the repoint (tasks.md §4) no consumer imports `supabase` or `AnalysisDir`
directly — persistence comes via the injected ports. The scan is AST-based (real
Import/ImportFrom nodes), not a substring match, so a comment or docstring
mentioning a forbidden name doesn't trip it and a typo'd path fails loudly.

Also guards the `storage/`→`manifest/` rename's BREAKING contract (#487)
permanently: `bloom_mcp.storage` must stay gone (no compatibility alias) and
`AnalysisWriter` must never resurface, so a future partial revert is caught by
CI rather than a manual check.

One disclosed, narrow exception (bloom#585): `sections/core/list_existing_analyses.py`
imports `experiment_utils.trim_staleness`, which itself reads through
`AnalysisDir` — a transitive, not direct, dependency this file's AST scan
does not (and is not meant to) catch. It is an ambient, advisory-only signal
layered on top of the ports-backed `analyses` payload, not a replacement data
path — see `add-bloommcp-outliers-staleness-audit/design.md` Decision 2.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src" / "bloom_mcp"

# Consumers that must obtain persistence via injected ports. The Phase-1 workflow
# tools (tools/workflows/*, _helpers.py) are retired by devendor-bloommcp-analysis
# (C7) — the port-only guarantee they used to (vacuously) cover is now enforced on
# the real write consumers: the 5 granular persistence-writing tools.
# tools/correlation_tools.py was dropped in C9 (see test_correlation_tools_absent).
# Paths repointed to sections/sleap_roots/analysis/ + sections/core/ by the
# Phase-2 sections migration (P2.2/P2.3) — tools/qc_tools.py, storage_tools.py,
# and the 5 *_tool.py files are gone.
_CONSUMERS = [
    "sections/core/list_available_experiments.py",
    "sections/core/load_experiment_data.py",
    "sections/core/list_existing_analyses.py",
    # get_download_links.py (bloom#599) — a fourth core read consumer, added here
    # for the same reason list_existing_analyses.py already is.
    "sections/core/get_download_links.py",
    "sections/sleap_roots/analysis/pca_analysis.py",
    "sections/sleap_roots/analysis/qc_clean.py",
    "sections/sleap_roots/analysis/qc_inspect.py",
    "sections/sleap_roots/analysis/remove_outliers.py",
    "sections/sleap_roots/analysis/clustering.py",
    # umap_analysis.py (#425) and descriptive_stats.py (#488) were both missing from
    # this list pre-dating this change — added here, closing that pre-existing drift.
    "sections/sleap_roots/analysis/umap_analysis.py",
    "sections/sleap_roots/analysis/descriptive_stats.py",
]

# Names that may not be imported by a consumer module.
_FORBIDDEN = {"supabase", "AnalysisDir"}


def _imported_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.split(".")[0])
            for alias in node.names:
                names.add(alias.name)
    return names


def test_consumers_do_not_import_supabase_or_storage_writer():
    offenders: list[str] = []
    for rel in _CONSUMERS:
        path = _SRC / rel
        assert path.exists(), f"guard lists a missing module: {rel}"
        imported = _imported_names(ast.parse(path.read_text(encoding="utf-8")))
        hits = imported & _FORBIDDEN
        if hits:
            offenders.append(f"{rel}: {sorted(hits)}")
    assert not offenders, f"consumers still import persistence directly: {offenders}"


def test_manifest_package_rename_is_permanent():
    """The `storage/`→`manifest/` rename (#487) has no compatibility alias."""
    import bloom_mcp.manifest as manifest

    for name in (
        "AnalysisDir",
        "Manifest",
        "VersionEntry",
        "get_code_versions",
        "next_version_id",
        "slugify",
        "version_dir_name",
        "read_manifest",
        "write_manifest",
        "validate_schema",
    ):
        assert hasattr(manifest, name), f"bloom_mcp.manifest lost re-export: {name}"

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("bloom_mcp.storage")


def test_analysis_writer_is_gone():
    """The dead `AnalysisWriter` class (and its module) must never resurface."""
    assert not hasattr(
        __import__("bloom_mcp.manifest", fromlist=["manifest"]), "AnalysisWriter"
    )
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("bloom_mcp.manifest.writer")
