"""A fake scRNA dataset for the expression and per-cell tools: cells, catalog and counts files."""

from __future__ import annotations

from bloom_mcp.data_access.table_ports import Function, Param

SITE = "https://bloom.example"
# Cell 0-1 cortex, 2-3 xylem, 4 in a cluster with no catalog row and nothing recorded.
CELLS = [
    {
        "x": 0.0,
        "y": 0.0,
        "cluster_ordinal": 0,
        "genotype": "Col-0",
        "replicate": "rep1",
        "facets": {"transgene_pos": "False"},
    },
    {
        "x": 1.0,
        "y": 0.0,
        "cluster_ordinal": 0,
        "genotype": "pFACT",
        "replicate": "rep2",
        "facets": {"transgene_pos": "True"},
    },
    {
        "x": 0.0,
        "y": 1.0,
        "cluster_ordinal": 1,
        "genotype": "pFACT",
        "replicate": "rep1",
        "facets": None,
    },
    {
        "x": 1.0,
        "y": 1.0,
        "cluster_ordinal": 1,
        "genotype": "Col-0",
        "replicate": "rep2",
        "facets": None,
    },
    {
        "x": 2.0,
        "y": 2.0,
        "cluster_ordinal": 255,
        "genotype": None,
        "replicate": None,
        "facets": None,
    },
]
TABLES = {
    "scrna_datasets": [
        {
            "id": 3,
            "name": "MYB41 root",
            "species_id": 16,
            "kind": "full",
            "deleted_at": None,
        },
        {
            "id": 4,
            "name": "Atlas",
            "species_id": 16,
            "kind": "reference",
            "deleted_at": None,
        },
        {
            "id": 5,
            "name": "Gone",
            "species_id": 16,
            "kind": "full",
            "deleted_at": "2026-01-01",
        },
    ],
    "scrna_clusters": [
        {
            "id": 1,
            "dataset_id": 3,
            "cluster_id": "C",
            "name": "Cortex",
            "source": None,
            "ordinal": 0,
            "color": "#1f77b4",
        },
        {
            "id": 2,
            "dataset_id": 3,
            "cluster_id": "X",
            "name": None,
            "source": None,
            "ordinal": 1,
            "color": None,
        },
    ],
    "scrna_genes": [
        {"id": 10, "dataset_id": 3, "gene_name": "AT4G28110"},
        {"id": 11, "dataset_id": 3, "gene_name": "AT1G01010"},
        {"id": 12, "dataset_id": 3, "gene_name": "AT2G02020"},
        {"id": 13, "dataset_id": 3, "gene_name": "AT3G03030"},
        {"id": 14, "dataset_id": 3, "gene_name": "Glyma.01G000100"},
        # Listed by symbol: PDF1.2 is its own gene, not PDF1.
        {"id": 15, "dataset_id": 3, "gene_name": "PDF1"},
        {"id": 16, "dataset_id": 3, "gene_name": "PDF1.2"},
        {"id": 17, "dataset_id": 3, "gene_name": "AT5G05050"},
        # Another dataset's gene, never read for dataset 3.
        {"id": 20, "dataset_id": 4, "gene_name": "AT9G09090"},
    ],
    "scrna_counts": [
        {
            "id": 1,
            "dataset_id": 3,
            "gene_id": 10,
            "counts_object_path": "counts/MYB41_root_3_/AT4G28110.json",
        },
        {
            "id": 2,
            "dataset_id": 3,
            "gene_id": 11,
            "counts_object_path": "counts/MYB41 root/AT1G01010.json",
        },
        {"id": 3, "dataset_id": 3, "gene_id": 12, "counts_object_path": None},
        {
            "id": 4,
            "dataset_id": 3,
            "gene_id": 13,
            "counts_object_path": "counts/MYB41_root_3_/AT3G03030.json",
        },
        {
            "id": 5,
            "dataset_id": 3,
            "gene_id": 17,
            "counts_object_path": "counts/MYB41_root_3_/AT5G05050.json",
        },
        # Another dataset's file for gene 12, which dataset 3 stores none for.
        {
            "id": 6,
            "dataset_id": 4,
            "gene_id": 12,
            "counts_object_path": "counts/Atlas_4_/AT2G02020.json",
        },
    ],
    "genes": [
        {
            "gene_id": "AT4G28110.Araport11.447",
            "symbol": "MYB41",
            "standard_name": None,
            "short_description": None,
            "ortho_group": None,
            "reference_id": None,
        },
        # One symbol on two genes.
        {
            "gene_id": "AT2G02020.Araport11.1",
            "symbol": "PAIR",
            "standard_name": None,
            "short_description": None,
            "ortho_group": None,
            "reference_id": None,
        },
        {
            "gene_id": "AT3G03030.Araport11.1",
            "symbol": "PAIR",
            "standard_name": None,
            "short_description": None,
            "ortho_group": None,
            "reference_id": None,
        },
    ],
}
FILES = {
    ("scrna", "counts/MYB41_root_3_/AT4G28110.json"): b'{"1": 4.0, "2": 2.0, "3": 1.0}',
    # An older, list-shaped file under the legacy path shape.
    ("scrna", "counts/MYB41 root/AT1G01010.json"): b'[{"0": 1.0}, {"4": 3.0}]',
    # Written against other cells.
    ("scrna", "counts/MYB41_root_3_/AT3G03030.json"): b'{"9": 1.0}',
    # Values that need rounding.
    ("scrna", "counts/MYB41_root_3_/AT5G05050.json"): b'{"0": 1, "1": 0.123456789}',
    ("scrna", "counts/Atlas_4_/AT2G02020.json"): b'{"0": 1.0}',
}
CELL_ARRAYS = Function("scrna_cell_arrays", (Param("ds_id", "bigint", True),))
