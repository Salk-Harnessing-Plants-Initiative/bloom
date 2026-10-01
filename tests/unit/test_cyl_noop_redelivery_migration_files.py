"""Text checks on the fix-cyl-noop-redelivery-scan-resolution migration and rollback.

The migration re-creates insert_cyl_result_envelope from 20260930120100's body with only
the no-op fallback block changed (design D1, D5, D8). These pin what an integration test
cannot see: everything outside that block is byte-identical, the block's code is exactly
the intended code, its two false comments are gone, the lookup order, and the ACL.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = REPO_ROOT / "supabase" / "migrations"
ROLLBACKS = REPO_ROOT / "supabase" / "rollbacks"
PREV = MIGRATIONS / "20260930120100_stamp_cyl_trait_source_recipe_and_run.sql"
NEW_GLOB = "*_resolve_cyl_noop_redelivery_scan_from_source.sql"
ROLLBACK_GLOB = "*_resolve_cyl_noop_redelivery_scan_from_source_rollback.sql"

RPC_REGION_START = "CREATE OR REPLACE FUNCTION public.insert_cyl_result_envelope("
RPC_REGION_END = "    TO bloom_writer, service_role, bloom_admin, bloom_workflows;"
BLOCK_START = re.compile(r"^\s*-- bloom#875\b")
NOOP_RETURN = "        RETURN jsonb_build_object("


def _exactly_one(directory: Path, glob: str) -> Path:
    matches = sorted(directory.glob(glob))
    assert len(matches) == 1, f"expected exactly one {glob}, found {matches}"
    return matches[0]


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def _rpc_region(path: Path) -> list[str]:
    lines = _lines(path)
    starts = [i for i, line in enumerate(lines) if line.startswith(RPC_REGION_START)]
    ends = [i for i, line in enumerate(lines) if line == RPC_REGION_END]
    assert len(starts) == 1 and len(ends) == 1, (path.name, starts, ends)
    return lines[starts[0] : ends[0] + 1]


def _split(region: list[str]) -> tuple[list[str], list[str], list[str]]:
    """(before, fallback block, after). The block runs from the "-- bloom#875" comment
    to the END IF that closes `IF v_status_rows = 0 THEN`, i.e. up to (not including)
    the END IF just above the no-op branch's RETURN."""
    starts = [i for i, line in enumerate(region) if BLOCK_START.match(line)]
    assert len(starts) == 1, starts
    ret = region.index(NOOP_RETURN, starts[0])
    assert region[ret - 1] == "        END IF;", region[ret - 1]
    return region[: starts[0]], region[starts[0] : ret - 1], region[ret - 1 :]


def _normalized(lines: list[str]) -> list[str]:
    """Comments stripped, whitespace collapsed, blank lines dropped."""
    out = []
    for line in lines:
        code = " ".join(line.split("--", 1)[0].split())
        if code:
            out.append(code)
    return out


def _code(text: str) -> str:
    return "\n".join(re.sub(r"--.*$", "", line) for line in text.splitlines())


PREV_BLOCK_CODE = [
    "IF v_status_rows = 0 THEN",
    "SELECT scan_id INTO v_scan_id",
    "FROM public.cyl_pipeline_run_scans",
    "WHERE source_id = v_source_id",
    "LIMIT 1;",
    "IF v_scan_id IS NOT NULL THEN",
    "UPDATE public.cyl_pipeline_run_scans",
    "SET status = 'written',",
    "source_id = v_source_id,",
    "updated_at = now()",
    "WHERE argo_workflow_name = p_argo_workflow_name",
    "AND scan_id = v_scan_id",
    "AND status != 'failed';",
    "GET DIAGNOSTICS v_status_rows = ROW_COUNT;",
    "END IF;",
    "END IF;",
]
NEW_BLOCK_CODE = [
    "IF v_status_rows = 0 THEN",
    "SELECT scan_id INTO v_scan_id",
    "FROM public.cyl_trait_sources",
    "WHERE id = v_source_id;",
    "IF v_scan_id IS NULL THEN",
    "SELECT scan_id INTO v_scan_id",
    "FROM public.cyl_pipeline_run_scans",
    "WHERE source_id = v_source_id",
    "LIMIT 1;",
    "END IF;",
    "IF v_scan_id IS NOT NULL THEN",
    "UPDATE public.cyl_pipeline_run_scans",
    "SET status = 'written',",
    "source_id = v_source_id,",
    "updated_at = now()",
    "WHERE argo_workflow_name = p_argo_workflow_name",
    "AND scan_id = v_scan_id",
    "AND status != 'failed'",
    "AND (source_id IS NULL OR source_id = v_source_id);",
    "GET DIAGNOSTICS v_status_rows = ROW_COUNT;",
    "END IF;",
    "END IF;",
]


