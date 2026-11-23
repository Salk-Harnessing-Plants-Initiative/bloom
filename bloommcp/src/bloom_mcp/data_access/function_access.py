"""Which database functions bloommcp's data tools may call: read-only ones, by name.

An allow-list, like the tables': a function not named here can't be called even if
bloom_agent may execute it. tests/integration/test_bloommcp_data_functions.py fails when a
function bloom_agent can execute is in neither list, and when a readable one could write.
"""

from __future__ import annotations

from dataclasses import dataclass

from .table_notes import TRAIT_TOOL_RULE
from .table_ports import Sort

# The most rows a search returns; the functions cap their own limit inputs here too.
MAX_SEARCH_ROWS = 1000


@dataclass(frozen=True)
class ReadableFunction:
    description: str
    # Sorts the function's rows so pages never overlap.
    order: tuple[Sort, ...]
    # A search's row-limit input: bloommcp sets it from offset and limit, Claude doesn't.
    page_input: str | None = None
    # A top-N input Claude sets, up to MAX_SEARCH_ROWS.
    count_input: str | None = None
    # Values stored as 4-byte floats that the function widens; shown as the website shows them.
    float4_columns: tuple[str, ...] = ()


def _by(*columns: str) -> tuple[Sort, ...]:
    return tuple(Sort(c) for c in columns)


_PAGED = f"Pages through up to {MAX_SEARCH_ROWS:,} matches."


READABLE_FUNCTIONS: dict[str, ReadableFunction] = {
    # Cylinder experiments and their traits; trait values are the latest run's by default.
    "get_experiment_summary_counts": ReadableFunction(
        "Plants and traits per cylinder experiment. With no id it lists every experiment, "
        "deleted ones included: keep only ids in cyl_experiments with deleted = false.",
        _by("experiment_id"),
    ),
    "get_experiment_traits": ReadableFunction(
        "Every trait value of one cylinder experiment, one row per scan and trait, from "
        "the latest run unless source_id_, run_id_ or recipe_key_ pins one. "
        + TRAIT_TOOL_RULE,
        # trait_value breaks ties between old rows with no source.
        _by("scan_id", "trait_name", "source_id", "trait_value"),
        float4_columns=("trait_value",),
    ),
    "get_scan_traits": ReadableFunction(
        "One trait's values for every scan of a cylinder experiment, latest run by default. "
        + TRAIT_TOOL_RULE,
        _by("scan_id", "trait_value"),
        float4_columns=("trait_value",),
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
    # Expression.
    "scrna_gene_search": ReadableFunction(
        f"Find genes in one scRNA dataset by the start of their name. {_PAGED}",
        _by("gene_name"),
        page_input="lim",
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
        # Stop at 50 rows; filtering name on the experiment tables returns every match.
        "cyl_experiment_search",
        "gravi_experiment_search",
    }
)


def is_callable(name: str) -> bool:
    return name in READABLE_FUNCTIONS
