"""count_rows — count a table's rows: exact unless too many match, then a labelled estimate."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from bloom_mcp.contract import as_mcp_tool
from bloom_mcp.data_access import table_access
from bloom_mcp.data_access.table_access import LARGE_COUNT_ROWS
from bloom_mcp.data_access.table_ports import QueryTimedOut
from bloom_mcp.tools import _ports

from ._common import (
    DECLARED_ERRORS,
    FilterParam,
    check_columns,
    check_filterable,
    readable_columns,
    to_filters,
    too_slow,
)

EXACT_NOTE = "Exact count."
TOO_MANY_NOTE = (
    "An estimate from the database's planner: too many rows match to count exactly in "
    "time. Tell the user this number is an estimate."
)
TIMED_OUT_NOTE = (
    "An estimate from the database's planner: the exact count took too long. Tell the user "
    "this number is an estimate; narrower filters give an exact count."
)


class CountRowsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: str = Field(description="Table name, from list_tables.")
    filters: list[FilterParam] = Field(
        default_factory=list, description="Conditions, all must hold."
    )


class CountRowsResult(BaseModel):
    table: str
    count: int | None = Field(
        description="Row count; null if even the estimate is unavailable."
    )
    approximate: bool
    note: str


@as_mcp_tool(
    input_model=CountRowsParams, output_model=CountRowsResult, errors=DECLARED_ERRORS
)
def count_rows(params: CountRowsParams) -> CountRowsResult:
    """Count the rows of one of Bloom's tables or views, optionally filtered.

    Counts are exact unless a very large number of rows match (a whole cylinder-traits
    table, say); then the result is the database's estimate with approximate=true. When
    approximate is true, tell the user the number is an estimate.
    """
    allowed = readable_columns(params.table)
    check_columns(params.table, [f.column for f in params.filters], allowed)
    check_filterable(params.table, [f.column for f in params.filters])
    filters = to_filters(params.filters)
    reader = _ports.table_reader()
    column = table_access.paging_key(params.table)[0]

    # The planner's guess is instant; it decides whether an exact count is affordable.
    try:
        planned = reader.count(params.table, column, filters, exact=False)
    except QueryTimedOut:
        raise too_slow() from None
    if planned is not None and planned >= LARGE_COUNT_ROWS:
        return _estimate(params.table, planned, TOO_MANY_NOTE)
    try:
        exact = reader.count(params.table, column, filters, exact=True)
    except QueryTimedOut:
        return _estimate(params.table, planned, TIMED_OUT_NOTE)
    return CountRowsResult(
        table=params.table, count=exact, approximate=False, note=EXACT_NOTE
    )


def _estimate(table: str, count: int | None, note: str) -> CountRowsResult:
    return CountRowsResult(table=table, count=count, approximate=True, note=note)