def test_differs_from_previous_only_in_the_fallback_block():
    prev_before, prev_block, prev_after = _split(_rpc_region(PREV))
    new_before, new_block, new_after = _split(
        _rpc_region(_exactly_one(MIGRATIONS, NEW_GLOB))
    )
    assert new_before == prev_before
    assert new_after == prev_after
    assert _normalized(prev_block) == PREV_BLOCK_CODE
    assert _normalized(new_block) == NEW_BLOCK_CODE


def _comment_text(lines: list[str]) -> str:
    """The block's -- comments as one whitespace-collapsed string."""
    parts = [line.split("--", 1)[1] for line in lines if "--" in line]
    return " ".join(" ".join(parts).split())


def test_the_two_false_fallback_comments_are_rewritten():
    _, prev_block, _ = _split(_rpc_region(PREV))
    _, new_block, _ = _split(_rpc_region(_exactly_one(MIGRATIONS, NEW_GLOB)))
    for phrase in (
        "source_id is written only by the non-no-op path",
        "the fallback correctly does nothing",
    ):
        assert phrase in _comment_text(prev_block), phrase  # the claim being retired
        assert phrase not in _comment_text(new_block), phrase


def test_previous_is_the_newest_definition_before_this_one():
    new = _exactly_one(MIGRATIONS, NEW_GLOB)
    definers = sorted(
        p.name
        for p in MIGRATIONS.glob("*.sql")
        if RPC_REGION_START in p.read_text(encoding="utf-8")
    )
    assert definers[definers.index(new.name) - 1] == PREV.name, definers


def test_noop_branch_reads_the_source_row_first():
    region = "\n".join(_rpc_region(_exactly_one(MIGRATIONS, NEW_GLOB)))
    code = _code(region)
    start = code.index("IF v_was_noop THEN")
    end = code.index("RETURN jsonb_build_object(", start)
    noop = " ".join(code[start:end].split())
    assert "cyl_scan_traits" not in noop and "cyl_scan_intermediates" not in noop
    source_first = noop.index("FROM public.cyl_trait_sources WHERE id = v_source_id")
    backup_if = noop.index("IF v_scan_id IS NULL THEN")
    backup = noop.index(
        "FROM public.cyl_pipeline_run_scans WHERE source_id = v_source_id"
    )
    assert source_first < backup_if < backup
    assert "AND (source_id IS NULL OR source_id = v_source_id);" in noop


def test_body_keeps_single_markers():
    region = "\n".join(_rpc_region(_exactly_one(MIGRATIONS, NEW_GLOB)))
    # test_noop_stamp_guard_detects_mutation and test_contract_migration_match.py read
    # these from pg_get_functiondef, which keeps comments.
    assert region.count("v_was_noop := true;") == 1
    assert region.count("pinned_version constant text :=") == 1


def _assert_owner_and_full_revoke(path: Path):
    code = _code(path.read_text(encoding="utf-8"))
    assert re.search(
        r"ALTER\s+FUNCTION\s+public\.insert_cyl_result_envelope\(jsonb,\s*text\)\s+OWNER\s+TO\s+postgres",
        code,
        re.I,
    )
    assert re.search(
        r"REVOKE\s+EXECUTE\s+ON\s+FUNCTION\s+public\.insert_cyl_result_envelope\(jsonb,\s*text\)"
        r"\s+FROM\s+PUBLIC,\s*anon,\s*authenticated",
        code,
        re.I,
    )
    assert re.search(
        r"GRANT\s+EXECUTE\s+ON\s+FUNCTION\s+public\.insert_cyl_result_envelope\(jsonb,\s*text\)"
        r"\s+TO\s+bloom_writer,\s*service_role,\s*bloom_admin,\s*bloom_workflows",
        code,
        re.I,
    )
    assert not re.search(r"DROP\s+FUNCTION", code, re.I)


def test_migration_restates_owner_and_full_revoke_without_backfill():
    path = _exactly_one(MIGRATIONS, NEW_GLOB)
    _assert_owner_and_full_revoke(path)
    assert "cyl_backfill_trait_source_recipe_identity" not in _code(
        path.read_text(encoding="utf-8")
    )


def test_rollback_restores_previous_body_verbatim():
    path = _exactly_one(ROLLBACKS, ROLLBACK_GLOB)
    assert _rpc_region(path) == _rpc_region(PREV)
    _assert_owner_and_full_revoke(path)
