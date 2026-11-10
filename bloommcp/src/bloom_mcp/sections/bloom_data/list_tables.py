"""list_tables — the Bloom tables Claude can read, or one table's columns."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from bloom_mcp.contract import as_mcp_tool
from bloom_mcp.data_access import page_links, table_access, table_notes
from bloom_mcp.tools import _ports

from ._common import logs_call, readable_column_details, reports_read_errors


class ListTablesParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contains: str | None = Field(
        default=None, description="Only tables whose name contains this."
    )
    table: str | None = Field(
        default=None, description="Describe this table: its columns."
    )


class ColumnInfo(BaseModel):
    name: str
    type: str = Field(description="SQL type, e.g. bigint, text, date, uuid, jsonb.")
    references: str | None = Field(
        default=None,
        description="table.column this column points to; join on it. Only readable tables.",
    )


class ListTablesResult(BaseModel):
    tables: list[str]
    notes: dict[str, str] = Field(
        description="Which table to use, for the listed tables that have a note."
    )
    columns: list[ColumnInfo] | None = Field(
        description="The described table's columns, if one was asked for."
    )


def _notes(tables: list[str]) -> dict[str, str]:
    """Each table's note, then how to link its rows to Bloom's website."""
    notes = table_notes.table_notes(tables)
    joined = {
        t: " ".join(filter(None, (notes.get(t), page_links.link_note(t))))
        for t in tables
    }
    return {t: note for t, note in joined.items() if note}


@as_mcp_tool(input_model=ListTablesParams, output_model=ListTablesResult)
@logs_call
@reports_read_errors
def list_tables(params: ListTablesParams) -> ListTablesResult:
    """List the Bloom tables you can read, or pass `table` to get its columns.

    Names start with their area: cyl_ (cylinder scans), gravi_ and plates_ (plates), scrna_
    and rnaseq_ (expression), gene/genome/assemblies (genes and genomes). Use query_table
    and count_rows to read them. Follow `notes`: they say which table gives the answer the
    website shows, and which views already join the tables you need. Otherwise join
    tables by following each column's `references`. Traits: these tables give the figures
    Bloom's pages show; for analysis (QC, heritability, PCA, clustering, correlations,
    statistical comparisons, plots) use the sleap_roots_* tools; if unclear, ask the user.

    Deleted experiments, species, scRNA datasets and gene candidates are left out of their
    own tables, but rows that belong to them (waves, plants, scans, traits, cells) are not:
    keep only rows whose parent id is still in the parent table. For cylinder experiments,
    also skip deleted = true, which older deletions use.
    """
    if params.table:
        columns = [
            ColumnInfo(name=c.name, type=c.type, references=_readable(c.references))
            for c in readable_column_details(params.table)
        ]
        return ListTablesResult(
            tables=[params.table],
            notes=_notes([params.table]),
            columns=columns,
        )
    names = sorted(
        t for t in _ports.table_reader().table_columns() if table_access.is_readable(t)
    )
    if params.contains:
        names = [t for t in names if params.contains.lower() in t.lower()]
    return ListTablesResult(tables=names, notes=_notes(names), columns=None)


def _readable(reference: str | None) -> str | None:
    """A foreign key, kept only when Claude can read the table it points to."""
    if reference is None or not table_access.is_readable(reference.split(".")[0]):
        return None
    return reference
