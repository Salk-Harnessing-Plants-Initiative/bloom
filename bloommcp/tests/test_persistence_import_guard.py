"""Guard: read/write consumers depend only on the ports, not Supabase/storage.

After the repoint (tasks.md §4) no consumer imports `supabase`, `AnalysisWriter`,
or `AnalysisDir` directly — persistence comes via the injected ports.
"""

from __future__ import annotations

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

# Tokens that may not appear on an import line in a consumer module.
_FORBIDDEN = ("supabase", "AnalysisWriter", "AnalysisDir")


def _import_offenders() -> list[str]:
    offenders: list[str] = []
    for rel in _CONSUMERS:
        path = _SRC / rel
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            stripped = line.strip()
            if not (stripped.startswith("import ") or stripped.startswith("from ")):
                continue
            for token in _FORBIDDEN:
                if token in stripped:
                    offenders.append(f"{rel}: {stripped}")
    return offenders


def test_consumers_do_not_import_supabase_or_storage_writer():
    offenders = _import_offenders()
    assert not offenders, f"consumers still import persistence directly: {offenders}"
