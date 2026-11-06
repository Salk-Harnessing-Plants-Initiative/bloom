"""Which tables and columns bloommcp's data tools may read.

An allow-list: a table not named here is invisible to Claude even if bloom_agent can read it.
tests/integration/test_bloommcp_table_classification.py fails when a database table is in
neither list, so a new table is classified on purpose.
"""

from __future__ import annotations

# Chat and usage internals, and protein embedding vectors (thousands of numbers per row).
EXCLUDED_TABLES = frozenset(
    {
        "bloommcp_usage",
        "chat_threads",
        "checkpoint_blobs",
        "checkpoint_migrations",
        "checkpoint_writes",
        "checkpoints",
        "cyl_experiment_trait_count_changes",
        "protein_embeddings_esm2",
        "protein_embeddings_esm3",
    }
)

READABLE_TABLES = frozenset(
    {
        "accessions",
        "arabidopsis_accessions",
        "assemblies",
        "cyl_accession_sample_counts",
        "cyl_camera_settings",
        "cyl_dataset_trait_names",
        "cyl_dataset_traits",
        "cyl_datasets",
        "cyl_experiment_accessions",
        "cyl_experiment_trait_counts",
        "cyl_experiments",
        "cyl_image_traits",
        "cyl_images",
        "cyl_pipeline_run_experiments",
        "cyl_pipeline_run_scans",
        "cyl_pipeline_run_workflows",
        "cyl_pipeline_runs",
        "cyl_plant_metadata",
        "cyl_plant_search",
        "cyl_plants",
        "cyl_plants_extended",
        "cyl_plants_metadata",
        "cyl_qc_codes",
        "cyl_qc_set_codes",
        "cyl_qc_sets",
        "cyl_scan_intermediates",
        "cyl_scan_latest_source",
        "cyl_scan_timeline",
        "cyl_scan_trait_names",
        "cyl_scan_traits",
        "cyl_scan_traits_latest",
        "cyl_scan_traits_source",
        "cyl_scan_videos",
        "cyl_scanners",
        "cyl_scans",
        "cyl_scans_extended",
        "cyl_scientists",
        "cyl_trait_by_experiment_wave",
        "cyl_trait_sources",
        "cyl_traits",
        "cyl_video_jobs",
        "cyl_wave_timeline",
        "cyl_waves",
        "experiment_progress_logs",
        "gene_candidate_scientists",
        "gene_candidate_support",
        "gene_candidates",
        "gene_orthologs",
        "gene_patents",
        "gene_progress_notes",
        "genes",
        "genome_annotation_builds",
        "genome_reference_versions",
        "genome_references",
        "gravi_experiments",
        "gravi_images",
        "gravi_plate_video_jobs",
        "gravi_plate_videos",
        "gravi_scan_metadata_accession",
        "gravi_scan_metadata_section_plants",
        "gravi_scan_metadata_sections",
        "gravi_scan_sessions",
        "gravi_scan_timeline",
        "gravi_scanners",
        "gravi_scans",
        "gravi_scans_extended",
        "ortho_gene_id_map",
        "orthogroup_runs",
        "orthogroups",
        "people",
        "phenotypers",
        "plate_plant_traits_list",
        "plates_exp",
        "plates_scan_trait",
        "plates_source_table",
        "plates_trait_source",
        "protein_embedding_models",
        "protein_embedding_runs",
        "proteins",
        "recent_experiments_by_cyl_scanner",
        "recent_phenotypes_by_plate_scanner",
        "rhizovision_accessions",
        "rhizovision_experiments",
        "rhizovision_images",
        "rhizovision_results",
        "rhizovision_settings",
        "rnaseq_references",
        "rnaseq_runs",
        "rnaseq_samples",
        "scrna_cells",
        "scrna_cluster_neighbors",
        "scrna_cluster_stats",
        "scrna_clusters",
        "scrna_counts",
        "scrna_datasets",
        "scrna_de",
        "scrna_de_genes",
        "scrna_de_runs",
        "scrna_embedding_dataset_members",
        "scrna_embedding_labels",
        "scrna_embedding_points",
        "scrna_embeddings",
        "scrna_enrichment_runs",
        "scrna_genes",
        "scrna_genotypes",
        "species",
        "translation_candidates",
        "translation_lines",
        "translation_project_users",
        "translation_projects",
    }
)

# Never selected, filtered or ordered on: emails, and the video queues' renderer error text
# (internal hosts and roles) and queue plumbing, which bloom_agent has no grant on anyway.
HIDDEN_COLUMNS: dict[str, frozenset[str]] = {
    "people": frozenset({"email"}),
    "phenotypers": frozenset({"email"}),
    "cyl_scientists": frozenset({"email"}),
    "cyl_video_jobs": frozenset({"error_message", "msg_id", "reads"}),
    "gravi_plate_video_jobs": frozenset({"error_message", "msg_id", "reads"}),
}

# Above this many rows (the planner's estimate), counts are estimated instead of exact.
LARGE_TABLE_ROWS = 200_000


def is_readable(table: str) -> bool:
    return table in READABLE_TABLES


def visible_columns(table: str, columns: list[str]) -> list[str]:
    hidden = HIDDEN_COLUMNS.get(table, frozenset())
    return [c for c in columns if c not in hidden]
