"""list_tables — the Bloom tables Claude can read, or one table's columns."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from bloom_mcp.contract import as_mcp_tool
from bloom_mcp.data_access import table_access
from bloom_mcp.tools import _ports

from ._common import DECLARED_ERRORS, readable_columns


class ListTablesParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contains: str | None = Field(
        default=None, description="Only tables whose name contains this."
    )
    table: str | None = Field(
        default=None, description="Describe this table: its columns."
    )


class ListTablesResult(BaseModel):
    tables: list[str]
    columns: list[str] | None = Field(
        description="The described table's columns, if one was asked for."
    )


@as_mcp_tool(
    input_model=ListTablesParams, output_model=ListTablesResult, errors=DECLARED_ERRORS
)
def list_tables(params: ListTablesParams) -> ListTablesResult:
    """List the Bloom tables you can read, or pass `table` to get its columns.

    Names start with their area: cyl_ (cylinder scans), gravi_ and plates_ (plates), scrna_
    and rnaseq_ (expression), gene/genome/assemblies (genes and genomes). Use query_table
    and count_rows to read them.
    """
    if params.table:
        return ListTablesResult(
            tables=[params.table], columns=readable_columns(params.table)
        )
    names = sorted(
        t for t in _ports.table_reader().table_columns() if table_access.is_readable(t)
    )
    if params.contains:
        names = [t for t in names if params.contains.lower() in t.lower()]
    return ListTablesResult(tables=names, columns=None)
