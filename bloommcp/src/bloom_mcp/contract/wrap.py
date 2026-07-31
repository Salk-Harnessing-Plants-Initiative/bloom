"""`@as_mcp_tool` — the uniform contract every granular tool wraps.

This decorator is **our own** glue layered over FastMCP (the MCP framework,
already a dependency) with **Pydantic** for the I/O models — it wraps, it does
not replace. On every call it:

1. validates the tool's declared Pydantic **input** model (→ `BloomMCPError`),
2. resolves the seed and stamps a single contract-time `Provenance`,
3. invokes the tool, injecting the resolved `random_state` and the `provenance`
   into the call **only** for parameters the tool declares (an explicit
   kwarg-injection contract — not name inference),
4. maps any raised exception to a structured `BloomMCPError` (never a raw
   traceback, never leaked internals), and
5. validates the declared Pydantic **output** model (→ `BloomMCPError`).

**Seed provenance integrity.** The resolved seed is recorded in `Provenance`
*only when it is actually applied* — i.e. the delegate declares a
``random_state`` parameter. A non-stochastic tool (no ``random_state``) records
``seed=None``. If a seed was *explicitly provided* but the delegate cannot
accept it, that is a tool-wiring bug and raises an ``internal_error`` rather than
recording a seed that never reached the computation (a reproducibility lie).

It does **not** call `np.random.seed()`: global re-seeding would duplicate and
fight `sleap-roots-analyze`'s per-estimator seeding (and would not reach UMAP's
numba RNG). Determinism of the function is upstream's (CI-guarded);
reproducibility of the stored artifact is ours, via the recorded resolved seed.

**Registration.** Tools register onto a FastMCP instance through the
`register(mcp, *tools)` seam at server-wiring time — the `mcp` instance does not
exist at decoration time, and Tier 1 does not modify `server.py`. The wrapped
callable carries a clean single-`params` signature so FastMCP builds a correct
input schema without seeing the injected `random_state` / `provenance` kwargs.
"""

from __future__ import annotations

import functools
import inspect
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from .errors import BloomMCPError
from .provenance import Provenance, resolve_seed


def register(mcp: Any, *tools: Callable) -> Any:
    """Register contract-wrapped tools onto a FastMCP instance.

    The seam the spec mandates: keeps tools decoupled from the live `mcp`
    instance at decoration time. Equivalent to calling `mcp.tool()(tool)` for
    each, returned `mcp` for chaining.

    Deliberately does NOT wrap tools with any per-call bookkeeping (usage
    recording included — see `bloom_mcp.identity.IdentityMiddleware`, which
    records usage itself instead). A per-tool wrapper here was tried and
    reverted: for a reused MCP `streamable-http` session (the common case —
    one client session spans many tool calls), FastMCP's `StreamableHTTPSessionManager`
    starts the tool-dispatch loop in a single long-lived task once, at session
    creation (`_handle_stateful_request`/`self._task_group.start(run_server)`
    in the installed `mcp` package); later requests in that session only feed
    a stream into that already-running task, never re-entering it. A
    `contextvars.ContextVar` set by an outer ASGI middleware on a *later*
    request's own task is a different Context object and is structurally
    invisible inside that already-running task — confirmed by reading
    `mcp/server/streamable_http_manager.py` directly and independently by the
    well-defined semantics of `asyncio`/`anyio` task creation (a spawned
    task's Context is snapshotted at creation time). This is not unique to
    this repo's code — FastMCP's own `get_http_headers()`/`get_http_request()`
    rely on the identical mechanism (`RequestContextMiddleware`,
    `fastmcp/server/http.py`) and inherit the same limitation for a reused
    session; `SessionMessage`'s per-message metadata carries no HTTP header
    info to work around it. See
    openspec/changes/add-bloommcp-caller-identity/design.md Decision 4.
    """
    for tool in tools:
        mcp.tool()(tool)
    return mcp


def as_mcp_tool(
    *,
    input_model: type[BaseModel],
    output_model: type[BaseModel],
    errors: tuple[type[Exception], ...] = (),
) -> Callable[[Callable], Callable]:
    """Wrap a tool with the bloom-mcp contract guarantees.

    Args:
        input_model: Pydantic model validating the tool's inputs (carries `seed`).
        output_model: Pydantic model validating the tool's result.
        errors: Declared exception types mapped to a `tool_error` BloomMCPError;
            anything else maps to an `internal_error` (never a raw traceback,
            never leaked internals).

    Returns:
        A decorator producing a contract-wrapped callable, registrable onto a
        FastMCP instance via `register(mcp, wrapped)`.
    """

    def decorator(func: Callable) -> Callable:
        accepted = set(inspect.signature(func).parameters)
        accepts_seed = "random_state" in accepted
        accepts_provenance = "provenance" in accepted

        @functools.wraps(func)
        def wrapper(params: Any = None, **kwargs: Any) -> BaseModel:
            raw = params if params is not None else kwargs
            if isinstance(raw, input_model):
                data = raw
            else:
                try:
                    data = input_model.model_validate(raw)
                except ValidationError as exc:
                    raise BloomMCPError.from_input_validation(exc) from None

            requested = getattr(data, "seed", None)
            if accepts_seed:
                try:
                    seed = resolve_seed(requested)
                except (ValueError, TypeError) as exc:
                    raise BloomMCPError.from_input_validation(exc) from None
            elif requested is not None:
                # A seed was given but the delegate can't apply it — recording it
                # would be a reproducibility lie. Surface a tool-wiring bug.
                raise BloomMCPError(
                    code="internal_error",
                    message=(
                        "A seed was provided but this tool's delegate does not "
                        "accept a random_state parameter."
                    ),
                    remedy="Remove the seed, or wire the delegate to accept random_state.",
                )
            else:
                seed = None

            provenance = Provenance.stamp(
                tool=func.__name__, params=data.model_dump(), seed=seed
            )

            extra: dict[str, Any] = {}
            if accepts_seed:
                extra["random_state"] = seed
            if accepts_provenance:
                extra["provenance"] = provenance

            try:
                result = func(data, **extra)
            except BloomMCPError:
                raise
            except Exception as exc:  # noqa: BLE001 — mapped, never re-raised raw
                raise BloomMCPError.from_exception(exc, declared=errors) from None

            if isinstance(result, output_model):
                return result
            try:
                return output_model.model_validate(result)
            except ValidationError as exc:
                raise BloomMCPError.from_output_validation(exc) from None

        # Present a clean single-`params` signature to FastMCP (hides the
        # injected kwargs). `functools.wraps` preserves identity + `__wrapped__`
        # (so `inspect.unwrap` works); the explicit `__signature__` takes
        # precedence over `__wrapped__` for `inspect.signature`.
        wrapper.__signature__ = inspect.Signature(
            [
                inspect.Parameter(
                    "params",
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    annotation=input_model,
                )
            ],
            return_annotation=output_model,
        )
        wrapper.__annotations__ = {"params": input_model, "return": output_model}
        return wrapper

    return decorator
