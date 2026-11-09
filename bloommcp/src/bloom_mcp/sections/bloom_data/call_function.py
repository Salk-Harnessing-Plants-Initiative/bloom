"""call_function — call one of Bloom's read-only database functions, capped and paged."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from bloom_mcp.contract import as_mcp_tool
from bloom_mcp.data_access import function_access
from bloom_mcp.data_access.function_access import MAX_SEARCH_ROWS, ReadableFunction
from bloom_mcp.data_access.table_ports import Function
from bloom_mcp.tools import _ports

from ._common import (
    LIST_FUNCTIONS_REMEDY,
    MAX_IN_VALUES,
    fix_request,
    logs_call,
    plain_value,
    reports_call_errors,
)
from ._page import DEFAULT_LIMIT, MAX_LIMIT, MAX_OFFSET, fit_page


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
    search_limit_reached: bool = Field(
        default=False,
        description=f"True if the search stopped at its {MAX_SEARCH_ROWS:,}-match "
        "limit: there may be more matches, so narrow the search.",
    )


def as_float4(value: Any) -> Any:
    """The shortest decimal that reads back as the same 4-byte float, as the website's
    CSV writes it: a stored 12.3 widened to 12.3000001907349 comes back as 12.3."""
    if isinstance(value, float) and math.isfinite(value):
        return float(str(np.float32(value)))
    return value


def _served(name: str) -> Function:
    if not function_access.is_callable(name):
        raise fix_request(
            f"'{name}' is not a function Bloom's data tools can call.",
            LIST_FUNCTIONS_REMEDY,
        )
    reader = _ports.table_reader()
    served = reader.functions().get(name)
    if served is None and reader.refresh_columns():
        served = reader.functions().get(name)
    if served is None:
        raise fix_request(
            f"'{name}' is not a function Bloom's data tools can call.",
            LIST_FUNCTIONS_REMEDY,
        )
    return served


def _check_args(
    function: Function, entry: ReadableFunction, args: dict[str, Any]
) -> None:
    names = {p.name for p in function.params} - {entry.page_input}
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
            LIST_FUNCTIONS_REMEDY,
        )
    for name, value in args.items():
        if isinstance(value, list) and len(value) > MAX_IN_VALUES:
            raise fix_request(
                f"'{function.name}' input '{name}' has {len(value)} values; the most is "
                f"{MAX_IN_VALUES}.",
                f"Split the list into calls of up to {MAX_IN_VALUES} values.",
            )
        if not plain_value(value, allow_null=True):
            raise fix_request(
                f"'{function.name}' input '{name}' takes text, a number, true/false, null, "
                "or a list of those.",
                LIST_FUNCTIONS_REMEDY,
            )
    count = args.get(entry.count_input) if entry.count_input else None
    if count is not None and (
        not isinstance(count, int)
        or isinstance(count, bool)
        or not 1 <= count <= MAX_SEARCH_ROWS
    ):
        raise fix_request(
            f"'{function.name}' input '{entry.count_input}' takes a whole number from 1 "
            f"to {MAX_SEARCH_ROWS:,}.",
            f"Ask for at most {MAX_SEARCH_ROWS:,}.",
        )


@as_mcp_tool(input_model=CallFunctionParams, output_model=CallFunctionResult)
@logs_call
@reports_call_errors
def call_function(params: CallFunctionParams) -> CallFunctionResult:
    """Call one of Bloom's read-only database functions (from list_functions) with its inputs.

    Returns at most 500 rows per call, fewer when the rows are large; when has_more is
    true, call again with offset=next_offset. A search returns at most 1,000 matches in
    all; search_limit_reached says when it stopped there. Trait values come from the latest
    pipeline run unless source_id_, run_id_ or recipe_key_ pins one.

    Text or JSON values over 2000 characters are cut and end in "…[cut]"; truncated_values
    says when. Number values can be NaN or Infinity, which arrive as the text "NaN" or
    "Infinity".
    """
    function = _served(params.function)
    entry = function_access.READABLE_FUNCTIONS[params.function]
    _check_args(function, entry, params.args)
    args = dict(params.args)
    # A search returns no more than its limit input, so ask for every row up to this page.
    wanted = min(params.offset + params.limit + 1, MAX_SEARCH_ROWS)
    if entry.page_input:
        args[entry.page_input] = wanted
    rows = _ports.table_reader().call(
        params.function, args, list(entry.order), params.limit + 1, params.offset
    )
    for row in rows:
        for column in entry.float4_columns:
            row[column] = as_float4(row.get(column))
    page = fit_page(rows, params.limit)
    limit_reached = (
        entry.page_input is not None
        and wanted == MAX_SEARCH_ROWS
        and params.offset + len(rows) >= MAX_SEARCH_ROWS
    )
    return CallFunctionResult(
        function=params.function,
        rows=page.rows,
        returned=len(page.rows),
        offset=params.offset,
        has_more=page.has_more,
        next_offset=page.next_offset(params.offset),
        truncated_values=page.truncated_values,
        cut_short=page.cut_short,
        search_limit_reached=limit_reached,
    )
