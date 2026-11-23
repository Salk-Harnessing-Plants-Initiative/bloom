"""bloom_data section — read any of Bloom's tables through bloom_agent.

General tools over allow-lists (``bloom_mcp.data_access.table_access`` and
``function_access``): list the tables, read rows (capped and paged), count rows (exact, or
a labelled estimate when too many match), list and call the read-only functions, look
up one gene across the gene, HPI and expression tables, read one scRNA dataset's markers
and DE, list one gene's expressing cells, and open stored files.
Tools appear namespaced ``bloom_data_<name>`` on ``/mcp`` and at ``/bloom_data/mcp``.
"""

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from bloom_mcp.auth import auth_provider
from bloom_mcp.contract import register

from . import (
    call_function,
    count_rows,
    gene_cells,
    gene_expression,
    gene_lookup,
    list_functions,
    list_tables,
    query_table,
    read_file,
    scrna_de,
)

section = FastMCP("bloom_data", auth=auth_provider)

# Reads only: clients may run these without asking the user to confirm a change.
READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)

register(
    section,
    list_tables.list_tables,
    query_table.query_table,
    count_rows.count_rows,
    list_functions.list_functions,
    call_function.call_function,
    gene_lookup.gene_lookup,
    gene_expression.gene_expression,
    read_file.read_file,
    scrna_de.scrna_de,
    gene_cells.scrna_gene_cells,
    annotations=READ_ONLY,
)
