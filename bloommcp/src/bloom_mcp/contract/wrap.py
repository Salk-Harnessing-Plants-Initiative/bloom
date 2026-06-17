"""`@as_mcp_tool` — the uniform contract every granular tool wraps.

This decorator is **our own** glue layered over FastMCP (the MCP framework,
already a dependency) with **Pydantic** for the I/O models — it wraps, it does
not replace. On every call it:

1. validates the tool's declared Pydantic **input** model (→ `BloomMCPError`),
2. resolves the seed (a concrete integer even when none was given) and stamps a
   single contract-time `Provenance`,
3. invokes the tool, injecting the resolved `random_state` and the `provenance`
   into the call **only** for parameters the tool declares (an explicit
   kwarg-injection contract — not name inference),
4. maps any raised exception to a structured `BloomMCPError` (never a raw
   traceback), and
5. validates the declared Pydantic **output** model (→ `BloomMCPError`).

It does **not** call `np.random.seed()`: global re-seeding would duplicate and
fight `sleap-roots-analyze`'s per-estimator seeding (and would not reach UMAP's
numba RNG). Determinism of the function is upstream's (CI-guarded);
reproducibility of the stored artifact is ours, via the recorded resolved seed.

Registration onto a FastMCP instance happens through a `register(mcp)` seam at
server-wiring time — the `mcp` instance does not exist at decoration time, and
Tier 1 does not modify `server.py`. The wrapped callable carries a clean
single-`params` signature so FastMCP builds a correct input schema without
seeing the injected `random_state` / `provenance` kwargs.
"""

from __future__ import annotations

import inspect
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from .errors import BloomMCPError
from .provenance import Provenance, resolve_seed

# Kwargs the decorator injects into a tool that declares them.
_INJECTED = ("random_state", "provenance")


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
            anything else maps to an `internal_error` (never a raw traceback).

    Returns:
        A decorator producing a contract-wrapped callable, registrable onto a
        FastMCP instance via `mcp.tool()(wrapped)` in a `register(mcp)` seam.
    """

    def decorator(func: Callable) -> Callable:
        accepted = set(inspect.signature(func).parameters)

        def wrapper(params: Any = None, /, **kwargs: Any) -> BaseModel:
            raw = params if params is not None else kwargs
            if isinstance(raw, input_model):
                data = raw
            else:
                try:
                    data = input_model.model_validate(raw)
                except ValidationError as exc:
                    raise BloomMCPError.from_input_validation(exc) from None

            seed = resolve_seed(getattr(data, "seed", None))
            provenance = Provenance.stamp(
                tool=func.__name__, params=data.model_dump(), seed=seed
            )

            injectable = {"random_state": seed, "provenance": provenance}
            extra = {k: v for k, v in injectable.items() if k in accepted}

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
        # injected kwargs); preserve identity for registration + discovery.
        wrapper.__name__ = func.__name__
        wrapper.__qualname__ = func.__name__
        wrapper.__doc__ = func.__doc__
        wrapper.__module__ = func.__module__
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
