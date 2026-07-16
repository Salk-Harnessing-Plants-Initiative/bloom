"""Guard: read/write consumers depend only on the ports, not Supabase/storage.

After the repoint (tasks.md §4) no consumer imports `supabase`, `AnalysisWriter`,
or `AnalysisDir` directly — persistence comes via the injected ports. The scan is
AST-based (real Import/ImportFrom nodes), not a substring match, so a comment or
docstring mentioning a forbidden name doesn't trip it and a typo'd path fails loudly.
"""

from __future__ import annotations

import ast
from pathlib import Path

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
    "sections/sleap_roots/analysis/pca_analysis.py",
    "sections/sleap_roots/analysis/qc_clean.py",
    "sections/sleap_roots/analysis/qc_inspect.py",
    "sections/sleap_roots/analysis/remove_outliers.py",
    "sections/sleap_roots/analysis/clustering.py",
]

# Names that may not be imported by a consumer module.
_FORBIDDEN = {"supabase", "AnalysisWriter", "AnalysisDir"}


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
        imported = _imported_names(ast.parse(path.read_text()))
        hits = imported & _FORBIDDEN
        if hits:
            offenders.append(f"{rel}: {sorted(hits)}")
    assert not offenders, f"consumers still import persistence directly: {offenders}"
