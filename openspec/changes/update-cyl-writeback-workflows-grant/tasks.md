## 1. Tests first (red)

- [x] 1.1 Update `test_execute_grants_are_exactly_the_sanctioned_roles`
      (`tests/integration/test_cyl_writeback_rpc.py:614-627`): add `bloom_workflows` to the
      role list expected to hold `EXECUTE`. Run it against the current migrations (no new
      migration yet) — confirm it now **fails** (`bloom_workflows` doesn't have the grant),
      proving the test actually exercises the missing grant.
- [x] 1.2 Add `test_bloom_workflows_can_call_the_writeback_rpc`: seed a scan (`_seed_scan`,
      as `supabase_admin`), `SET LOCAL ROLE bloom_workflows`, call the RPC with a valid
      envelope (`_call`/`_envelope` helpers), and assert it succeeds (`was_noop is False`,
      correct `scan_id`/`trait_count`/`blob_count`) — checked before the transaction's
      rollback-based teardown, matching `test_valid_envelope_writes_source_traits_blobs`'s
      pattern. Confirm this **fails** with `permission denied for function
      insert_cyl_result_envelope` before the grant exists.
- [x] 1.3 Extend `test_direct_write_is_denied`'s `role` parametrize list
      (`tests/integration/test_cyl_writeback_rpc.py:657`) to include `"bloom_workflows"`,
      proving the grant is execute-only: `SET LOCAL ROLE bloom_workflows` then a direct
      `INSERT` into any of `cyl_trait_sources`/`cyl_scan_traits`/`cyl_scan_intermediates`
      still raises `InsufficientPrivilege`. This should already pass before the migration
      (`bloom_workflows` holds no table grants today) — run it once to confirm it isn't
      accidentally green for the wrong reason, then again after 2.1 to confirm no
      regression.

## 2. Documentation

- [x] 2.1 Update `_WIKI/SUPABASE/README.md`'s "Write-back RPC" subsection (line ~64, the
      sanctioned-EXECUTE-roles sentence) to include `bloom_workflows`, matching the
      precedent set by task 6.2 of `add-cyl-writeback-rpc` when the RPC's original grant
      list was documented there.

## 3. Migration (green)

- [x] 3.1 Add `supabase/migrations/20260720000000_grant_bloom_workflows_writeback_rpc.sql`:
      a single `GRANT EXECUTE ON FUNCTION public.insert_cyl_result_envelope(jsonb) TO
      bloom_workflows;` (idempotent — `GRANT` on an already-held privilege is a no-op, no
      `IF NOT EXISTS` needed). Header comment: cross-references bloom#404 and bloom#398,
      states forward-only (does not touch `20260706170000_cyl_writeback_contract_a3.sql`),
      and notes no other grant is added (least-privilege: read via #391, execute-only here).
- [x] 3.2 `make migrate-local` against the local dev stack; confirm all three tests from
      section 1 (1.1, 1.2, 1.3) now pass.

## 4. Validation

- [x] 4.1 `openspec validate update-cyl-writeback-workflows-grant --strict` passes.
- [x] 4.2 `uv run --extra test pytest tests/integration/test_cyl_writeback_rpc.py -v --tb=short`
      passes in full (no regressions to the other role-grant/lockdown assertions).
- [x] 4.3 `/pre-merge` clean (lint + full suite + OpenSpec validation). Full
      `tests/integration/` run: 281 passed, 13 skipped, 38 failed — all 38 pre-existing
      and unrelated (HTTP-endpoint tests needing the full `docker-compose.prod.yml`/Kong
      stack via `make prod-up`, which this narrow change didn't bring up; and
      `test_lint_migrations.py`'s WSL-bash/CRLF environment quirk, confirmed by running
      `scripts/lint_migrations.sh` directly, which passes). Zero failures in
      `test_cyl_writeback_rpc.py` (76/76 passed) or any other test touching
      `bloom_workflows`/cyl write-back.
