"""Base Pydantic I/O models for contract-wrapped tools.

``ToolParams`` is the base input model — carries the optional ``seed`` field that
``@as_mcp_tool`` resolves, propagates as ``random_state=``, and records in
``Provenance``. Real tool input params extend this base.

``RunLinks`` is the base result model for consumer tools — carries the four
run-link fields (``run_ref``, ``version_dir``, ``manifest_path``, ``outputs``)
returned by every consumer tool result (``pca_analysis``, ``remove_outliers``,
and forthcoming ``clustering``/``umap``).
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from bloom_mcp.contract.provenance import SEED_MAX


class RunLinks(BaseModel):
    """Base result model for consumer tools.

    Carries the four run-link fields returned by every consumer tool result
    (``pca_analysis``, ``remove_outliers``, and forthcoming consumers). Tool-
    specific result models inherit this class rather than redeclaring the fields.
    """

    run_ref: str
    version_dir: str
    manifest_path: str
    outputs: dict[str, str]


class ToolParams(BaseModel):
    """Base input params for a contract-wrapped tool.

    The `seed` is optional; when absent, `@as_mcp_tool` resolves a concrete
    integer and records it in `Provenance` so the run stays reproducible. It is
    `strict` and range-bound to `[0, SEED_MAX)` so a float/bool/out-of-range
    value is rejected at input validation rather than recorded-but-invalid.
    """

    seed: Optional[int] = Field(default=None, ge=0, lt=SEED_MAX, strict=True)
