"""call_function — call one of Bloom's read-only database functions, capped and paged."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from bloom_mcp.contract import as_mcp_tool
from bloom_mcp.data_access import function_access
from bloom_mcp.data_access.table_ports import Function
from bloom_mcp.tools import _ports

from ._common import MAX_IN_VALUES, fix_request, logs_call, reports_read_errors
from ._page import DEFAULT_LIMIT, MAX_LIMIT, MAX_OFFSET, fit_page

_SCALARS = (str, int, float, bool)
_LIST_FUNCTIONS = "Call list_functions to see the functions and their inputs."


class CallFunctionParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    function: str = Field(description="Function name, from list_functions.")
    args: dict[str, Any] = Field(
        default_factory=dict,
        description="Inputs by name, as list_functions gives them; a list for [] types.",
    )
    limit: int = Field(
        default=DEFAULT_LIMIT,
        ge=1,
        le=MAX_LIMIT,
        description=f"Rows per page (max {MAX_LIMIT}).",
    )
    offset: int = Field(
        default=0,
        ge=0,
        le=MAX_OFFSET,
        description=f"Rows to skip (max {MAX_OFFSET:,}); use next_offset for the next page.",
    )


class CallFunctionResult(BaseModel):
    function: str
    rows: list[dict[str, Any]]
    returned: int
    offset: int
    has_more: bool
    next_offset: int | None
    truncated_values: bool = Field(description="True if some long values were cut.")
    cut_short: bool = Field(
        description="True if the page stopped before limit to keep the reply small; "
        "continue with next_offset."
    )


def _served(name: str) -> Function:
    if not function_access.is_callable(name):
        raise fix_request(
            f"'{name}' is not a function Bloom's data tools can call.", _LIST_FUNCTIONS
        )
    reader = _ports.table_reader()
    served = reader.functions().get(name)
    if served is None and reader.refresh_columns():
        served = reader.functions().get(name)
    if served is None:
        raise fix_request(
            f"'{name}' is not a function Bloom's data tools can call.", _LIST_FUNCTIONS
        )
    return served


def _check_args(function: Function, args: dict[str, Any]) -> None:
    names = {p.name for p in function.params}
    unknown = sorted(set(args) - names)
    missing = sorted(
        p.name for p in function.params if p.required and p.name not in args
    )
    if unknown or missing:
        problems = [
            f"no input {', '.join(unknown)}" if unknown else "",
            f"missing {', '.join(missing)}" if missing else "",
        ]
        raise fix_request(
            f"'{function.name}': {'; '.join(p for p in problems if p)}.",
            _LIST_FUNCTIONS,
        )
    for name, value in args.items():
        values = value if isinstance(value, list) else [value]
        if isinstance(value, list) and len(value) > MAX_IN_VALUES:
            raise fix_request(
                f"'{function.name}' input '{name}' has {len(value)} values; the most is "
                f"{MAX_IN_VALUES}.",
                f"Split the list into calls of up to {MAX_IN_VALUES} values.",
            )
        if not all(v is None or isinstance(v, _SCALARS) for v in values):
            raise fix_request(
                f"'{function.name}' input '{name}' takes text, a number, true/false, null, "
                "or a list of those.",
                _LIST_FUNCTIONS,
            )


@as_mcp_tool(input_model=CallFunctionParams, output_model=CallFunctionResult)
@logs_call
@reports_read_errors
def call_function(params: CallFunctionParams) -> CallFunctionResult:
    """Call one of Bloom's read-only database functions (from list_functions) with its inputs.

    Returns at most 500 rows per call, fewer when the rows are large; when has_more is
    true, call again with offset=next_offset. Trait values come from the latest pipeline
    run unless an input pins a source or run.
    """
    function = _served(params.function)
    _check_args(function, params.args)
    order = list(function_access.READABLE_FUNCTIONS[params.function].order)
    rows = _ports.table_reader().call(
        params.function, params.args, order, params.limit + 1, params.offset
    )
    page = fit_page(rows, params.limit)
    return CallFunctionResult(
        function=params.function,
        rows=page.rows,
        returned=len(page.rows),
        offset=params.offset,
        has_more=page.has_more,
        next_offset=page.next_offset(params.offset),
        truncated_values=page.truncated_values,
        cut_short=page.cut_short,
    )
