"""Text checks on the add-cyl-trait-recipe-key migration and rollback files.

These pin properties an integration test cannot see: the lock timeout, named and
guarded constraints, ownership, and that no migration scans cyl_scan_traits.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = REPO_ROOT / "supabase" / "migrations"
ROLLBACKS = REPO_ROOT / "supabase" / "rollbacks"


def _one(directory: Path, glob: str) -> Path:
    matches = sorted(directory.glob(glob))
    if not matches:
        pytest.skip(f"{glob} not written yet")
    return matches[-1]


def _code(path: Path) -> str:
    """The file with -- comments removed, so prose cannot satisfy or trip a check."""
    return "\n".join(
        re.sub(r"--.*$", "", line)
        for line in path.read_text(encoding="utf-8").splitlines()
    )


M1 = "*_add_cyl_trait_recipe_key.sql"
R1 = "*_add_cyl_trait_recipe_key_rollback.sql"


def test_m1_sets_lock_timeout_and_reloads_postgrest():
    code = _code(_one(MIGRATIONS, M1))
    assert re.search(r"SET\s+LOCAL\s+lock_timeout", code, re.I)
    assert re.search(r"NOTIFY\s+pgrst", code, re.I)


def test_m1_constraints_are_named_and_guarded():
    code = _code(_one(MIGRATIONS, M1))
    for check in (
        "cyl_trait_sources_recipe_key_format_check",
        "cyl_trait_sources_recipe_key_version_check",
    ):
        assert re.search(rf"DROP\s+CONSTRAINT\s+IF\s+EXISTS\s+{check}", code, re.I), (
            check
        )
        assert re.search(rf"ADD\s+CONSTRAINT\s+{check}\s+CHECK", code, re.I), check
    for fk in (
        "cyl_trait_sources_scan_id_fkey",
        "cyl_trait_sources_cyl_pipeline_run_id_fkey",
    ):
        assert re.search(rf"conname\s*=\s*'{fk}'", code, re.I), fk
        assert re.search(rf"ADD\s+CONSTRAINT\s+{fk}\s+FOREIGN\s+KEY", code, re.I), fk
    for idx in (
        "cyl_trait_sources_recipe_key_idx",
        "cyl_trait_sources_scan_id_idx",
        "cyl_pipeline_run_scans_argo_workflow_name_idx",
    ):
        assert re.search(rf"CREATE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+{idx}", code, re.I), (
            idx
        )


def test_m1_adds_no_inline_constraint_on_a_column():
    code = _code(_one(MIGRATIONS, M1))
    for stmt in re.findall(r"ADD\s+COLUMN[^,;]*", code, re.I):
        assert not re.search(r"\b(REFERENCES|CHECK)\b", stmt, re.I), stmt


def test_m1_never_reads_cyl_scan_traits():
    assert not re.search(r"\bcyl_scan_traits\b", _code(_one(MIGRATIONS, M1)))


def test_m1_functions_are_owned_by_postgres():
    code = _code(_one(MIGRATIONS, M1))
    created = re.findall(
        r"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+public\.(\w+)", code, re.I
    )
    assert set(created) == {
        "cyl_trait_recipe_payload_v1",
        "cyl_trait_recipe_key_v1",
        "cyl_backfill_trait_source_recipe_identity",
    }
    for fn in created:
        assert re.search(
            rf"ALTER\s+FUNCTION\s+public\.{fn}\([^)]*\)\s+OWNER\s+TO\s+postgres",
            code,
            re.I,
        ), fn


def test_every_update_in_m1_has_a_where():
    code = _code(_one(MIGRATIONS, M1))
    for stmt in re.findall(r"\bUPDATE\s+public\.\w+.*?;", code, re.I | re.S):
        assert re.search(r"\bWHERE\b", stmt, re.I), stmt


def test_r1_guard_runs_before_any_drop():
    code = _code(_one(ROLLBACKS, R1))
    guard = re.search(r"RAISE\s+EXCEPTION", code, re.I)
    first_drop = re.search(r"\b(DROP|ALTER\s+TABLE)\b", code, re.I)
    assert guard and first_drop and guard.start() < first_drop.start()


# --------------------------------------------------------------------------- #
# Migration 2: write-back stamping
# --------------------------------------------------------------------------- #

M2 = "*_stamp_cyl_trait_source_recipe_and_run.sql"
R2 = "*_stamp_cyl_trait_source_recipe_and_run_rollback.sql"
A9 = MIGRATIONS / "20260928130000_cyl_writeback_contract_a9.sql"
RPC_REGION_START = "CREATE OR REPLACE FUNCTION public.insert_cyl_result_envelope("
RPC_REGION_END = "    TO bloom_writer, service_role, bloom_admin, bloom_workflows;"


def _rpc_region(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith(RPC_REGION_START)]
    ends = [i for i, line in enumerate(lines) if line == RPC_REGION_END]
    assert len(starts) == 1 and len(ends) == 1, (path.name, starts, ends)
    return lines[starts[0] : ends[0] + 1]


def _normalized(lines: list[str]) -> list[str]:
    """Comments stripped, whitespace collapsed, blank lines dropped."""
    out = []
    for line in lines:
        code = " ".join(line.split("--", 1)[0].split())
        if code:
            out.append(code)
    return out


# The only lines migration 2 may remove from, or add to, the a9 function region.
M2_REMOVED = [
    "INSERT INTO public.cyl_trait_sources (name, metadata, idempotency_key)",
    "VALUES (v_name, prov, v_idem)",
    "REVOKE EXECUTE ON FUNCTION public.insert_cyl_result_envelope(jsonb, text) FROM PUBLIC;",
]
M2_ADDED = [
    "v_run_id bigint;",
    "IF p_argo_workflow_name IS NOT NULL THEN",
    "SELECT CASE WHEN count(DISTINCT rs.run_id) = 1 THEN min(rs.run_id) END",
    "INTO v_run_id",
    "FROM public.cyl_pipeline_run_scans rs",
    "WHERE rs.argo_workflow_name = p_argo_workflow_name;",
    "END IF;",
    "INSERT INTO public.cyl_trait_sources",
    "(name, metadata, idempotency_key, recipe_key, recipe_key_version,",
    "argo_workflow_name, cyl_pipeline_run_id)",
    "VALUES (v_name, prov, v_idem, public.cyl_trait_recipe_key_v1(prov), 1,",
    "p_argo_workflow_name, v_run_id)",
    "UPDATE public.cyl_trait_sources SET scan_id = v_scan_id WHERE id = v_source_id;",
    "REVOKE EXECUTE ON FUNCTION public.insert_cyl_result_envelope(jsonb, text)",
    "FROM PUBLIC, anon, authenticated;",
]


def test_m2_differs_from_a9_only_in_stamping():
    import difflib

    a9 = _normalized(_rpc_region(A9))
    m2 = _normalized(_rpc_region(_one(MIGRATIONS, M2)))
    diff = list(difflib.ndiff(a9, m2))
    removed = [line[2:] for line in diff if line.startswith("- ")]
    added = [line[2:] for line in diff if line.startswith("+ ")]
    assert removed == M2_REMOVED
    assert added == M2_ADDED


def test_a9_is_the_newest_definition_before_m2():
    m2 = _one(MIGRATIONS, M2)
    definers = sorted(
        p.name
        for p in MIGRATIONS.glob("*.sql")
        if RPC_REGION_START in p.read_text(encoding="utf-8")
    )
    assert definers[definers.index(m2.name) - 1] == A9.name, definers


def test_m2_calls_the_backfill_and_sets_owner():
    code = _code(_one(MIGRATIONS, M2))
    assert re.search(
        r"ALTER\s+FUNCTION\s+public\.insert_cyl_result_envelope\(jsonb,\s*text\)\s+OWNER\s+TO\s+postgres",
        code,
        re.I,
    )
    assert re.search(
        r"SELECT\s+public\.cyl_backfill_trait_source_recipe_identity\(\)", code, re.I
    )
    assert not re.search(r"DROP\s+FUNCTION", code, re.I)


def test_r2_restores_a9_region_verbatim():
    assert _rpc_region(_one(ROLLBACKS, R2)) == _rpc_region(A9)
    code = _code(_one(ROLLBACKS, R2))
    # 20260928130100's ACL, re-asserted outside the copied region.
    assert re.search(
        r"REVOKE\s+EXECUTE\s+ON\s+FUNCTION\s+public\.insert_cyl_result_envelope\(jsonb,\s*text\)"
        r"\s+FROM\s+PUBLIC,\s*anon,\s*authenticated",
        code,
        re.I,
    )


# --------------------------------------------------------------------------- #
# Migration 3: recipe-aware reads
# --------------------------------------------------------------------------- #

M3 = "*_add_cyl_trait_recipe_reads.sql"
R3 = "*_add_cyl_trait_recipe_reads_rollback.sql"
GET_TRAITS_BASE = MIGRATIONS / "20260728000000_get_experiment_traits.sql"
GET_TRAITS_DEF = "FUNCTION public.get_experiment_traits("


def test_20260728000000_is_the_newest_get_experiment_traits_before_m3():
    m3 = _one(MIGRATIONS, M3)
    definers = sorted(
        p.name
        for p in MIGRATIONS.glob("*.sql")
        if re.search(
            r"CREATE\s+OR\s+REPLACE\s+" + re.escape(GET_TRAITS_DEF), _code(p), re.I
        )
    )
    assert definers[definers.index(m3.name) - 1] == GET_TRAITS_BASE.name, definers


def test_m3_replaces_rerunnably_and_reloads():
    code = _code(_one(MIGRATIONS, M3))
    assert re.search(
        r"DROP\s+FUNCTION\s+IF\s+EXISTS\s+public\.get_experiment_traits\(bigint,\s*bigint,\s*text\)",
        code,
        re.I,
    )
    created = re.findall(
        r"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+public\.(\w+)", code, re.I
    )
    assert set(created) == {
        "get_experiment_traits",
        "list_trait_recipes",
        "get_trait_recipe_coverage",
        "_cyl_trait_recipe_presence",
    }
    for fn in created:
        assert re.search(
            rf"ALTER\s+FUNCTION\s+public\.{fn}\([^)]*\)\s+OWNER\s+TO\s+postgres",
            code,
            re.I,
        ), fn
        assert re.search(
            rf"REVOKE\s+EXECUTE\s+ON\s+FUNCTION\s+public\.{fn}\([^)]*\)\s+FROM\s+PUBLIC,\s*anon",
            code,
            re.I,
        ), fn
    assert re.search(r"NOTIFY\s+pgrst", code, re.I)


def test_m3_adds_no_write_capability():
    code = _code(_one(MIGRATIONS, M3)).lower()
    assert "create policy" not in code
    assert not re.search(r"grant\s+[^;]*\b(insert|update|delete|all)\b", code)


def test_m3_presence_uses_index_probes():
    code = _code(_one(MIGRATIONS, M3))
    probes = re.findall(
        r"LATERAL\s*\(\s*SELECT\s+1\s+FROM\s+public\.cyl_scan_traits\s+\w+\s+WHERE\s+(.*?)LIMIT\s+1\s*\)",
        code,
        re.I | re.S,
    )
    assert any(re.search(r"source_id\s*=", p, re.I) for p in probes), probes
    assert any(re.search(r"source_id\s+IS\s+NULL", p, re.I) for p in probes), probes
    assert not re.search(
        r"EXISTS\s*\(\s*SELECT[^)]*FROM\s+public\.cyl_scan_traits", code, re.I
    )


def test_r3_restores_the_three_argument_function():
    code = _code(_one(ROLLBACKS, R3))
    assert re.search(
        r"DROP\s+FUNCTION\s+IF\s+EXISTS\s+public\.get_experiment_traits\(bigint,\s*bigint,\s*text,\s*text,\s*bigint\[\]\)",
        code,
        re.I,
    )
    assert "run_id_        text   DEFAULT NULL\n) RETURNS TABLE (" in _one(
        ROLLBACKS, R3
    ).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Migration 4: dataset recipe mode
# --------------------------------------------------------------------------- #

M4 = "*_add_cyl_dataset_recipe_mode.sql"
R4 = "*_add_cyl_dataset_recipe_mode_rollback.sql"
DATASET_BASE = (
    MIGRATIONS / "20240904033106_create_fix_create_cyl_dataset_function_again.sql"
)


def test_20240904033106_is_the_newest_create_cyl_dataset_before_m4():
    m4 = _one(MIGRATIONS, M4)
    definers = sorted(
        p.name
        for p in MIGRATIONS.glob("*.sql")
        if re.search(
            r"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+(public\.)?create_cyl_dataset\(",
            _code(p),
            re.I,
        )
    )
    assert definers[definers.index(m4.name) - 1] == DATASET_BASE.name, definers


def test_m4_shape():
    code = _code(_one(MIGRATIONS, M4))
    assert re.search(r"SET\s+LOCAL\s+lock_timeout", code, re.I)
    assert re.search(
        r"DROP\s+CONSTRAINT\s+IF\s+EXISTS\s+cyl_datasets_recipe_key_format_check",
        code,
        re.I,
    )
    assert re.search(
        r"ADD\s+CONSTRAINT\s+cyl_datasets_recipe_key_format_check\s+CHECK", code, re.I
    )
    assert re.search(
        r"DROP\s+FUNCTION\s+IF\s+EXISTS\s+public\.create_cyl_dataset\(text,\s*bigint,\s*bigint,\s*json,\s*json\)",
        code,
        re.I,
    )
    assert re.search(
        r"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+public\.create_cyl_dataset\(", code, re.I
    )
    assert re.search(
        r"ALTER\s+FUNCTION\s+public\.create_cyl_dataset\([^)]*\)\s+OWNER\s+TO\s+postgres",
        code,
        re.I,
    )
    assert re.search(r"SET\s+statement_timeout\s+TO\s+'0'", code, re.I)
    assert re.search(r"NOTIFY\s+pgrst", code, re.I)
    for stmt in re.findall(r"\bUPDATE\s+public\.\w+.*?;", code, re.I | re.S):
        assert re.search(r"\bWHERE\b", stmt, re.I), stmt


@pytest.mark.parametrize("glob, directory", [(M4, MIGRATIONS), (R4, ROLLBACKS)])
def test_no_database_or_role_settings(glob, directory):
    code = _code(_one(directory, glob))
    assert not re.search(r"\balter\s+(database|role)\b", code, re.I)


def test_r4_restores_the_base_function_region():
    def region(path):
        text = path.read_text(encoding="utf-8")
        start = text.index("CREATE OR REPLACE FUNCTION create_cyl_dataset(")
        end = text.index("set statement_timeout TO '0';", start)
        return text[start:end]

    assert region(_one(ROLLBACKS, R4)) == region(DATASET_BASE)
