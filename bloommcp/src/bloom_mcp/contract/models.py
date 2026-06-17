"""Base Pydantic I/O models for contract-wrapped tools.

Tier 1 ships a minimal base: #191 (per-tool Pydantic input models) is unmerged,
and the real `pca_analysis` / `clustering` models arrive with the granular tools
(Tiers 3/4). `ToolParams` carries the one field the contract reads directly —
the optional `seed`, which `@as_mcp_tool` resolves and propagates as
`random_state=`. Real tool params extend this base.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from bloom_mcp.contract.provenance import SEED_MAX


class ToolParams(BaseModel):
    """Base input params for a contract-wrapped tool.

    The `seed` is optional; when absent, `@as_mcp_tool` resolves a concrete
    integer and records it in `Provenance` so the run stays reproducible. It is
    `strict` and range-bound to `[0, SEED_MAX)` so a float/bool/out-of-range
    value is rejected at input validation rather than recorded-but-invalid.
    """

    seed: Optional[int] = Field(default=None, ge=0, lt=SEED_MAX, strict=True)
