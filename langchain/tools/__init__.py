"""
LangChain tools for the Bloom platform.

This module organizes tools into categories:
- base: Generic database tools (query_database, count_rows, etc.)
- scrna_tools: Single-cell RNA-seq data tools
- cyl_tools: Cylinder phenotyping tools
"""

from .base import (
    generic_tools,
    query_database,
    count_rows,
    get_table_columns,
    list_tables,
    list_species_tool,
    hello,
    # Also export utilities for use in other modules
    REST_URL,
    get_headers,
)

from .scrna_tools import (
    scrna_tools,
    get_all_datasets_tool,
    get_dataset_by_id_tool,
    search_datasets_by_species_tool,
    get_clusters_by_dataset_tool,
    get_genes_by_dataset_tool,
    search_gene_tool,
    get_differential_expression_files_tool,
    get_top_de_genes_tool,
    get_cells_by_cluster_tool,
    get_gene_counts_tool,
    get_gene_expression_by_cluster_tool,
)

from .cyl_tools import (
    cyl_tools,
    list_experiments_tool,
    get_experiment_by_id_tool,
    list_waves_by_experiment_tool,
    list_plants_tool,
    get_plant_by_qr_tool,
    list_scans_tool,
    get_scan_tool,
    get_scan_traits_tool,
    list_scanners_tool,
    list_phenotypers_tool,
    get_plant_scan_history_tool,
)

# Combined tool lists for convenience
all_tools = generic_tools + scrna_tools + cyl_tools

__all__ = [
    # Tool lists
    "all_tools",
    "generic_tools",
    "scrna_tools",
    "cyl_tools",
    # Generic tools
    "query_database",
    "count_rows",
    "get_table_columns",
    "list_tables",
    "list_species_tool",
    "hello",
    # scRNA tools
    "get_all_datasets_tool",
    "get_dataset_by_id_tool",
    "search_datasets_by_species_tool",
    "get_clusters_by_dataset_tool",
    "get_genes_by_dataset_tool",
    "search_gene_tool",
    "get_differential_expression_files_tool",
    "get_top_de_genes_tool",
    "get_cells_by_cluster_tool",
    "get_gene_counts_tool",
    "get_gene_expression_by_cluster_tool",
    # Cylinder tools
    "list_experiments_tool",
    "get_experiment_by_id_tool",
    "list_waves_by_experiment_tool",
    "list_plants_tool",
    "get_plant_by_qr_tool",
    "list_scans_tool",
    "get_scan_tool",
    "get_scan_traits_tool",
    "list_scanners_tool",
    "list_phenotypers_tool",
    "get_plant_scan_history_tool",
    # Utilities
    "REST_URL",
    "get_headers",
]
