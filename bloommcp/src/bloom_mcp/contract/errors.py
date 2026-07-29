"""Structured, agent-safe errors for the bloom-mcp tool contract.

`@as_mcp_tool` maps every failure to a `BloomMCPError` carrying a stable `code`,
a human `message`, and a `remedy`. Two promises hold:

- **never a raw traceback** — failures are re-raised `from None`; and
- **never leak internals** — an *internal* failure (an undeclared exception or
  an output-contract breach) returns a fixed message plus a short correlation
  id, while the detail (which may carry paths, hosts, connection strings, SQL,
  or bucket keys) goes only to the server log. Input-validation errors surface
  only the field locations + error types, never the offending values.

A *declared* exception (one the tool author opted into via `errors=`) is treated
as author-controlled and its message is passed through.
"""

from __future__ import annotations

import logging
import uuid

logger = logging.getLogger(__name__)


def _validation_locs(exc: Exception) -> str:
    """Summarize a Pydantic ValidationError as 'loc: type' pairs — no values."""
    errors = getattr(exc, "errors", None)
    if not callable(errors):
        return "invalid input"
    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ()))
        parts.append(f"{loc or '<root>'}: {err.get('type', 'invalid')}")
    return "; ".join(parts) or "invalid input"


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
        """Map an input-model validation failure to a user-fixable error.

        Surfaces only the field locations + error types — never the offending
        input values, which could carry sensitive data.
        """
        return cls(
            code="invalid_input",
            message=f"Input did not match the tool's schema ({_validation_locs(exc)}).",
            remedy="Correct the arguments to match the tool's input schema and retry.",
        )

    @classmethod
    def from_output_validation(cls, exc: Exception) -> "BloomMCPError":
        """Map an output-model validation failure to an internal-breach error.

        The detail is logged server-side under a correlation id; the agent sees
        only the fixed message + the id.
        """
        ref = uuid.uuid4().hex[:12]
        logger.error("output-contract breach [%s]: %s", ref, exc)
        return cls(
            code="internal_output_contract",
            message=f"Tool produced output that violates its declared schema (ref: {ref}).",
            remedy="This is an internal contract breach; report the ref id — not a user error.",
        )

    @classmethod
    def from_exception(
        cls, exc: Exception, *, declared: tuple[type[Exception], ...] = ()
    ) -> "BloomMCPError":
        """Map a raised exception to a structured error, never leaking internals.

        A *declared* (expected, author-controlled) exception becomes a
        ``tool_error`` whose message is passed through. Anything else becomes an
        ``internal_error`` with a fixed message + correlation id; the detail is
        logged server-side, never returned to the agent.
        """
        if isinstance(exc, declared):
            return cls(
                code="tool_error",
                message=str(exc) or exc.__class__.__name__,
                remedy="Check the inputs/experiment for this tool and retry.",
            )
        ref = uuid.uuid4().hex[:12]
        logger.error("internal tool error [%s]", ref, exc_info=exc)
        return cls(
            code="internal_error",
            message=f"An unexpected internal error occurred (ref: {ref}).",
            remedy="Report the ref id; the detail is in the server logs.",
        )
