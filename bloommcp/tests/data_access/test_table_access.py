"""The allow-list of tables and hidden columns bloommcp's data tools read through."""

from __future__ import annotations

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
