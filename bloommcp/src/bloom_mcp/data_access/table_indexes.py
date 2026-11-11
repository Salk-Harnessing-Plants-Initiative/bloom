"""Which columns Claude may sort by, and where `contains` may search, so every read the
data tools send can use an index.

Taken from prod's indexes (the first column of each btree index, and trigram indexes).
tests/integration/test_bloommcp_table_indexes.py fails when a listed index is gone.
"""

from __future__ import annotations

# The first column of each readable table's plain btree indexes.
SORTABLE_COLUMNS: dict[str, frozenset[str]] = {
    t: frozenset(c.split())
    for t, c in {
        "accessions": "id name",
        "assemblies": "id",
        "cyl_camera_settings": "id scanner_exposure_time",
        "cyl_datasets": "id name",
        "cyl_experiment_trait_counts": "experiment_id",
        "cyl_experiments": "id scientist_id species_id",
        "cyl_image_traits": "id",
        "cyl_images": "id scan_id",
        "cyl_pipeline_run_scans": "argo_workflow_name id run_id scan_id",
        "cyl_pipeline_run_workflows": "run_id",
        "cyl_pipeline_runs": "id",
        "cyl_plant_metadata": "id",
        "cyl_plants": "accession_id id qr_code wave_id",
        "cyl_plants_metadata": "id",
        "cyl_qc_codes": "id",
        "cyl_qc_set_codes": "set_id",
        "cyl_qc_sets": "id name",
        "cyl_scan_intermediates": "id source_id",
        "cyl_scan_latest_source": "scan_id",
        "cyl_scan_videos": "id scan_id",
        "cyl_scanners": "id name",
        "cyl_scans": "id plant_id",
        "cyl_scientists": "email id",
        "cyl_trait_sources": "id idempotency_key recipe_key scan_id",
        "cyl_traits": "id name",
        "cyl_video_jobs": "id scan_id status",
        "cyl_waves": "experiment_id id",
        "experiment_progress_logs": "id",
        "gene_candidate_scientists": "id",
        "gene_candidate_support": "primary_key",
        "gene_candidates": "gene",
        "gene_orthologs": "gene_x",
        "gene_patents": "primary_key",
        "gene_progress_notes": "primary_key",
        "genes": "gene_id short_id",
        "genome_annotation_builds": "file_path id version_id",
        "genome_reference_versions": "genome_id id",
        "genome_references": "id name",
        "gravi_experiments": "id species_id",
        "gravi_images": "id scan_id",
        "gravi_plate_video_jobs": "experiment_id id status",
        "gravi_plate_videos": "experiment_id id",
        "gravi_scan_metadata_accession": "accession_id id",
        "gravi_scan_metadata_section_plants": "id section_id",
        "gravi_scan_metadata_sections": "id metadata_id",
        "gravi_scan_sessions": "id",
        "gravi_scanners": "id name",
        "gravi_scans": "experiment_id id",
        "ortho_gene_id_map": "id ortho_group",
        "people": "id",
        "phenotypers": "email id",
        "plate_plant_traits_list": "id plant_trait",
        "plates_exp": "id plate_id",
        "plates_scan_trait": "id plate_id",
        "plates_source_table": "file_path id",
        "plates_trait_source": "id source",
        "rnaseq_references": "id name",
        "rnaseq_runs": "created_at dataset_id genome_version_id id workflow_type",
        "rnaseq_samples": "id name",
        "scrna_cells": "dataset_id genotype_id id",
        "scrna_cluster_neighbors": "dataset_id",
        "scrna_cluster_stats": "dataset_id",
        "scrna_clusters": "dataset_id id",
        "scrna_counts": "dataset_id id",
        "scrna_datasets": "genome_version_id id",
        "scrna_de": "dataset_id id run_id",
        "scrna_de_genes": "dataset_id de_id id",
        "scrna_de_runs": "dataset_id id",
        "scrna_embedding_dataset_members": "dataset_id embedding_id",
        "scrna_embedding_labels": "embedding_id",
        "scrna_embedding_points": "cell_id embedding_id",
        "scrna_embeddings": "id name",
        "scrna_enrichment_runs": "dataset_id file_path id rnaseq_run_id",
        "scrna_genes": "dataset_id id",
        "scrna_genotypes": "dataset_id id",
        "species": "common_name genus id",
        "translation_candidates": "id",
        "translation_lines": "id",
        "translation_project_users": "id",
        "translation_projects": "id name",
    }.items()
}

# Tables and views over more than 100,000 rows: `contains` there must use a trigram index.
LARGE_TABLES = frozenset(
    {
        "cyl_images",
        "cyl_scans",
        "genes",
        "scrna_cells",
        "scrna_counts",
        "scrna_de_genes",
        "scrna_genes",
        # Views over cyl_scans, cyl_scan_traits or cyl_dataset_traits.
        "cyl_dataset_trait_names",
        "cyl_pipeline_run_experiments",
        "cyl_scan_timeline",
        "cyl_scan_trait_names",
        "cyl_scan_traits_latest",
        "cyl_scan_traits_source",
        "cyl_scans_extended",
        "cyl_trait_by_experiment_wave",
        "cyl_wave_timeline",
        "recent_experiments_by_cyl_scanner",
    }
)

# Large-table columns with a trigram index, so `contains` doesn't scan the table.
CONTAINS_COLUMNS: dict[str, frozenset[str]] = {
    "genes": frozenset({"gene_id", "standard_name", "symbol"}),
    "scrna_genes": frozenset({"gene_name"}),
}


def sortable_columns(table: str) -> frozenset[str]:
    """Columns `table` may be sorted by; a view or unlisted table only by its paging key."""
    from . import table_access  # here, so the live index test can load this file alone

    return SORTABLE_COLUMNS.get(table) or frozenset(table_access.paging_key(table)[:1])


def contains_columns(table: str) -> frozenset[str] | None:
    """Columns `contains` may search on `table`; None when any column may be searched."""
    if table not in LARGE_TABLES:
        return None
    return CONTAINS_COLUMNS.get(table, frozenset())
