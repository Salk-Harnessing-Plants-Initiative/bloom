"""Live smoke: ``umap_analysis`` through the real running dev stack (#425).

Real MCP-transport call. ``umap_analysis`` requires a cleaned version
(``require_clean=True``), so this calls ``qc_clean`` first. Implicitly parametrized over
**both** oracle fixtures via ``db_experiment_id``'s dependency on the session-wide
``fixture_name`` fixture (``params=["turface_19", "cylinder"]`` in ``conftest.py``) — not
just turface_19, and with no per-test parametrize mark needed here.

Neither fixture is marked ``live_smoke_slow``, unlike ``clustering``'s GMM-on-cylinder case:
that mark exists there because a full covariance matrix per component over ~588 traits vs
~123 samples is wildly underdetermined for GMM specifically. UMAP's k-NN graph + embedding
has no analogous full-covariance step, and the default ``n_neighbors=15`` is comfortably
below both fixtures' sample counts (turface_19's raw fixture: 187 samples; cylinder's: 129
samples) — the same reasoning ``test_pca_analysis_smoke.py`` gives for keeping its
846-trait cylinder case in the CI-safe subset.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.live_smoke


def test_umap_analysis_smoke(call_tool, db_experiment_id: str) -> None:
    call_tool("sleap_roots_qc_clean", {"experiment": db_experiment_id})

    result = call_tool(
        "sleap_roots_umap_analysis",
        {"experiment": db_experiment_id, "seed": 42},
    )

    assert result["experiment"] == db_experiment_id
    assert result["n_samples"] > 0
    assert result["n_components"] == 2
    assert result["seed"] == 42
    assert result["run_ref"]
    assert result["manifest_path"]
