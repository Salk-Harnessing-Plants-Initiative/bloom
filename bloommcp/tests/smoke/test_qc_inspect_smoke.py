"""Live smoke: ``qc_inspect`` through the real running dev stack (#483).

Real MCP-transport call against both oracle fixtures -- see ``conftest.py``. The
fast/unmarked oracle assertions live in ``tests/tools/test_qc_inspect_tool.py``.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.live_smoke


def test_qc_inspect_smoke(call_tool, seeded_experiment: str) -> None:
    result = call_tool("sleap_roots_qc_inspect", {"experiment": seeded_experiment})

    assert result["experiment"] == seeded_experiment
    assert result["n_samples"] > 0
    assert result["n_traits"] > 0
    assert "recommendation" in result
    assert result["run_ref"]
    assert result["manifest_path"]
