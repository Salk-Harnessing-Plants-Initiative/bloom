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


class OutputLink(BaseModel):
    """A pointer to one persisted output artifact (bloom#581, #642 follow-up).

    ``key`` is the same object key surfaced in ``RunLinks.outputs``. Exactly one
    of ``url``/``path`` is set: ``url`` is a signed download link (Supabase
    backend, or an operator-configured local ``BLOOM_STORAGE_URL``); ``path``
    is the resolved absolute filesystem path for the local backend's default
    (no served URL — the caller already has direct filesystem access to a file
    bloommcp just wrote, so there is nothing to sign or serve). ``sha256``
    matches the manifest's ``output_sha256`` for this artifact; ``size_bytes``
    is the artifact's byte size (non-negative — a zero-byte artifact is legal).
    """

    key: str
    url: Optional[str] = None
    path: Optional[str] = None
    sha256: str
    size_bytes: int = Field(ge=0)


class RunLinks(BaseModel):
    """Base result model for consumer tools.

    Carries the four run-link fields returned by every consumer tool result
    (``pca_analysis``, ``remove_outliers``, and forthcoming consumers). Tool-
    specific result models inherit this class rather than redeclaring the fields.

    ``output_links`` (bloom#581) is additive: one ``OutputLink`` per ``outputs``
    entry, populated only when the result comes from a fresh ``ResultStore.commit()``
    (never from ``get_run``/``list_runs``, which leave it empty) — see
    ``bloommcp-result-store``'s "Per-Output Signed Links And Size At Commit".
    """

    run_ref: str
    version_dir: str
    manifest_path: str
    outputs: dict[str, str]
    output_links: dict[str, OutputLink] = Field(default_factory=dict)


class ToolParams(BaseModel):
    """Base input params for a contract-wrapped tool.

    The `seed` is optional; when absent, `@as_mcp_tool` resolves a concrete
    integer and records it in `Provenance` so the run stays reproducible. It is
    `strict` and range-bound to `[0, SEED_MAX)` so a float/bool/out-of-range
    value is rejected at input validation rather than recorded-but-invalid.
    """

    seed: Optional[int] = Field(default=None, ge=0, lt=SEED_MAX, strict=True)
