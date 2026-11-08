"""query_table — read rows from one of Bloom's tables, filtered, capped and paged."""

from __future__ import annotations

import json
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

DEFAULT_LIMIT = 50
MAX_LIMIT = 500
# Longer text or JSON values are cut, so one row can't fill the reply.
MAX_CELL_CHARS = 2000
TRUNCATED = "…[cut]"
# Skipping rows makes Postgres walk them all; past this, filter on the key instead.
MAX_OFFSET = 10_000
# A page stops early once its rows reach this many characters, so one reply stays small.
MAX_REPLY_CHARS = 100_000


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


def _cut(value: Any) -> tuple[Any, bool]:
    if isinstance(value, (dict, list)):
        text = json.dumps(value, default=str)
        return (
            (value, False)
            if len(text) <= MAX_CELL_CHARS
            else (text[:MAX_CELL_CHARS] + TRUNCATED, True)
        )
    if isinstance(value, str) and len(value) > MAX_CELL_CHARS:
        return value[:MAX_CELL_CHARS] + TRUNCATED, True
    return value, False


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
    cyl_scan_traits_latest.
    """
    if params.offset > MAX_OFFSET:
        raise fix_request(
            f"offset can't be over {MAX_OFFSET:,}: skipping that many rows is slow for "
            "the database.",
            "Filter instead: on the column you sort by, ask for values after the last "
            "one you read (gt, or lt when descending), with offset 0.",
        )
    allowed = readable_columns(params.table)
    columns = params.columns or allowed
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

    has_more = len(rows) > params.limit
    rows = rows[: params.limit]
    redacted = table_access.unfilterable_columns(params.table)
    truncated = cut_short = False
    page: list[dict[str, Any]] = []
    size = 0
    for row in rows:
        for key, value in row.items():
            if key in redacted:
                value = table_access.redact_emails(value)
            row[key], cut = _cut(value)
            truncated = truncated or cut
        size += len(json.dumps(row, default=str))
        if page and size > MAX_REPLY_CHARS:
            has_more = cut_short = True
            break
        page.append(row)
    rows = page
    return QueryTableResult(
        table=params.table,
        columns=columns,
        rows=rows,
        returned=len(rows),
        offset=params.offset,
        has_more=has_more,
        next_offset=params.offset + len(rows) if has_more else None,
        truncated_values=truncated,
        cut_short=cut_short,
    )
