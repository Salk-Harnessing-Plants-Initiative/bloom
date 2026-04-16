"""
Context tools for the LangChain agent.

Provides on-demand schema, rules, and tool discovery so the system prompt
stays minimal and tokens are only spent when needed.
"""
from langchain_core.tools import tool


# ==================== Context Payloads ====================

CONTEXT_SCRNA = """## scRNA-seq Data (Supabase/PostgREST)

### Tables
- scrna_datasets: id, name, species_id, strain, assembly, annotation
- scrna_cells: id, dataset_id, cell_number, barcode, x, y, cluster_id, replicate
- scrna_genes: id, dataset_id, gene_number, gene_name
- scrna_counts: cell_id, gene_id, count
- scrna_de: id, dataset_id, file_path, cluster_id

### Tools
- get_all_datasets_tool: List all scRNA datasets
- get_dataset_by_id_tool: Get dataset details
- search_datasets_by_species_tool: Find datasets by species
- get_clusters_by_dataset_tool: Get clusters and cell counts
- get_genes_by_dataset_tool: List genes in a dataset
- search_gene_tool: Search genes by name
- get_differential_expression_files_tool: Get DE result files
- get_top_de_genes_tool: Top differentially expressed genes per cluster
- get_cells_by_cluster_tool: Cells with coordinates per cluster
- get_gene_counts_tool: Expression counts per cell
- get_gene_expression_by_cluster_tool: Expression summary per cluster

### UI Links
- Expression Explorer: {frontend_url}/app/expression/{{species_id}}/{{dataset_id}}
"""

CONTEXT_CYL = """## Cylinder Phenotyping Data (Supabase/PostgREST)

### Tables
- cyl_experiments: id, name, scientist_id, species_id, created_at
- cyl_waves: id, experiment_id, name, planting_date
- cyl_plants: id, experiment_id, wave_id, qr_code, accession_id
- cyl_scans: id, plant_id, date_scanned, scanner_id
- cyl_images: id, scan_id, path, angle
- cyl_scan_traits: id, scan_id, trait_name, value
- cyl_scanners: id, name, location

### Tools
- list_experiments_tool: List phenotyping experiments
- get_experiment_by_id_tool: Get experiment details
- list_waves_by_experiment_tool: Planting waves per experiment
- list_plants_tool: Plants with accessions
- get_plant_by_qr_tool: Look up plant by QR code
- list_scans_tool / get_scan_tool: Plant scan data
- get_scan_traits_tool: Measured traits for a scan
- get_plant_scan_history_tool: Full scan history for a plant
- get_plant_growth_timeline_tool: Chronological growth data
- get_trait_growth_stats_tool: Growth statistics for a trait
- compare_waves_trait_tool: Compare trait across waves
- get_experiment_trait_stats_tool: Experiment-wide trait statistics

### UI Links
- Greenhouse: {frontend_url}/app/greenhouse
- Phenotypes: {frontend_url}/app/phenotypes
- Plant Viewer: {frontend_url}/app/greenhouse/{{experiment_id}}/plant/{{plant_id}}
"""

CONTEXT_GENERIC = """## Generic Database Tools (Supabase/PostgREST)

### Tools
- query_database: Query any table with REST filters (NOT SQL)
- count_rows: Count rows in a table
- get_table_columns: Inspect table schema
- list_tables: List available tables
- list_species_tool: List all species

### Query Syntax (PostgREST filters, NOT SQL)
- Equality: {{'column': 'eq.value'}}
- Search: {{'name': 'ilike.*pattern*'}}
- Greater than: {{'id': 'gt.5'}}
- In list: {{'id': 'in.(1,2,3)'}}
- Nested joins: 'id,name,species(common_name)'
"""

CONTEXT_MCP = """## CSV Experiment Files (MCP Tools)

Files like cylinder_alfalfa_gwas_wave2, turface_rice_treatment_exp1 are CSV files
on the filesystem — NOT database tables. Never use query_database for these.

### Tools
- list_available_experiments: List CSV experiment files
- load_experiment_data: Load a CSV experiment file
- inspect_data_quality: Check data quality of an experiment
"""

# Map tool_set → relevant context sections
CONTEXT_MAP = {
    "scrna": [CONTEXT_SCRNA, CONTEXT_GENERIC],
    "cyl": [CONTEXT_CYL, CONTEXT_GENERIC],
    "generic": [CONTEXT_GENERIC],
    "all": [CONTEXT_SCRNA, CONTEXT_CYL, CONTEXT_GENERIC, CONTEXT_MCP],
}


# ==================== Tools ====================


@tool
def get_agent_context(tool_set: str = "all") -> str:
    """Get schema, rules, and available tools for the current data context.

    Call this at the start of a conversation to learn what data sources,
    tables, and tools are available. You only need to call this once.

    Args:
        tool_set: The active tool set — "scrna", "cyl", "generic", or "all"

    Returns:
        Schema details, available tools, and UI links for the active tool set.
    """
    from config import FRONTEND_URL

    sections = CONTEXT_MAP.get(tool_set, CONTEXT_MAP["all"])
    context = "\n".join(sections)
    context = context.format(frontend_url=FRONTEND_URL)

    return (
        "# Agent Context\n\n"
        "You have READ-ONLY access. You cannot create, update, or delete data.\n\n"
        f"Active tool set: **{tool_set}**\n\n"
        f"{context}"
    )


@tool
def list_available_tools() -> list[dict]:
    """List all tools currently available to you with short descriptions.

    Call this when you're unsure which tool to use for a task.

    Returns:
        List of tool names and descriptions.
    """
    from tools import all_tools

    return [
        {"name": t.name, "description": t.description.split("\n")[0].strip()}
        for t in all_tools
    ]


# ==================== Tool List ====================

context_tools = [get_agent_context, list_available_tools]
