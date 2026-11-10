"""Notes shown with tables in list_tables: which table gives the answer the website shows,
what to leave out (deleted items, placeholders, superseded runs) and how to read results.
"""

from __future__ import annotations

_INCLUDES_DELETED_EXPERIMENTS = (
    "Includes deleted experiments: keep only experiment_ids found in cyl_experiments with "
    "deleted = false."
)
_SCAN_EXPERIMENT = (
    "Includes deleted experiments: to keep only live ones, take scan_ids from "
    "cyl_scans_extended rows whose experiment_id is in cyl_experiments with deleted = false."
)
# Views that hide deleted_at deletions but not the older deleted = true flag.
_OLDER_DELETIONS_SHOW = (
    "Experiments deleted the older way (deleted = true) still show: keep only "
    "experiment_ids found in cyl_experiments with deleted = false."
)
_INCLUDES_DELETED_DATASETS = (
    "Includes deleted datasets: keep only dataset_ids found in scrna_datasets."
)
_FIND_EXPERIMENTS_BY_NAME = "To find experiments by name, filter name with contains; count_rows gives the total."

# Data tools give what Bloom's pages show; analysis is the sleap-roots tools' job.
TRAIT_TOOL_RULE = (
    "Trait values come from the sleap-roots pipeline. Use these rows for values and the "
    "figures Bloom's pages show (a scan's values; per-wave or per-accession n, mean and "
    "median). For analysis (QC and cleaning, outliers, heritability, PCA, UMAP, clustering, "
    "correlations, statistical comparisons, plots) don't compute from these rows: use the "
    "sleap-roots analysis tools (sleap_roots_*; pass the numeric cyl_experiments.id, and "
    "most need qc_clean first) and say the results come from them. If a question could be "
    "either, ask the user which they want."
)

# Shown with the table in list_tables: which table to use where the wrong one gives a wrong answer.
TABLE_NOTES: dict[str, str] = {
    "cyl_scan_traits_latest": (
        "Use for trait values: each scan's values from that scan's latest run, as the "
        "website shows; a trait that run didn't produce is absent even if an older run had "
        "it. " + _SCAN_EXPERIMENT + " " + TRAIT_TOOL_RULE
    ),
    "cyl_scan_traits_source": (
        "Every run's trait values. Only for comparing runs; is_latest marks the value to "
        "use. " + _SCAN_EXPERIMENT + " " + TRAIT_TOOL_RULE
    ),
    "cyl_experiments": (
        "Skip rows with deleted = true: older deletions use this flag. "
        + _FIND_EXPERIMENTS_BY_NAME
    ),
    "cyl_waves": _INCLUDES_DELETED_EXPERIMENTS,
    "cyl_datasets": "For a dataset's traits, read cyl_dataset_trait_names.",
    "cyl_plants": (
        "Includes deleted experiments: read cyl_plants_extended instead, which has "
        "experiment_id. " + _INCLUDES_DELETED_EXPERIMENTS
    ),
    "cyl_scans": (
        "Includes deleted experiments: read cyl_scans_extended instead, which has "
        "experiment_id. " + _INCLUDES_DELETED_EXPERIMENTS
    ),
    "cyl_scan_timeline": (
        "Scans per day across all experiments, deleted ones included; for live experiments "
        "only, count cyl_scans_extended by date_scanned instead."
    ),
    "gravi_experiments": _FIND_EXPERIMENTS_BY_NAME,
    # Views that read with their owner's rights, so deleted experiments come through.
    "cyl_scans_extended": (
        "One row per scan with its plant, wave, experiment and species: read this instead "
        "of joining those tables. " + _INCLUDES_DELETED_EXPERIMENTS
    ),
    "cyl_plants_extended": (
        "One row per plant with its wave, experiment and species: read this instead of "
        "joining those tables. " + _INCLUDES_DELETED_EXPERIMENTS
    ),
    "cyl_plant_search": (
        "Find cylinder plants by QR code, accession, species or experiment in one read. "
        + _OLDER_DELETIONS_SHOW
    ),
    "cyl_experiment_accessions": _OLDER_DELETIONS_SHOW,
    "recent_experiments_by_cyl_scanner": _OLDER_DELETIONS_SHOW,
    "cyl_accession_sample_counts": (
        "Counts plants of experiments deleted the older way (deleted = true) too, with no "
        "experiment_id to remove them: for exact plant counts per accession, count "
        "cyl_plants_extended rows whose experiment_id is in cyl_experiments with "
        "deleted = false."
    ),
    **{
        t: _INCLUDES_DELETED_EXPERIMENTS
        for t in (
            "cyl_experiment_trait_counts",
            "cyl_pipeline_run_experiments",
            "cyl_qc_sets",
            "cyl_video_jobs",
        )
    },
    "scrna_datasets": (
        "Skip name = 'NULL_DATASET' (a placeholder with no data) and kind = 'reference' "
        "(an atlas without cells) unless asked about them, as the website does."
    ),
    "scrna_de_runs": (
        "Only status = 'complete' runs have results. A dataset's results combine all its "
        "complete runs: a later run replaces only the comparisons it re-ran, so don't "
        "read just the latest run. " + _INCLUDES_DELETED_DATASETS
    ),
    "scrna_de": (
        "Use only rows whose run_id is a complete run in scrna_de_runs. For each "
        "dataset_id, cluster_id and contrast, the current row is the one from the run with "
        "the latest completed_at, as the website shows. tested = false means the "
        "comparison was skipped and has no genes. " + _INCLUDES_DELETED_DATASETS
    ),
    "scrna_de_genes": (
        "Read through scrna_de: keep genes whose de_id is a current comparison there."
    ),
    "cyl_trait_by_experiment_wave": (
        "Averages every pipeline run's values, not only the latest, so it doesn't match "
        "the website's figures; for per-wave figures, take wave_number and plant_age_days "
        "from cyl_scans_extended by scan_id and group cyl_scan_traits_latest by them. "
        + _OLDER_DELETIONS_SHOW
        + " "
        + TRAIT_TOOL_RULE
    ),
    "gravi_scans_extended": (
        "One row per plate scan with its session, experiment, species and accession: read "
        "this instead of joining the gravi_ tables."
    ),
    **{
        t: _INCLUDES_DELETED_DATASETS
        for t in (
            "scrna_cells",
            "scrna_genes",
            "scrna_clusters",
            "scrna_counts",
            "scrna_cluster_stats",
            "scrna_genotypes",
            "scrna_cluster_neighbors",
            "scrna_enrichment_runs",
            "scrna_embedding_dataset_members",
        )
    },
    "cyl_wave_timeline": (
        "Includes deleted experiments: keep only experiment names found in cyl_experiments "
        "with deleted = false."
    ),
}


def table_notes(tables: list[str]) -> dict[str, str]:
    return {t: TABLE_NOTES[t] for t in tables if t in TABLE_NOTES}
