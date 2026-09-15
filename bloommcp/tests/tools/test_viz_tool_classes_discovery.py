"""#466 — the 3 converged viz tools' new tool classes are registered for discovery.

Mirrors ``test_remove_outliers_tool.py``'s
``test_outliers_class_registered_in_discovery_and_canonical_registries`` (a direct membership
assertion, so a typo in either registry fails directly) plus an end-to-end check that a
committed run under each new class actually surfaces via ``list_existing_analyses``.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from bloom_mcp.data_access import FakeReader, SupabaseReader
from bloom_mcp.result_store import FakeResultStore, SupabaseResultStore
from bloom_mcp.sections.core import list_existing_analyses as list_existing_analyses_mod
from bloom_mcp.tools import _ports
from bloom_mcp.sections.sleap_roots.analysis.plot_correlation_matrix import (
    PlotCorrelationMatrixParams,
    plot_correlation_matrix,
)
from bloom_mcp.sections.sleap_roots.analysis.plot_trait_boxplots import (
    PlotTraitBoxplotsParams,
    plot_trait_boxplots,
)
from bloom_mcp.sections.sleap_roots.analysis.plot_trait_histograms import (
    PlotTraitHistogramsParams,
    plot_trait_histograms,
)

_EXPERIMENT = "viz_discovery.csv"


def _df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Barcode": [f"b{i}" for i in range(10)],
            "geno": ["g1", "g2"] * 5,
            "t1": [float(i) for i in range(10)],
            "t2": [float(2 * i + 1) for i in range(10)],
        }
    )


def test_new_tool_classes_registered_in_discovery_and_canonical_registries():
    from bloom_mcp.manifest import CANONICAL_TOOL_CLASSES
    from bloom_mcp.sections.core.list_existing_analyses import TOOL_CLASSES

    for tool_class in ("trait_histograms", "trait_boxplots", "correlation_matrix"):
        assert tool_class in TOOL_CLASSES
        assert tool_class in CANONICAL_TOOL_CLASSES


@pytest.fixture
def injected_ports():
    reader = FakeReader()
    reader.add_experiment(_EXPERIMENT, _df())
    store = FakeResultStore()
    _ports.configure(reader=reader, store=store)
    list_existing_analyses_mod._RESPONSE_CACHE.clear()
    try:
        yield reader, store
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())
        list_existing_analyses_mod._RESPONSE_CACHE.clear()


def test_committed_runs_from_all_3_tools_are_discoverable(injected_ports):
    plot_trait_histograms(PlotTraitHistogramsParams(experiment=_EXPERIMENT))
    plot_trait_boxplots(PlotTraitBoxplotsParams(experiment=_EXPERIMENT))
    plot_correlation_matrix(PlotCorrelationMatrixParams(experiment=_EXPERIMENT))

    payload = json.loads(list_existing_analyses_mod.list_existing_analyses(_EXPERIMENT))
    analyses = payload["analyses"]

    assert "trait_histograms" in analyses and len(analyses["trait_histograms"]) == 1
    assert "trait_boxplots" in analyses and len(analyses["trait_boxplots"]) == 1
    assert "correlation_matrix" in analyses and len(analyses["correlation_matrix"]) == 1

    # Each tool's run lives in its own version lineage — none affects another's.
    for entry in (
        analyses["trait_histograms"][0],
        analyses["trait_boxplots"][0],
        analyses["correlation_matrix"][0],
    ):
        assert entry["version_dir"].startswith("v1")


def test_interleaved_calls_across_tools_advance_independent_version_lineages(
    injected_ports,
):
    """Stronger than the v1-only check above: calling the 3 tools out of order and more
    than once must not interleave their version counters — each tool_class's lineage
    advances only on its own calls (#466 review: the v1-only assertion is structurally
    safe per the store's (experiment, tool_class) keying, but was previously untested as
    such)."""
    plot_trait_boxplots(PlotTraitBoxplotsParams(experiment=_EXPERIMENT))
    plot_correlation_matrix(PlotCorrelationMatrixParams(experiment=_EXPERIMENT))
    plot_trait_histograms(PlotTraitHistogramsParams(experiment=_EXPERIMENT))
    plot_correlation_matrix(PlotCorrelationMatrixParams(experiment=_EXPERIMENT))
    plot_trait_boxplots(PlotTraitBoxplotsParams(experiment=_EXPERIMENT))

    payload = json.loads(list_existing_analyses_mod.list_existing_analyses(_EXPERIMENT))
    analyses = payload["analyses"]

    assert [e["version_dir"][:2] for e in analyses["trait_boxplots"]] == ["v1", "v2"]
    assert [e["version_dir"][:2] for e in analyses["correlation_matrix"]] == [
        "v1",
        "v2",
    ]
    assert [e["version_dir"][:2] for e in analyses["trait_histograms"]] == ["v1"]
