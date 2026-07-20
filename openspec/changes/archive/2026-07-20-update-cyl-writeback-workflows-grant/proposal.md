## Why

A4 runs `bloomctl` from a cluster pod as the scoped `bloom_workflows` identity (role +
`is_workflows` → `custom_access_token_hook` login path landed in PR #391,
`supabase/migrations/20260716000000_create_workflows_role.sql`). That migration grants
`bloom_workflows` the **read** side A4 stage-in needs (`SELECT` on `cyl_images` /
`cyl_scans_extended`, Storage read on the `images` bucket). It does not touch the
**write-back** RPC: `insert_cyl_result_envelope`'s `EXECUTE` grant
(`supabase/migrations/20260706170000_cyl_writeback_contract_a3.sql:253-255`) still lists
only `bloom_writer, service_role, bloom_admin`. A pod signing in as `bloom_workflows` and
calling `bloomctl cyl ingest-result` hits `permission denied` on the RPC today.

This one-line grant was agreed with @blm3886 in bloom#404 (comment 2026-07-09), sequenced
to land after #391 — which merged 2026-07-17. Adding it unblocks bloom#398 (non-interactive
bloomctl auth) from being tested end-to-end against write-back.

## What Changes

- **New forward migration** granting `bloom_workflows` `EXECUTE` on
  `insert_cyl_result_envelope(jsonb)`, alongside the existing `bloom_writer`,
  `service_role`, `bloom_admin` grantees. Forward-only per this repo's convention —
  `20260706170000_cyl_writeback_contract_a3.sql` is already applied and MUST NOT be edited.
  No RLS policy or table changes: E's lockdown already makes the RPC the sole writer for
  every role, and `bloom_workflows` gets no other grant on the three write-back tables —
  this keeps it least-privilege (read for stage-in, execute-only for write-back).
- **Test update:** `tests/integration/test_cyl_writeback_rpc.py::test_execute_grants_are_exactly_the_sanctioned_roles`
  currently asserts `EXECUTE` holds for exactly `bloom_writer, service_role, bloom_admin`
  and not for `bloom_user, bloom_agent` — add `bloom_workflows` to the "should hold" list
  so the "exactly the sanctioned roles" claim stays accurate.
- **New test:** a behavioral test calling the RPC as `bloom_workflows` (`SET LOCAL ROLE`,
  same pattern as `test_direct_write_is_denied`) with a valid envelope and asserting it
  succeeds and writes rows — the catalog-permission check alone doesn't prove the RPC
  actually works for this role.
- **Test extension:** add `bloom_workflows` to `test_direct_write_is_denied`'s parametrized
  role list, proving the grant is execute-only — `bloom_workflows` still cannot `INSERT`
  directly into `cyl_trait_sources`/`cyl_scan_traits`/`cyl_scan_intermediates`. This is the
  negative proof behind the least-privilege claim below; without it that claim is only prose.

Out of scope (separate, larger follow-up per bloom#404): the `cyl_pipeline_runs` /
`cyl_pipeline_run_scans` run-tracking tables and their pgmq queue wiring. Out of scope
(bloom#398, separate change): the `bloomctl` CLI-side non-interactive auth path that will
consume this credential. No `design.md` — the mechanism (service-account `bloom_workflows`
role, plain `GRANT EXECUTE`) already exists as a merged precedent; there's no new
architectural decision to record here.

## Impact

- Affected specs: `cyl-trait-writeback` (MODIFIED: the write-back RPC's sanctioned-EXECUTE-roles requirement).
- Affected code:
  - `supabase/migrations/20260720000000_grant_bloom_workflows_writeback_rpc.sql` (new)
  - `tests/integration/test_cyl_writeback_rpc.py` (updated `test_execute_grants_are_exactly_the_sanctioned_roles`,
    extended `test_direct_write_is_denied` parametrize, new
    `test_bloom_workflows_can_call_the_writeback_rpc`)
  - `_WIKI/SUPABASE/README.md` (the "Write-back RPC" sanctioned-roles sentence, currently stale
    the moment this grant lands — precedent: task 6.2 of `add-cyl-writeback-rpc` updated the
    same file for the RPC's original grant list)
- Related, not modified here: bloom#398 (bloomctl auth, separate change), bloom#404
  (tracker for this grant + the still-open queue/tables follow-up),
  `talmolab/sleap-roots-pipeline#17` (credential provisioning, separate).
