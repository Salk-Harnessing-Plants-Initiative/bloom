"""
Base utilities and generic database tools for LangChain agent.
"""
import os
from typing import Optional
import httpx
from langchain_core.tools import tool

# PostgREST Configuration (Supabase) — uses bloom_agent key (read-only)
from config import SUPABASE_URL, BLOOM_AGENT_KEY as SUPABASE_KEY
REST_URL = f"{SUPABASE_URL}/rest/v1"


def get_headers():
    """Get headers for PostgREST requests."""
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }


# ==================== Generic Database Tools ====================

_SQL_PATTERNS = [
    'SELECT ', 'COUNT(', 'COUNT (', 'JOIN ', ' FROM ',
    'WHERE ', 'INSERT ', 'UPDATE ', 'DELETE ', 'GROUP BY',
    'ORDER BY', 'HAVING ', 'DISTINCT ', 'ALTER ', 'DROP ',
    'CREATE ', 'TRUNCATE ',
]


def _reject_sql(table: str, select: str, filters) -> str | None:
    """Return error message if any parameter contains SQL syntax, else None."""
    check = f" {table} {select} {filters} ".upper()
    if any(kw in check for kw in _SQL_PATTERNS):
        return (
            "ERROR: This tool uses PostgREST REST filters, NOT SQL. "
            "DO NOT use SELECT, COUNT(), JOIN, WHERE, or any SQL syntax.\n\n"
            "Correct usage:\n"
            "  query_database('cyl_experiments', 'id,name', {'species_id': 'eq.1'})\n"
            "  query_database('scrna_datasets', '*', {'name': 'ilike.*soybean*'})\n\n"
            "PostgREST filter syntax:\n"
            "  {'column': 'eq.value'}        → equals\n"
            "  {'name': 'ilike.*pattern*'}    → case-insensitive like\n"
            "  {'id': 'gt.5'}                → greater than\n"
            "  {'id': 'in.(1,2,3)'}          → in list"
        )
    return None


@tool
def query_database(table: str, select: str = "*", filters: Optional[dict] = None, limit: int = 100, order: Optional[str] = None) -> list:
    """
    Query a database table using PostgREST REST filters (GET-only, read-only).

    IMPORTANT: This is NOT SQL. DO NOT use SELECT, COUNT(), JOIN, WHERE, or SQL syntax.
    This tool makes HTTP GET requests to a PostgREST API using REST-style filters.

    Args:
        table: Table name (e.g., 'cyl_experiments', 'scrna_datasets', 'species')
        select: Columns to return — PostgREST syntax, NOT SQL SELECT.
                Use '*' for all, or 'id,name,species(common_name)' for specific columns with joins.
        filters: PostgREST filters as dict (NOT SQL WHERE clauses). Examples:
                 {'species_id': 'eq.1'}       → equals
                 {'name': 'ilike.*soybean*'}   → case-insensitive like
                 {'id': 'gt.5'}               → greater than
                 {'id': 'in.(1,2,3)'}         → in list
        limit: Max rows to return (default 100)
        order: Order by column, e.g., 'created_at.desc' or 'name.asc'

    Returns:
        List of matching records as dictionaries
    """
    # Guard: reject SQL syntax
    error = _reject_sql(table, select, filters)
    if error:
        return [error]

    params = {"select": select, "limit": limit}
    if filters:
        params.update(filters)
    if order:
        params["order"] = order

    response = httpx.get(f"{REST_URL}/{table}", headers=get_headers(), params=params)
    if response.status_code != 200:
        raise Exception(f"Query failed ({response.status_code}): {response.text}")
    return response.json()


@tool
def count_rows(table: str, filters: Optional[dict] = None) -> dict:
    """
    Count rows in a table (GET-only, read-only). Use this instead of SQL COUNT().

    DO NOT use SQL syntax. Use PostgREST filters for filtering.

    Args:
        table: Table name (e.g., 'cyl_scans', 'scrna_datasets')
        filters: Optional PostgREST filters as dict (e.g., {'species_id': 'eq.1'})

    Returns:
        Dict with table name and count
    """
    headers = get_headers()
    headers["Prefer"] = "count=exact"

    params = {"select": "id", "limit": 0}
    if filters:
        params.update(filters)

    response = httpx.get(f"{REST_URL}/{table}", headers=headers, params=params)
    if response.status_code != 200 and response.status_code != 206:
        raise Exception(f"Count failed ({response.status_code}): {response.text}")

    content_range = response.headers.get("content-range", "")
    if "/" in content_range:
        count = int(content_range.split("/")[1])
    else:
        count = 0

    return {"table": table, "count": count, "filters": filters}


@tool
def get_table_columns(table: str) -> dict:
    """
    Get column names for a table by inspecting one row (GET-only, read-only).

    Args:
        table: Table name to inspect (e.g., 'cyl_experiments', 'scrna_datasets')

    Returns:
        Dict with table info including available columns and a sample row
    """
    response = httpx.get(
        f"{REST_URL}/{table}",
        headers=get_headers(),
        params={"limit": 1}
    )
    if response.status_code != 200:
        raise Exception(f"Failed to inspect table: {response.text}")

    data = response.json()
    if data:
        return {"table": table, "columns": list(data[0].keys()), "sample": data[0]}
    return {"table": table, "columns": [], "sample": None, "note": "Table is empty"}


@tool
def list_tables() -> list:
    """
    List all available tables in the database that can be queried.

    Returns:
        List of table names with descriptions
    """
    return [
        # scRNA-seq data
        {"table": "scrna_datasets", "description": "Single-cell RNA-seq datasets"},
        {"table": "scrna_cells", "description": "Cells in scRNA-seq datasets"},
        {"table": "scrna_genes", "description": "Genes in scRNA-seq datasets"},
        {"table": "scrna_counts", "description": "Gene expression counts per cell"},
        {"table": "scrna_de", "description": "Differential expression results"},
        # Species
        {"table": "species", "description": "Plant species information"},
        {"table": "accessions", "description": "Plant accessions/varieties"},
        # Cylinder phenotyping
        {"table": "cyl_experiments", "description": "Cylinder phenotyping experiments"},
        {"table": "cyl_waves", "description": "Planting waves within experiments"},
        {"table": "cyl_plants", "description": "Individual plants in experiments"},
        {"table": "cyl_scans", "description": "3D scans of plants"},
        {"table": "cyl_images", "description": "Images from plant scans"},
        {"table": "cyl_scan_traits", "description": "Measured traits from scans"},
        {"table": "cyl_scanners", "description": "Scanner devices"},
        # Genes
        {"table": "genes", "description": "Gene information"},
        {"table": "gene_candidates", "description": "Candidate genes for research"},
        # Other
        {"table": "phenotypers", "description": "Phenotyping devices/systems"},
        {"table": "people", "description": "Scientists and team members"},
    ]


@tool
def list_species_tool() -> list:
    """List all available species in the database."""
    response = httpx.get(
        f"{REST_URL}/species",
        headers=get_headers(),
        params={"select": "id,common_name,genus,species"}
    )
    if response.status_code != 200:
        raise Exception(f"Failed to list species: {response.text}")
    return response.json()


@tool
def hello(name: str) -> str:
    """Greets the user with their name."""
    return f"Hello {name}"


# Export all generic tools
generic_tools = [
    query_database,
    count_rows,
    get_table_columns,
    list_tables,
    list_species_tool,
    hello,
]
