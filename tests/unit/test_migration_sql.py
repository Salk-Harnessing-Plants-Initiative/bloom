"""Tests for scripts/migration_sql.py: what a migration's SQL creates, changes and drops.

Expected sets for the real fixtures were read from the fixture files themselves, so a
change to the scanner that alters them is a behaviour change, not a refactor.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures" / "migrations"


def _load():
    spec = importlib.util.spec_from_file_location(
        "migration_sql", REPO_ROOT / "scripts" / "migration_sql.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["migration_sql"] = module
    spec.loader.exec_module(module)
    return module


migration_sql = _load()
scan = migration_sql.scan


def _fixture(name: str):
    return scan((FIXTURES / name).read_text(encoding="utf-8"))


# --- Real migrations -----------------------------------------------------------


def test_genotypes_migration():
    facts = _fixture("20260909090000_scrna_cells_genotype_and_labels.sql")
    assert facts.tables_created == {"scrna_genotypes"}
    # scrna_genotypes is only ENABLE ROW LEVEL SECURITY'd after creation: not structural.
    assert facts.tables_altered == {"scrna_cells", "scrna_clusters"}
    assert facts.tables_dropped == set()
    assert facts.constraints_added == {
        "scrna_genotypes_one_per_dataset",
        "scrna_genotypes_id_per_dataset",
        "scrna_genotypes_name_not_blank",
        "scrna_genotypes_construct_not_blank",
        "scrna_genotypes_lengths",
        "scrna_cells_genotype_fkey",
        "scrna_cells_facets_are_flat_text",
        "scrna_cells_replicate_length",
    }
    assert facts.constraints_dropped == {
        "scrna_cells_genotype_fkey",
        "scrna_cells_facets_are_flat_text",
        "scrna_cells_replicate_length",
    }
    assert facts.indexes_added == {"idx_scrna_cells_genotype"}
    assert facts.indexes_dropped == set()
    assert facts.unnamed_constraints == ()


def test_contrast_migration_with_dashes_inside_strings():
    facts = _fixture("20260908120000_scrna_de_add_contrast.sql")
    assert facts.tables_created == set()
    assert facts.tables_altered == {"scrna_de"}
    readded = {
        "scrna_de_contrast_names_both_groups",
        "scrna_de_groups_differ",
        "scrna_de_no_file_means_nothing_tested",
        "scrna_de_counts_all_or_none",
        "scrna_de_significant_within_tested",
        "scrna_de_lfc_cut_narrows_fdr_cut",
        "scrna_de_up_plus_down_is_lfc_significant",
        "scrna_de_counts_non_negative",
        "scrna_de_text_not_blank",
        "scrna_de_name_lengths",
        "scrna_de_group_sizes_all_or_none",
        "scrna_de_contrast_rows_carry_counts",
        "scrna_de_comparison_uniqueness",
    }
    assert facts.constraints_added == readded
    assert facts.constraints_dropped == readded | {"scrna_de_counts_consistent"}
    assert facts.indexes_added == set()
    assert facts.indexes_dropped == {"idx_scrna_de_dataset_cluster"}


def test_common_results_migration_with_do_blocks():
    facts = _fixture("20260912090000_scrna_de_common_results.sql")
    assert facts.tables_created == {"scrna_de_runs", "scrna_de_genes"}
    # scrna_clusters, scrna_de_runs and scrna_genes gain keys inside DO blocks;
    # scrna_de_genes is only ENABLE ROW LEVEL SECURITY'd after creation.
    assert facts.tables_altered == {"scrna_de", "scrna_clusters", "scrna_de_runs", "scrna_genes"}
    assert facts.constraints_added == {
        # inline in CREATE TABLE scrna_de_runs
        "scrna_de_runs_batch_does_not_dispatch",
        "scrna_de_runs_failed_says_why",
        "scrna_de_runs_finished_has_a_time",
        # ALTER TABLE ... ADD CONSTRAINT, including the four inside DO blocks
        "scrna_de_result_is_somewhere",
        "scrna_de_untested_counted_nothing",
        "scrna_de_run_rows_are_complete",
        "scrna_de_group_kind_known",
        "scrna_de_cluster_in_catalogue",
        "scrna_clusters_id_per_dataset",
        "scrna_de_cluster_ref_in_catalogue",
        "scrna_de_run_rows_name_the_catalogue",
        "scrna_de_runs_id_per_dataset",
        "scrna_de_id_per_dataset",
        "scrna_de_run_in_same_dataset",
        "scrna_de_comparison_uniqueness",
        "scrna_genes_dataset_gene_key",
        # inline in CREATE TABLE scrna_de_genes
        "scrna_de_genes_one_row_per_gene",
        "scrna_de_genes_result_in_same_dataset",
        "scrna_de_genes_gene_belongs_to_dataset",
        "scrna_de_genes_probabilities_are_probabilities",
        "scrna_de_genes_proportions_are_proportions",
        "scrna_de_genes_fdr_is_not_below_pvalue",
        "scrna_de_genes_fold_change_is_a_number_or_nothing",
    }
    assert facts.constraints_dropped == {
        "scrna_de_no_file_means_nothing_tested",
        "scrna_de_result_is_somewhere",
        "scrna_de_untested_counted_nothing",
        "scrna_de_run_rows_are_complete",
        "scrna_de_group_kind_known",
        "scrna_de_cluster_in_catalogue",
        "scrna_de_cluster_ref_in_catalogue",
        "scrna_de_run_rows_name_the_catalogue",
        "scrna_de_run_id_fkey",
        "scrna_de_run_in_same_dataset",
        "scrna_de_comparison_uniqueness",
    }
    assert facts.indexes_added == {
        "scrna_de_runs_active_idx",
        "scrna_de_runs_dataset_idx",
        "scrna_de_run_idx",
        "scrna_de_question_idx",
        "scrna_de_genes_ranking_idx",
        "scrna_de_genes_gene_idx",
    }
    assert facts.tables_touched == {
        "scrna_de_runs", "scrna_de_genes", "scrna_de", "scrna_clusters", "scrna_genes",
    }


def test_function_only_migration_changes_no_schema():
    facts = _fixture("20260910120000_fix_refresh_cyl_experiment_trait_counts_safeupdate.sql")
    assert not facts.changes_schema
    assert facts.tables_touched == set()


# --- Tokenizer edge cases ---------------------------------------------------------


def test_double_dash_inside_a_string_is_not_a_comment():
    facts = scan("COMMENT ON TABLE t IS 'a -- b'; CREATE TABLE x (id int);")
    assert facts.tables_created == {"x"}


def test_escaped_quote_in_e_string():
    facts = scan(r"SELECT E'it\'s -- here'; CREATE TABLE y (id int);")
    assert facts.tables_created == {"y"}


def test_doubled_quote_in_plain_string():
    facts = scan("SELECT 'it''s; CREATE TABLE nope (id int)'; CREATE TABLE z (id int);")
    assert facts.tables_created == {"z"}


def test_function_body_is_not_scanned():
    sql = (
        "CREATE FUNCTION f() RETURNS TABLE (a int) AS $fn$ BEGIN "
        "CREATE TABLE hidden (id int); ALTER TABLE t ADD CONSTRAINT c CHECK (true); "
        "END $fn$ LANGUAGE plpgsql;"
    )
    facts = scan(sql)
    assert not facts.changes_schema


def test_do_block_body_is_scanned():
    sql = (
        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 't_key') "
        "THEN ALTER TABLE public.t ADD CONSTRAINT t_key UNIQUE (a); END IF; END $$;"
    )
    facts = scan(sql)
    assert facts.constraints_added == {"t_key"}
    assert facts.tables_altered == {"t"}


def test_block_comments_including_nested_are_ignored():
    sql = (
        "/* ALTER TABLE a ADD CONSTRAINT c CHECK (true); /* nested */ still comment */\n"
        "CREATE INDEX IF NOT EXISTS i ON t (a);"
    )
    facts = scan(sql)
    assert facts.constraints_added == set()
    assert facts.indexes_added == {"i"}


def test_line_comment_in_the_middle_of_a_statement():
    facts = scan("ALTER TABLE public.t -- why\n  ADD CONSTRAINT c CHECK (x > 0);")
    assert facts.constraints_added == {"c"}


def test_lowercase_sql():
    facts = scan("alter table public.t add constraint c check (x > 0); create index if not exists i on t (x);")
    assert facts.constraints_added == {"c"}
    assert facts.indexes_added == {"i"}


def test_multi_action_alter_table():
    facts = scan(
        "ALTER TABLE t ADD CONSTRAINT a CHECK (x > 0), ADD CONSTRAINT b UNIQUE (y), "
        "DROP CONSTRAINT IF EXISTS old;"
    )
    assert facts.constraints_added == {"a", "b"}
    assert facts.constraints_dropped == {"old"}


def test_rls_and_owner_changes_are_not_structural():
    facts = scan(
        "ALTER TABLE public.t ENABLE ROW LEVEL SECURITY;"
        "ALTER TABLE public.t OWNER TO postgres;"
        "ALTER TABLE public.t VALIDATE CONSTRAINT c;"
    )
    assert facts.tables_altered == set()
    assert not facts.changes_schema


def test_column_changes_are_structural():
    facts = scan(
        "ALTER TABLE a ALTER COLUMN x DROP NOT NULL;"
        "ALTER TABLE b RENAME COLUMN x TO y;"
        "ALTER TABLE c DROP COLUMN IF EXISTS z;"
        "ALTER TABLE d ADD COLUMN IF NOT EXISTS w int;"
    )
    assert facts.tables_altered == {"a", "b", "c", "d"}


@pytest.mark.parametrize(
    "action",
    ["ADD CHECK (x > 0)", "ADD UNIQUE (a)", "ADD PRIMARY KEY (id)", "ADD FOREIGN KEY (a) REFERENCES u (id)"],
)
def test_unnamed_table_constraint_is_reported(action):
    facts = scan(f"ALTER TABLE public.t {action};")
    assert facts.unnamed_constraints == ("t",)


def test_inline_column_constraint_on_add_column_is_not_unnamed():
    facts = scan("ALTER TABLE t ADD COLUMN IF NOT EXISTS y int UNIQUE;")
    assert facts.unnamed_constraints == ()


def test_quoted_identifiers_keep_their_case():
    facts = scan('ALTER TABLE "public"."T" ADD CONSTRAINT "My_C" CHECK (true);')
    assert facts.tables_altered == {"T"}
    assert facts.constraints_added == {"My_C"}


def test_non_public_schema_is_kept():
    facts = scan("ALTER TABLE storage.objects ADD CONSTRAINT c CHECK (true);")
    assert facts.tables_altered == {"storage.objects"}


def test_only_and_if_exists_are_skipped():
    facts = scan("ALTER TABLE IF EXISTS ONLY public.t ADD CONSTRAINT c CHECK (true);")
    assert facts.tables_altered == {"t"}


def test_drop_table_list():
    facts = scan("DROP TABLE IF EXISTS public.a, public.b CASCADE;")
    assert facts.tables_dropped == {"a", "b"}


def test_drop_index():
    facts = scan("DROP INDEX IF EXISTS public.i1;")
    assert facts.indexes_dropped == {"i1"}


def test_create_unique_index_concurrently_on_only():
    facts = scan("CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS i ON ONLY public.t (a);")
    assert facts.indexes_added == {"i"}


def test_table_rename_is_touched_under_its_new_name():
    facts = scan("ALTER TABLE public.old_name RENAME TO new_name;")
    assert facts.tables_renamed == {("old_name", "new_name")}
    assert facts.tables_touched == {"new_name"}


def test_constraint_word_inside_a_create_table_string_is_ignored():
    facts = scan(
        "CREATE TABLE IF NOT EXISTS public.x ("
        " note text DEFAULT 'CONSTRAINT fake CHECK (1)',"
        " CONSTRAINT real_one CHECK (note <> ''));"
    )
    assert facts.constraints_added == {"real_one"}


def test_comment_on_constraint_is_not_an_addition():
    facts = scan("COMMENT ON CONSTRAINT c ON public.t IS 'x';")
    assert not facts.changes_schema


def test_create_view_is_a_schema_change():
    facts = scan("CREATE OR REPLACE VIEW public.cyl_plants_extended AS SELECT 1 AS id;")
    assert facts.views_created == {"cyl_plants_extended"}
    assert facts.tables_touched == {"cyl_plants_extended"}
    assert facts.changes_schema


def test_create_materialized_view():
    facts = scan("CREATE MATERIALIZED VIEW IF NOT EXISTS public.mv AS SELECT 1;")
    assert facts.views_created == {"mv"}


def test_drop_view_is_a_schema_change():
    facts = scan("DROP VIEW IF EXISTS public.v, public.w; DROP MATERIALIZED VIEW mv;")
    assert facts.views_dropped == {"v", "w", "mv"}
    assert facts.changes_schema


def test_view_inside_a_function_body_is_ignored():
    facts = scan(
        "CREATE FUNCTION f() RETURNS void AS $$ BEGIN CREATE VIEW hidden AS SELECT 1; END $$ "
        "LANGUAGE plpgsql;"
    )
    assert not facts.changes_schema


def test_view_rename_is_touched_under_its_new_name():
    facts = scan("ALTER VIEW IF EXISTS public.old_v RENAME TO new_v; ALTER MATERIALIZED VIEW mv RENAME TO mv2;")
    assert facts.views_renamed == {("old_v", "new_v"), ("mv", "mv2")}
    assert facts.tables_touched == {"new_v", "mv2"}
    assert facts.changes_schema


@pytest.mark.parametrize(
    "sql",
    [
        "ALTER VIEW public.v RENAME COLUMN a TO b;",
        "ALTER VIEW IF EXISTS public.v RENAME a TO b;",
        'ALTER MATERIALIZED VIEW IF EXISTS public.v RENAME COLUMN "A" TO b;',
    ],
)
def test_view_column_rename_is_an_alteration(sql):
    facts = scan(sql)
    assert facts.views_altered == {"v"}
    assert facts.tables_touched == {"v"}


def test_alter_table_rename_on_a_view_counts_as_a_rename():
    assert scan("ALTER TABLE public.v RENAME TO w;").tables_touched == {"w"}


@pytest.mark.parametrize(
    "sql",
    [
        "ALTER VIEW public.v OWNER TO postgres;",
        "ALTER VIEW public.v SET (security_invoker = true);",
        "ALTER VIEW public.v ALTER COLUMN a SET DEFAULT 1;",
        "COMMENT ON VIEW public.v IS 'x';",
        "GRANT SELECT ON public.v TO authenticated;",
        "REFRESH MATERIALIZED VIEW public.mv;",
    ],
)
def test_view_changes_the_diagram_does_not_draw_are_not_schema_changes(sql):
    assert not scan(sql).changes_schema


@pytest.mark.parametrize(
    "sql, created",
    [
        ('CREATE VIEW public."Odd View" AS SELECT 1;', "Odd View"),
        ("CREATE OR REPLACE VIEW public.v WITH (security_invoker = true) AS SELECT 1;", "v"),
        ("CREATE VIEW public.v (a, b) AS SELECT 1, 2;", "v"),
        ("CREATE VIEW public.v AS SELECT 'drop table x' AS note;", "v"),
        ("DO $$ BEGIN CREATE VIEW public.v AS SELECT 1; END $$;", "v"),
    ],
)
def test_created_view_is_named(sql, created):
    assert scan(sql).views_created == {created}


def test_drop_view_cascade_names_each_view():
    assert scan("DROP VIEW IF EXISTS public.a, public.b CASCADE;").views_dropped == {"a", "b"}


def test_scan_files_unions_facts(tmp_path):
    a = tmp_path / "a.sql"
    b = tmp_path / "b.sql"
    c = tmp_path / "c.sql"
    a.write_text("CREATE TABLE x (id int);")
    b.write_text("ALTER TABLE y ADD CONSTRAINT c CHECK (true);")
    c.write_text("CREATE VIEW v AS SELECT 1; DROP VIEW w; ALTER VIEW o RENAME TO n; ALTER VIEW z RENAME a TO b;")
    facts = migration_sql.scan_files([a, b, c])
    assert facts.tables_touched == {"x", "y", "v", "n", "z"}
    assert facts.constraints_added == {"c"}
    assert facts.views_created == {"v"} and facts.views_dropped == {"w"}
    assert facts.views_renamed == {("o", "n")} and facts.views_altered == {"z"}
