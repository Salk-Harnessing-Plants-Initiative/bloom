"""The allow-list of tables and hidden columns bloommcp's data tools read through."""

from __future__ import annotations

import time

import pytest

from bloom_mcp.data_access import table_access, table_notes


def test_no_table_is_both_readable_and_excluded():
    assert not (table_access.READABLE_TABLES & table_access.EXCLUDED_TABLES)


def test_chat_and_usage_tables_are_excluded():
    for table in ("checkpoints", "checkpoint_writes", "chat_threads", "bloommcp_usage"):
        assert not table_access.is_readable(table)


def test_tables_with_staff_emails_are_excluded():
    for table in (
        "experiment_progress_logs",
        "rhizovision_accessions",
        "rhizovision_experiments",
        "rhizovision_images",
        "rhizovision_results",
        "rhizovision_settings",
    ):
        assert not table_access.is_readable(table)


def test_hidden_columns_belong_to_readable_tables():
    assert set(table_access.HIDDEN_COLUMNS) <= table_access.READABLE_TABLES


def test_every_email_column_is_hidden():
    for table in ("people", "phenotypers", "cyl_scientists"):
        assert "email" in table_access.HIDDEN_COLUMNS[table]


def test_video_job_error_text_is_hidden():
    for table in ("gravi_plate_video_jobs", "cyl_video_jobs"):
        assert {"error_message", "msg_id", "reads"} <= table_access.HIDDEN_COLUMNS[
            table
        ]


def test_visible_columns_drop_hidden_ones_and_keep_order():
    columns = ["id", "name", "email", "created_at"]
    assert table_access.visible_columns("people", columns) == [
        "id",
        "name",
        "created_at",
    ]


def test_an_unknown_table_is_not_readable():
    assert not table_access.is_readable("not_a_table")


def test_redact_emails_reaches_nested_values_and_keys():
    value = {"a@b.org": ["x c.d@e.co.uk y", {"k": "no email", "n": 3}], "z": None}
    assert table_access.redact_emails(value) == {
        table_access.EMAIL_HIDDEN: [
            f"x {table_access.EMAIL_HIDDEN} y",
            {"k": "no email", "n": 3},
        ],
        "z": None,
    }


def test_redacted_columns_are_readable_but_not_filterable():
    for column in ("status_logs", "legacy_progress_logs_jsonb"):
        assert column in table_access.EMAIL_REDACTED_COLUMNS["gene_candidates"]
        assert column in table_access.visible_columns("gene_candidates", [column])


def test_paging_keys_name_readable_tables_and_default_to_id():
    assert set(table_access.PAGING_KEYS) <= table_access.READABLE_TABLES
    assert all(table_access.PAGING_KEYS.values())
    assert table_access.paging_key("species") == ("id",)
    assert table_access.paging_key("orthogroups") == (
        "run_id",
        "protein_uid",
        "orthogroup",
    )


def test_every_note_names_a_readable_table():
    assert set(table_notes.TABLE_NOTES) <= table_access.READABLE_TABLES


def test_trait_values_are_read_only_through_the_views_that_apply_the_latest_rule():
    assert not table_access.is_readable("cyl_scan_traits")
    assert "latest" in table_notes.TABLE_NOTES["cyl_scan_traits_latest"]
    assert "is_latest" in table_notes.TABLE_NOTES["cyl_scan_traits_source"]


def test_views_that_show_deleted_experiments_say_so():
    for view in ("cyl_scans_extended", "cyl_plants_extended", "cyl_wave_timeline"):
        assert "deleted" in table_notes.TABLE_NOTES[view]


@pytest.mark.parametrize(
    "table",
    [
        "cyl_waves",
        "cyl_plants",
        "cyl_scans",
        "cyl_scan_timeline",
        "cyl_scan_traits_latest",
        "cyl_scan_traits_source",
    ],
)
def test_cylinder_tables_that_show_deleted_experiments_say_so(table):
    assert "deleted" in table_notes.TABLE_NOTES[table]


@pytest.mark.parametrize(
    "table",
    [
        "scrna_cells",
        "scrna_genes",
        "scrna_clusters",
        "scrna_counts",
        "scrna_cluster_stats",
        "scrna_genotypes",
        "scrna_de",
        "scrna_de_runs",
    ],
)
def test_scrna_tables_that_show_deleted_datasets_say_so(table):
    assert "deleted datasets" in table_notes.TABLE_NOTES[table]


def test_the_summary_counts_name_deleted_experiments():
    from bloom_mcp.data_access import function_access

    entry = function_access.READABLE_FUNCTIONS["get_experiment_summary_counts"]
    assert "deleted = false" in entry.description


def test_dataset_traits_are_read_by_name():
    # Its trait_id is a raw trait-value row, which looks like a cyl_traits id but isn't.
    assert not table_access.is_readable("cyl_dataset_traits")
    assert "cyl_dataset_trait_names" in table_notes.TABLE_NOTES["cyl_datasets"]
    assert table_access.is_readable("cyl_dataset_trait_names")


def test_the_older_deleted_flag_is_named():
    assert "deleted = true" in table_notes.TABLE_NOTES["cyl_experiments"]


@pytest.mark.parametrize(
    ("table", "columns"),
    [
        ("cyl_pipeline_runs", {"error_message"}),
        ("cyl_pipeline_run_scans", {"error_message"}),
        ("scrna_de_runs", {"error_message"}),
        ("rnaseq_runs", {"message", "step_pods"}),
    ],
)
def test_pipeline_error_text_and_pod_names_are_hidden(table, columns):
    assert columns <= table_access.HIDDEN_COLUMNS[table]
    assert "status" in table_access.visible_columns(table, ["status", *columns])


