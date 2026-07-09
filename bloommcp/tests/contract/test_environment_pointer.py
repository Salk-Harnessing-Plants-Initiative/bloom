"""The environment pointer resolves by precedence and is reproducible.

Maps the spec "Exact-Environment Provenance Pointer" scenarios: the image
digest takes precedence when present; otherwise a non-empty fallback resolves
(never "unknown", distinct from the code_versions trace).
"""

from __future__ import annotations

from bloom_mcp.contract.provenance import resolve_environment
from bloom_mcp.storage.code_versions import get_code_versions


def test_image_digest_takes_precedence(monkeypatch):
    """BLOOM_MCP_IMAGE_DIGEST, when set, is the environment pointer."""
    monkeypatch.setenv("BLOOM_MCP_IMAGE_DIGEST", "sha256:abc123")
    assert resolve_environment() == "sha256:abc123"


def test_fallback_is_non_empty_and_distinct(monkeypatch):
    """With no digest, the fallback resolves non-empty, never 'unknown'."""
    monkeypatch.delenv("BLOOM_MCP_IMAGE_DIGEST", raising=False)

    env = resolve_environment()
    assert env
    assert env != "unknown"
    # Distinct from the human-readable code_versions trace.
    assert env != get_code_versions().model_dump(exclude_none=True)
