"""count_rows — count a table's rows: exact when cheap, a labelled estimate when not."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from bloom_mcp.contract import as_mcp_tool
from bloom_mcp.data_access.table_access import LARGE_TABLE_ROWS
from bloom_mcp.data_access.table_ports import QueryTimedOut
from bloom_mcp.tools import _ports

from ._common import (
    DECLARED_ERRORS,
    FilterParam,
    check_columns,
    readable_columns,
    to_filters,
    too_slow,
)

ESTIMATE_NOTE = (
    "An estimate from the database's planner: an exact count of a table this large, "
    "or one not yet measured, would be too slow."
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
    """Count the rows of one of Bloom's tables, optionally filtered.

    Small tables are counted exactly. Large ones (over 200,000 rows, such as cylinder scan
    traits) get the database's estimate, marked approximate; say so when reporting it.
    """
    allowed = readable_columns(params.table)
    check_columns(params.table, [f.column for f in params.filters], allowed)
    filters = to_filters(params.filters)
    reader = _ports.table_reader()
    column = "id" if "id" in allowed else allowed[0]

    estimate = reader.row_estimates([params.table]).get(params.table)
    if estimate is not None and estimate < LARGE_TABLE_ROWS:
        try:
            exact = reader.count(params.table, column, filters, exact=True)
            return CountRowsResult(
                table=params.table, count=exact, approximate=False, note="Exact count."
            )
        except QueryTimedOut:
            pass
    try:
        planned = reader.count(params.table, column, filters, exact=False)
    except QueryTimedOut:
        raise too_slow() from None
    return CountRowsResult(
        table=params.table, count=planned, approximate=True, note=ESTIMATE_NOTE
    )
