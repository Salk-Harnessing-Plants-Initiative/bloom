"""Base Pydantic I/O models for contract-wrapped tools.

Tier 1 ships a minimal base: #191 (per-tool Pydantic input models) is unmerged,
and the real `pca_analysis` / `clustering` models arrive with the granular tools
(Tiers 3/4). `ToolParams` carries the one field the contract reads directly —
the optional `seed`, which `@as_mcp_tool` resolves and propagates as
`random_state=`. Real tool params extend this base.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class ToolParams(BaseModel):
    """Base input params for a contract-wrapped tool.

    The `seed` is optional; when absent, `@as_mcp_tool` resolves a concrete
    integer and records it in `Provenance` so the run stays reproducible.
    """

    seed: Optional[int] = None
