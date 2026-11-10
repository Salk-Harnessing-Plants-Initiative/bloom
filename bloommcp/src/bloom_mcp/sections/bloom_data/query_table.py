"""query_table — read rows from one of Bloom's tables, filtered, capped and paged."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from bloom_mcp.contract import as_mcp_tool
from bloom_mcp.data_access import table_access
from bloom_mcp.data_access.table_ports import Sort
from bloom_mcp.tools import _ports

from ._common import (
    FilterParam,
    check_columns,
    check_filterable,
    fix_request,
    logs_call,
    readable_columns,
    reports_read_errors,
    to_filters,
)
from ._page import (
    DEFAULT_LIMIT,
    MAX_CELL_CHARS,
    MAX_LIMIT,
    MAX_OFFSET,
    MAX_REPLY_CHARS,
    TRUNCATED,
    fit_page,
)

__all__ = ["MAX_CELL_CHARS", "MAX_REPLY_CHARS", "TRUNCATED", "query_table"]


class QueryTableParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: str = Field(description="Table name, from list_tables.")
    columns: list[str] | None = Field(
        default=None,
        description="Columns to return; all readable columns when omitted.",
    )
    filters: list[FilterParam] = Field(
        default_factory=list, description="Conditions, all must hold."
    )
    order_by: str | None = Field(
        default=None,
        description="Column to sort by; rows are always also sorted by the table's key.",
    )
    descending: bool = Field(
        default=False, description="Largest first (order_by, or the key if none)."
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
        description=f"Rows to skip (max {MAX_OFFSET:,}); use next_offset for the next page.",
    )


class QueryTableResult(BaseModel):
    table: str
    columns: list[str]
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


def _order(table: str, order_by: str | None, descending: bool) -> list[Sort]:
    """Claude's column first, then the table's unique key, so pages never overlap."""
    key = table_access.paging_key(table)
    if order_by is None:
        return [Sort(c, descending) for c in key]
    return [Sort(order_by, descending)] + [Sort(c) for c in key if c != order_by]


@as_mcp_tool(input_model=QueryTableParams, output_model=QueryTableResult)
@logs_call
@reports_read_errors
def query_table(params: QueryTableParams) -> QueryTableResult:
    """Read rows from one of Bloom's tables (cylinder scans, plates, expression, genes, ...).

    Call list_tables first for table and column names. Returns at most 500 rows per call,
    fewer when the rows are large; when has_more is true, call again with
    offset=next_offset. On large tables, filter on an
    id column (experiment, species, scan) or the query may time out. For trait values, read
    cyl_scan_traits_latest; to analyse them, use the sleap_roots_* tools, which run the
    sleap-roots pipeline's analysis, and say so.

    Text or JSON values over 2000 characters are cut and end in "…[cut]" (a cut JSON value
    comes back as text); truncated_values says when. Number columns can hold NaN or
    Infinity, which arrive as the text "NaN" or "Infinity".
    """
    if params.offset > MAX_OFFSET:
        raise fix_request(
            f"offset can't be over {MAX_OFFSET:,}: skipping that many rows is slow for "
            "the database.",
            "Filter instead, with offset 0: on the first column you sort by, ask for values "
            "from the last one you read onward (gte, or lte when descending), then drop the "
            "rows you already have.",
        )
    allowed = readable_columns(params.table)
    columns = params.columns or allowed
    if not columns:
        # An empty select would make PostgREST return every column, hidden ones too.
        raise fix_request(
            f"'{params.table}' has no columns Bloom's data tools can read."
        )
    check_columns(params.table, columns, allowed)
    check_columns(params.table, [f.column for f in params.filters], allowed)
    chosen = [params.order_by] if params.order_by else []
    check_columns(params.table, chosen, allowed)
    check_filterable(params.table, [f.column for f in params.filters] + chosen)
    filters = to_filters(params.filters)
    order = _order(params.table, params.order_by, params.descending)

    rows = _ports.table_reader().select(
        params.table, columns, filters, order, params.limit + 1, params.offset
    )

    page = fit_page(rows, params.limit, table_access.redacted_columns(params.table))
    return QueryTableResult(
        table=params.table,
        columns=columns,
        rows=page.rows,
        returned=len(page.rows),
        offset=params.offset,
        has_more=page.has_more,
        next_offset=page.next_offset(params.offset),
        truncated_values=page.truncated_values,
        cut_short=page.cut_short,
    )
