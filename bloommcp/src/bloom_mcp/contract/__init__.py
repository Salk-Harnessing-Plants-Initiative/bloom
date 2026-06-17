"""bloom-mcp tool contract: the uniform wrapper, provenance, and errors.

`@as_mcp_tool` gives every granular tool the same guarantees (validated Pydantic
I/O, structured `BloomMCPError`s, a single stamped `Provenance`) so provenance
and error handling are guaranteed by the contract, not per-tool boilerplate.
"""

from __future__ import annotations

from .errors import BloomMCPError
from .provenance import Provenance, resolve_environment, resolve_seed
from .wrap import as_mcp_tool

__all__ = [
    "as_mcp_tool",
    "BloomMCPError",
    "Provenance",
    "resolve_environment",
    "resolve_seed",
]
