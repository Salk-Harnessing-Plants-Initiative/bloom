"""Which database functions bloommcp's data tools may call: read-only ones, by name.

An allow-list, like the tables': a function not named here can't be called even if
bloom_agent may execute it. tests/integration/test_bloommcp_data_tools.py fails when a
function bloom_agent can execute is in neither list, and when a readable one could write.
"""

from __future__ import annotations

from dataclasses import dataclass

from .table_ports import Sort


@dataclass(frozen=True)
class ReadableFunction:
    description: str
    # Sorts the function's rows so pages never overlap.
    order: tuple[Sort, ...]


def _by(*columns: str) -> tuple[Sort, ...]:
    return tuple(Sort(c) for c in columns)


_MOST_SIMILAR_FIRST = (Sort("similarity", descending=True), Sort("uid"))

READABLE_FUNCTIONS: dict[str, ReadableFunction] = {
    # Cylinder experiments and their traits; trait values are the latest run's by default.
    "cyl_experiment_search": ReadableFunction(
        "Find cylinder experiments by name, optionally within one species.", _by("id")
    ),
    "get_experiment_summary_counts": ReadableFunction(
        "Plants and traits per cylinder experiment (all experiments when no id is given).",
        _by("experiment_id"),
    ),
    "get_experiment_traits": ReadableFunction(
        "Every trait value of one cylinder experiment, one row per scan and trait, from "
        "the latest run unless source_id_ or run_id_ pins one.",
        _by("scan_id", "trait_name", "source_id"),
    ),
    "get_scan_traits": ReadableFunction(
        "One trait's values for every scan of a cylinder experiment, latest run by default.",
        _by("scan_id"),
    ),
    "list_experiment_trait_sources": ReadableFunction(
        "The pipeline runs that produced trait values for a cylinder experiment.",
        _by("source_id"),
    ),
    "list_trait_recipes": ReadableFunction(
        "The trait recipes (pipeline settings) used for experiments or scans.",
        _by("recipe_key", "recipe_key_version"),
    ),
    "get_trait_recipe_coverage": ReadableFunction(
        "Which scans have traits under which recipe.", _by("scan_id", "recipe_key")
    ),
    # Plates.
    "gravi_experiment_search": ReadableFunction(
        "Find plate (GraviScan) experiments by name, optionally within one species.",
        _by("id"),
    ),
    # Genes and proteins.
    "search_genes": ReadableFunction(
        "Find cross-species proteins by part of their uid or gene id.", _by("uid")
    ),
    "search_accession_genes": ReadableFunction(
        "Find accession proteins by part of their uid or gene id, optionally in one "
        "accession.",
        _by("uid"),
    ),
    "knn_search_esm2": ReadableFunction(
        "Cross-species proteins most similar to one protein (ESM-2 embeddings).",
        _MOST_SIMILAR_FIRST,
    ),
    "knn_search_esm3": ReadableFunction(
        "Accession proteins most similar to one protein (ESM-3 embeddings).",
        _MOST_SIMILAR_FIRST,
    ),
    "best_match_per_accession": ReadableFunction(
        "The most similar protein in each accession to one protein (ESM-3).",
        _by("accession_id", "rank_in_accession", "uid"),
    ),
    "compare_gene_across_accessions": ReadableFunction(
        "One gene's variants across accessions, ranked by similarity to a reference.",
        _MOST_SIMILAR_FIRST,
    ),
    "get_orthogroup_info": ReadableFunction(
        "Which proteins share an orthogroup with a query protein (active OrthoFinder run).",
        _by("protein_uid", "orthogroup"),
    ),
    # Expression.
    "scrna_gene_search": ReadableFunction(
        "Find genes in one scRNA dataset by part of their name.", _by("gene_name")
    ),
}

EXCLUDED_FUNCTIONS = frozenset(
    {
        # Marked VOLATILE, so they may write: imports, uploads, logs, usage bookkeeping.
        "append_experiment_log",
        "assign_partition_numbers",
        "create_cyl_dataset",
        "get_scans_without_videos",
        "get_unique_categories",
        "insert_cyl_qc_codes",
        "insert_image",
        "insert_image_v2_0",
        "insert_image_v3_0",
        "record_bloommcp_usage",
        # Triggers.
        "check_email_domain",
        "delete_reverse_pair",
        "insert_reverse_pair",
        "set_created_by",
        "update_chat_threads_updated_at",
        # Run with their owner's rights: an email-to-account lookup, and an internal count.
        "bloom_user_for_email",
        "compute_cyl_experiment_summary_counts_live",
        # Internal helpers and website plumbing.
        "_cyl_trait_recipe_presence",
        "bloom_table_row_estimates",
        "cyl_plant_search_navigate",
        "cyl_plant_search_query",
        "cyl_trait_recipe_key_v1",
        "cyl_trait_recipe_payload_v1",
        "genome_name_ok",
        # A whole dataset's cells or embedding in one row: many MB.
        "scrna_cell_arrays",
        "scrna_embedding_arrays",
        "scrna_embedding_label_codes",
    }
)


def is_callable(name: str) -> bool:
    return name in READABLE_FUNCTIONS
