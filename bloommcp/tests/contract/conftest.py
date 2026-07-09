"""Shared stub tool + models for the contract-layer unit suite.

Tier 1 has no real analysis tools (``pca_analysis``/``clustering`` are Tiers
3/4), so the contract decorator is exercised against a **stub** tool and a
**stub params model** standing in for the unmerged #191 input models. A
recorder captures what the decorator injects (resolved ``random_state`` and the
stamped ``Provenance``) so tests can assert the contract guarantees at the
wrapper boundary with no live Supabase.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from bloom_mcp.contract.models import ToolParams


class StubInput(ToolParams):
    """Stub tool input — extends the base params (inherits the optional seed)."""

    experiment: str


class StubOutput(BaseModel):
    """Stub tool output."""

    n_components: int


@pytest.fixture
def recorder() -> dict:
    """Mutable holder for what the stub tool receives from the decorator."""
    return {}
