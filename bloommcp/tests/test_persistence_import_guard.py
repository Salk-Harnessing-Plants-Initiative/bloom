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

# Consumers that must obtain persistence via injected ports.
_CONSUMERS = [
    "tools/qc_tools.py",
    "tools/storage_tools.py",
    "tools/correlation_tools.py",
    "tools/workflows/_helpers.py",
    "tools/workflows/qc.py",
    "tools/workflows/stats.py",
    "tools/workflows/dimred.py",
    "tools/workflows/clustering.py",
    "tools/workflows/outlier.py",
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