@pytest.mark.parametrize(
    "view",
    [
        "cyl_scans_extended",
        "cyl_plants_extended",
        "cyl_plant_search",
        "gravi_scans_extended",
    ],
)
def test_the_joining_views_have_a_note(view):
    assert table_notes.TABLE_NOTES[view]


def test_the_scrna_notes_match_what_the_website_shows():
    notes = table_notes.TABLE_NOTES
    assert "NULL_DATASET" in notes["scrna_datasets"]
    assert "reference" in notes["scrna_datasets"]
    assert "complete" in notes["scrna_de_runs"]
    assert "combine all its complete runs" in notes["scrna_de_runs"]
    assert "cluster_id and contrast" in notes["scrna_de"]
    assert "latest completed_at" in notes["scrna_de"]
    assert "tested = false" in notes["scrna_de"]
    assert "scrna_de" in notes["scrna_de_genes"]


def test_the_wave_averages_say_they_mix_every_run():
    assert (
        "every pipeline run" in table_notes.TABLE_NOTES["cyl_trait_by_experiment_wave"]
    )


@pytest.mark.parametrize(
    "address",
    ["josé@salk.edu", "o'brien@salk.edu", "o’brien@salk.edu", "a.b+c@lab.salk.edu"],
)
def test_unusual_addresses_are_hidden_whole(address):
    assert (
        table_access.redact_emails(f"by {address}.")
        == f"by {table_access.EMAIL_HIDDEN}."
    )


def test_redacting_and_refusing_filters_are_separate_rules_covering_the_same_columns():
    assert table_access.EMAIL_REDACTED_COLUMNS == table_access.UNFILTERABLE_COLUMNS
    assert table_access.redacted_columns("gene_candidates")
    assert table_access.unfilterable_columns("gene_candidates")


def test_the_latest_trait_note_says_latest_is_per_scan():
    assert "each scan's values" in table_notes.TABLE_NOTES["cyl_scan_traits_latest"]


@pytest.mark.parametrize(
    "text",
    [
        "a" * 200_000,
        "a@" + "b" * 200_000,
        "a@" + "b." * 100_000,
        ("x" * 50 + " ") * 4_000 + "ann.lee@salk.edu",
    ],
    ids=["no at sign", "no dot", "dotted", "address at the end"],
)
def test_long_text_is_redacted_quickly(text):
    started = time.monotonic()
    redacted = table_access.redact_emails(text)
    assert time.monotonic() - started < 0.5
    assert "ann.lee" not in redacted


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("John.Doe@SALK.EDU", "[email hidden]"),
        ("a+b@sub.salk.edu and c@x.org", "[email hidden] and [email hidden]"),
        ("(ann@salk.edu)", "([email hidden])"),
        ("ü@ä.de", "[email hidden]"),
    ],
)
def test_addresses_are_still_hidden_whole(text, expected):
    assert table_access.redact_emails(text) == expected


@pytest.mark.parametrize(
    "view",
    [
        "cyl_accession_sample_counts",
        "cyl_experiment_accessions",
        "cyl_trait_by_experiment_wave",
        "cyl_plant_search",
        "recent_experiments_by_cyl_scanner",
    ],
)
def test_views_that_miss_the_older_deleted_flag_say_so(view):
    assert "deleted = true" in table_notes.TABLE_NOTES[view]


def test_accession_plant_counts_point_to_a_table_that_can_drop_deleted_experiments():
    assert (
        "cyl_plants_extended" in table_notes.TABLE_NOTES["cyl_accession_sample_counts"]
    )


@pytest.mark.parametrize(
    "table",
    [
        "cyl_experiment_trait_counts",
        "cyl_pipeline_run_experiments",
        "cyl_qc_sets",
        "cyl_video_jobs",
        "scrna_cluster_neighbors",
        "scrna_enrichment_runs",
        "scrna_embedding_dataset_members",
    ],
)
def test_more_child_tables_that_show_deleted_items_say_so(table):
    assert "Includes deleted" in table_notes.TABLE_NOTES[table]


def test_the_de_gene_note_leads_with_named_top_genes():
    note = table_notes.TABLE_NOTES["scrna_de_genes"]
    assert "fdr < 0.05" in note and "scrna_genes.id" in note


def test_the_marker_note_matches_how_the_website_picks_markers():
    note = table_notes.TABLE_NOTES["scrna_cluster_stats"]
    assert "markers" in note and "group2 = 'rest'" in note
    assert "log2fc > 0" in note and "fdr < 0.05" in note and "top 25" in note


@pytest.mark.parametrize(
    "table",
    [
        "cyl_scan_traits_latest",
        "cyl_scan_traits_source",
        "cyl_trait_by_experiment_wave",
    ],
)
def test_trait_tables_split_website_figures_from_analysis(table):
    note = table_notes.TABLE_NOTES[table]
    assert table_notes.TRAIT_TOOL_RULE in note


def test_the_trait_rule_keeps_website_figures_and_routes_analysis():
    rule = table_notes.TRAIT_TOOL_RULE
    assert "figures Bloom's pages show" in rule and "mean and median" in rule
    for analysis in (
        "QC",
        "heritability",
        "PCA",
        "clustering",
        "correlations",
        "plots",
    ):
        assert analysis in rule
    assert (
        "sleap_roots_*" in rule and "cyl_experiments.id" in rule and "qc_clean" in rule
    )
    assert "ask the user" in rule
    assert "summary statistics" not in rule


def test_wave_averages_point_to_latest_run_figures():
    note = table_notes.TABLE_NOTES["cyl_trait_by_experiment_wave"]
    assert "cyl_scans_extended by scan_id" in note
    assert "group cyl_scan_traits_latest" in note
