"""The allow-list of tables and hidden columns bloommcp's data tools read through."""

from __future__ import annotations

import pytest

from bloom_mcp.data_access import table_access


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
    assert set(table_access.TABLE_NOTES) <= table_access.READABLE_TABLES


def test_trait_values_are_read_only_through_the_views_that_apply_the_latest_rule():
    assert not table_access.is_readable("cyl_scan_traits")
    assert "latest" in table_access.TABLE_NOTES["cyl_scan_traits_latest"]
    assert "is_latest" in table_access.TABLE_NOTES["cyl_scan_traits_source"]


def test_views_that_show_deleted_experiments_say_so():
    for view in ("cyl_scans_extended", "cyl_plants_extended", "cyl_wave_timeline"):
        assert "deleted" in table_access.TABLE_NOTES[view]


def test_the_older_deleted_flag_is_named():
    assert "deleted = true" in table_access.TABLE_NOTES["cyl_experiments"]


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
    assert table_access.TABLE_NOTES[view]
