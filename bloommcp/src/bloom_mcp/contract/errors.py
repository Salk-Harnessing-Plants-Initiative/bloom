"""Structured, agent-safe errors for the bloom-mcp tool contract.

`@as_mcp_tool` maps every failure to a `BloomMCPError` carrying a stable `code`,
a human `message`, and a `remedy`. A raw traceback is never returned to the
agent — the structured form (`to_dict`) is what crosses the wire.
"""

from __future__ import annotations


class BloomMCPError(Exception):
    """A structured error surfaced to the agent (code + message + remedy)."""

    def __init__(self, code: str, message: str, remedy: str) -> None:
        """Store the structured fields and a flat message for ``str()``."""
        self.code = code
        self.message = message
        self.remedy = remedy
        super().__init__(f"[{code}] {message} — {remedy}")

    def to_dict(self) -> dict[str, str]:
        """Return the serializable structured form (no traceback)."""
        return {"code": self.code, "message": self.message, "remedy": self.remedy}

    @classmethod
    def from_input_validation(cls, exc: Exception) -> "BloomMCPError":
        """Map an input-model validation failure to a user-fixable error."""
        return cls(
            code="invalid_input",
            message=f"Input did not match the tool's schema: {exc}",
            remedy="Correct the arguments to match the tool's input schema and retry.",
        )

    @classmethod
    def from_output_validation(cls, exc: Exception) -> "BloomMCPError":
        """Map an output-model validation failure to an internal-breach error."""
        return cls(
            code="internal_output_contract",
            message=f"Tool produced output that violates its declared schema: {exc}",
            remedy="This is an internal contract breach; report it — not a user error.",
        )

    @classmethod
    def from_exception(
        cls, exc: Exception, *, declared: tuple[type[Exception], ...] = ()
    ) -> "BloomMCPError":
        """Map a raised exception to a structured error, never leaking a traceback.

        Declared (expected) exceptions become a ``tool_error``; anything else is
        an ``internal_error``. The ``message`` carries only ``str(exc)``, never a
        formatted traceback.
        """
        if isinstance(exc, declared):
            return cls(
                code="tool_error",
                message=str(exc) or exc.__class__.__name__,
                remedy="Check the inputs/experiment for this tool and retry.",
            )
        return cls(
            code="internal_error",
            message=str(exc) or exc.__class__.__name__,
            remedy="An unexpected internal error occurred; report it.",
        )
