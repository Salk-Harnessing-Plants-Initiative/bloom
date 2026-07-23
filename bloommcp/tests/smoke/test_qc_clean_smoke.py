"""Live smoke: ``qc_clean`` through the real running dev stack (#483).

Real MCP-transport call (not in-process, not mocked) against both oracle fixtures --
see ``conftest.py`` and ``tests/fixtures/README.md``. The fast/unmarked oracle
assertions against the exact golden values live in
``tests/tools/test_qc_clean_tool.py``; this test only proves the tool round-trips
correctly through the real container.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.live_smoke


def test_qc_clean_smoke(call_tool, seeded_experiment: str) -> None:
    result = call_tool("sleap_roots_qc_clean", {"experiment": seeded_experiment})

    assert result["experiment"] == seeded_experiment
    assert result["n_samples_out"] > 0
    assert result["n_traits_out"] > 0
    assert result["cleaned_nan_cells_remaining"] == 0
    assert result["run_ref"]
    assert result["manifest_path"]
