## 1. Scaffolding

- [x] 1.1 Worktree `.worktrees/cyl-noop-redelivery-900`, branch `fix/cyl-noop-redelivery-900`
  from `origin/staging` (fast-forwarded to `b6dfba9a`, which includes #989 and #992), upstream
  unset so a bare push cannot land on `staging`.
- [x] 1.2 Read bloom#900 and bloom#875 with their comments, the live RPC body
  (`20260930120100_stamp_cyl_trait_source_recipe_and_run.sql`), the recipe migration that added
  `cyl_trait_sources.scan_id` (`20260930120000`), and every unarchived delta on
  `cyl-trait-writeback`, `cyl-ingest-cli`, `cyl-batch-ingest-result` and `cyl-pipeline-ui`.
- [x] 1.3 Staging facts, read-only (2026-10-01): source 228 has `scan_id` 12894756; 0 of 91
  object-metadata sources lack `scan_id`; `cyl_scan_traits` is about 28.8M rows with no index
  leading on `source_id`; `cyl_pipeline_run_scans` is 1,565 rows.
- [x] 1.4 Decisions D1–D5 recorded in `design.md` (author's answers, 2026-10-01).

## 2. Archive-ordering hazard (handled in this PR; `openspec validate --strict` cannot see it)

- [x] 2.1 `fix-cyl-redelivery-blob-collision` MODIFIES `cyl-ingest-cli` "Re-ingest is a benign,
  distinctly-reported no-op" with text identical to the live spec. Its block is raised in this PR
  to this change's block, byte-identical, so whichever archives second changes nothing.
- [x] 2.2 `add-cyl-trait-recipe-key` ADDS `cyl-trait-writeback` "Write-back stamps each new source
  with its recipe, scan, Workflow and run", which said the RPC keeps "the fallback that resolves the
  scan from an existing run-scan row carrying this source's id". Raised in this PR to refer to the
  fallback specified in "Write-back RPC ingests a ResultEnvelope", true before and after.
- [x] 2.3 No other unarchived change touches the eight requirements this change modifies (grep of
  every `### Requirement:` heading under `openspec/changes/*/specs/`, excluding `archive/`). PR #988
  (`isolate-cyl-pipeline-environments`, open) modifies `cyl-pipeline-ui` "Starting pipeline runs can
  be switched off per environment" only — disjoint.
- [ ] 2.4 At archive time, re-run 2.3's grep and `diff` each modified block against
  `openspec/specs/…` after archiving; record the result here.

## 3. Tests first (red)

Every test in this section is written and run **before** §4, and must fail (or, for guards that
pin unchanged behaviour, pass) for the stated reason. Record the red run's output summary under
each item.

### 3a. RPC integration tests — new file `tests/integration/test_cyl_noop_redelivery_scan.py`

An autouse fixture applies this change's migration body (`sql_body(MIGRATION)`, BEGIN/COMMIT
stripped) on `pg_conn` at the start of each test; `pg_conn` rolls back, so the shared dev DB is
never changed (D6 of the author's answers). If the migration file does not exist yet, the fixture
fails (not skips), which is the red state. Helpers (`_seed_scan`, `_envelope`, `_call`,
`_seed_run_scan_for_writeback`, `_run_scan_status`, `_source_id`) are reused from
`tests/integration/test_cyl_writeback_rpc.py`.

- [ ] 3.1 `test_manual_origin_noop_is_marked_written` (bloom#900, manual shape): deliver with no
  workflow name; seed a `'queued'` row for the scan under `wf-b`; re-deliver the same envelope under
  `wf-b`. Assert `was_noop` true, `status_update_matched` true, `wf-b`'s row is
  `('written', <original source_id>)`, and the counts of `cyl_trait_sources`, `cyl_scan_traits`
  and `cyl_scan_intermediates` rows are unchanged by the re-delivery.
- [ ] 3.2 `test_hand_submitted_origin_noop_is_marked_written` (bloom#900 live shape, bloom#875's
  own reproduction): first delivery under `wf-a` with **no** run-scan rows for `wf-a`; then as 3.1.
- [ ] 3.3 `test_run_scan_lookup_is_the_backup_when_source_scan_is_null`: deliver under `wf-a` with a
  seeded row (stamping it), `UPDATE cyl_trait_sources SET scan_id = NULL` for that source, seed
  `wf-b`'s queued row, re-deliver under `wf-b` → `('written', source_id)`, matched true. Must pass
  before and after §4 (pins #880's path).
- [ ] 3.4 `test_noop_with_no_recorded_scan_and_no_carrying_row_reports_no_match`: manual-origin
  source with `scan_id` set to NULL, `wf-b` queued row for its scan → matched false, row still
  `('queued', None)`.
- [ ] 3.5 `test_noop_under_workflow_that_did_not_dispatch_the_scan_matches_nothing`: manual-origin
  source for scan S1; `wf-b` has a queued row only for a different scan S2 → matched false, S2's
  row unchanged.
- [ ] 3.6 `test_noop_does_not_resurrect_a_failed_row`: as 3.1 but `wf-b`'s row is `'failed'` →
  matched false, row stays `('failed', None)`.
- [ ] 3.7 `test_same_key_different_scan_noop_does_not_mark_the_other_scan`: manual-origin source
  for S1; re-deliver the same key with S2's `image_ids` under `wf-b`, which has a queued row for S2
  only → matched false, S2 row `('queued', None)`, no `cyl_scan_traits` rows for S2.
- [ ] 3.8 `test_noop_without_workflow_name_touches_no_run_scan_row`: re-delivery with no workflow
  name → `status_update_matched` null; no `cyl_pipeline_run_scans` row's `status`/`source_id`/
  `updated_at` changes.
- [ ] 3.9 `test_redefinition_keeps_acl_owner_and_single_overload`: after the body is applied, the
  EXECUTE ACL equals `{postgres, service_role, bloom_writer, bloom_admin, bloom_workflows}`
  (anon/authenticated absent), owner is `postgres`, and only the 2-arg overload exists.
- [ ] 3.10 `test_migration_body_is_idempotent`: applying the body twice succeeds and 3.1's shape
  still passes.
- [ ] 3.11 `test_rollback_restores_the_previous_behaviour`: apply this change's rollback body after
  the migration body; 3.1's shape now reports matched false (proves the rollback really restores
  the `20260930120100` behaviour), and 3.9's ACL/owner assertions still hold.
- [ ] 3.12 Red run: create 4.1's migration file first as an unmodified copy of the
  `20260930120100` function section (no fallback edit yet). 3.1 and 3.2 must fail on
  `status_update_matched` false; 3.3–3.10 must pass (they pin behaviour the fix keeps). Then make
  the D1 edit (4.1) and re-run.
- [ ] 3.13 Update `tests/integration/test_cyl_writeback_rpc.py`'s
  `test_noop_redelivery_under_never_dispatched_workflow_reports_no_match` docstring/name only if it
  now states something false (it seeds no `wf-orphan` row, so its assertion stays true); do not
  change its assertion.

### 3b. Migration-file unit tests — new file `tests/unit/test_cyl_noop_redelivery_migration_files.py`

- [ ] 3.14 `test_differs_from_previous_only_in_the_fallback`: `difflib` of the new migration's
  function body against `20260930120100`'s, from `CREATE OR REPLACE FUNCTION` through the final
  `GRANT`, equals an explicit expected set of removed/added lines (the fallback block only); the
  trailing `SELECT public.cyl_backfill_trait_source_recipe_identity();` is absent (D5).
- [ ] 3.15 `test_previous_is_the_newest_definition_before_this_one` and
  `test_this_is_the_newest_definition`.
- [ ] 3.16 `test_noop_branch_never_reads_trait_or_blob_tables`: the text between
  `IF v_was_noop THEN` and its `RETURN` contains neither `cyl_scan_traits` nor
  `cyl_scan_intermediates` (the timeout guard from design D1), and does contain
  `FROM public.cyl_trait_sources`.
- [ ] 3.17 `test_full_revoke_is_restated`: the migration and the rollback both carry
  `REVOKE EXECUTE … FROM PUBLIC, anon, authenticated` and the four-role GRANT.
- [ ] 3.18 `test_rollback_restores_previous_body_verbatim`.
- [ ] 3.19 Red run: all fail (files missing).

### 3c. bloomctl — `bloomcli/tests/test_cyl_ingest.py`

- [ ] 3.20 `test_ingest_one_envelope_unmatched_noop_is_a_failure_that_wrote_nothing`:
  `{**RESULT_NOOP, "status_update_matched": False}` with `ARGO_WORKFLOW_NAME` set → `failed`,
  `retriable` false, message contains `source_id=55` and "nothing was written", and contains
  neither "write-back succeeded" nor "written trait/blob data".
- [ ] 3.21 `test_cli_unmatched_noop_reports_already_ingested_then_fails`: same RPC result through
  `cyl ingest-result` → exit non-zero; output contains "Already ingested (no-op)" and the 3.20
  message; no "write-back succeeded".
- [ ] 3.22 `test_batch_unmatched_noop_only_failure_exits_zero`: a batch whose only failure is
  3.20's shape exits 0 and lists it under `FAILED` with the new message.
- [ ] 3.23 Guards that must stay green unchanged: `test_cli_reports_status_update_mismatch_as_a_failure`,
  `test_ingest_one_envelope_reports_status_update_mismatch_as_failed`,
  `…_status_mismatch_message_does_not_assume_a_single_cause` (both),
  `test_ingest_one_envelope_noop_redelivery_under_new_workflow_reports_skipped`,
  `test_ingest_one_envelope_noop_is_skipped`, `test_cli_noop_is_not_an_error`.
- [ ] 3.24 Red run: 3.20–3.22 fail on the current message.

### 3d. Web — vitest

- [ ] 3.25 `RunDetailLive.test.tsx`: replace the bloom#900 tests with
  "a failed no-result row on a scan with results shows no bloom#900 note or warning" — rows with
  write-back's no-result text and with the poller's backstop text, `cyl_scan_latest_source`
  present → no element contains "bloom#900", and `rerun-actions` has no note. Keep the
  likely-cause assertions of the existing stage-in test.
- [ ] 3.26 `failure-hints.test.ts`: delete the `isNoOpCandidate`, `NO_OP_NOTE`,
  `BACKSTOP_MESSAGE`/`WRITEBACK_NO_RESULT_MESSAGE` source-equality tests; keep `likelyCause`'s.
- [ ] 3.27 `RunScansTable.test.tsx`: drop `noOpNote` from fixtures and its rendering assertion.
- [ ] 3.28 Red run: 3.25 fails (the note still renders).

## 4. Implementation (green)

- [ ] 4.1 `supabase/migrations/<ts>_resolve_cyl_noop_redelivery_scan_from_source.sql`, `<ts>` after
  `20261001180000` and after the staging tip at commit time (re-check `scripts/lint_migrations.sh
  origin/staging` before pushing): the `20260930120100` body verbatim except the fallback block per
  design D1; owner, full REVOKE, GRANT; header naming bloom#900/#875 and the rollback path.
- [ ] 4.2 `supabase/rollbacks/<ts>_resolve_cyl_noop_redelivery_scan_from_source_rollback.sql`:
  the `20260930120100` body verbatim, owner, full REVOKE, GRANT; header says staging hot-apply only
  and names `supabase migration repair --status reverted <ts>`.
- [ ] 4.3 `bloomcli/src/bloomctl/cyl/ingest.py`: in both `ingest_one_envelope` and
  `cyl ingest-result`, branch the `status_update_matched is False` message on `was_noop`; the
  `was_noop: false` text is unchanged. Update the comment above the batch check, which assumes
  `was_noop=false`. Exit codes and `retriable` unchanged.
- [ ] 4.4 Web: delete `NO_OP_NOTE`, `isNoOpCandidate`, `BACKSTOP_MESSAGE`,
  `WRITEBACK_NO_RESULT_MESSAGE` from `failure-hints.ts`; `NO_OP_RERUN_WARNING`, `noOpNote`,
  `noOpAmongFailed` and the `note` prop use from `RunDetailLive.tsx`; `noOpNote` from
  `RunScansTable.tsx`. Leave the failed-row metadata/latest-source lookup as is (likely cause and
  "current in trait views" still use it).
- [ ] 4.5 `rg -n "bloom#900|NO_OP_|isNoOpCandidate|noOpNote" web bloomcli services` returns only
  the absence assertions of 3.25.
- [ ] 4.6 Run 3a–3d green; record counts.
- [ ] 4.7 Docs: `rg -n "900" docs openspec/specs` — update any statement that the UI note exists or
  that a manual-origin no-op ends `failed`. Archived changes are not edited.

## 5. Validation

- [ ] 5.1 `openspec validate fix-cyl-noop-redelivery-scan-resolution --strict`, and the same for
  `fix-cyl-redelivery-blob-collision` and `add-cyl-trait-recipe-key` (both edited here).
- [ ] 5.2 `scripts/lint_migrations.sh origin/staging`.
- [ ] 5.3 `/pre-merge`. Known Windows-only local failures (`test_env_defaults.py` `test_validator_*`,
  `test_weekly_backup.py` collection, `test_verify_env_parity`, some fastq/doctor/file-mode tests)
  are recorded, not fixed; CI (Linux) is authoritative.
- [ ] 5.4 `database.types.ts` untouched (no schema-shape change).

## 6. PR

- [ ] 6.1 `/pr-description`; body says "Part of #900, part of #875" — no closing keywords
  anywhere in commits or body (squash bodies come from commits; promotion is a merge commit).
- [ ] 6.2 Fill **Schema changes** (function redefinition only: no table, column, constraint or
  index change; ER snapshot unchanged); `make pr-body-check BODY=<file>`.
- [ ] 6.3 Push with `git push -u origin fix/cyl-noop-redelivery-900` (create the ref via the REST
  API first if the push 500s); open against `staging`.
- [ ] 6.4 If PR #988 merges first, merge `origin/staging` in (never rebase once review comments
  exist) and resolve the `failure-hints.ts` / `RunDetailLive.tsx` import conflict.

## 7. After merge and staging deploy (each step needs the author's go-ahead)

- [ ] 7.1 Read-only: `supabase_migrations.schema_migrations` has `<ts>`; the live
  `insert_cyl_result_envelope` source contains `FROM public.cyl_trait_sources` inside the no-op
  branch; its ACL is the sanctioned set.
- [ ] 7.2 Before each run: `kubectl get pods -n runai-busch-lab` for GPU contention; filter Bloom
  Workflows by `environment=staging`.
- [ ] 7.3 Acceptance A (bloom#900): Bloom-dispatched staging run over scan 12894756 (source 228).
  Before/after, read-only: `max(id)` and `count(*)` of `cyl_trait_sources`; count and value
  checksum of `cyl_scan_traits` for scan 12894756. Expect the run-scan row `('written', 228)`,
  no new source, unchanged traits, `failed_count` 0, and write-back logging the scan as skipped.
- [ ] 7.4 Acceptance B (bloom#875): a Bloom-dispatched re-run over one of scans 12894761–64 whose
  source is already carried by an earlier Bloom run's row (check read-only first). Expect
  `('written', <same source>)` and no new source.
- [ ] 7.5 Drafts for the author to approve before posting: evidence comments on #900 and #875.
  Close both by hand only after 7.3/7.4 (bloom#780).
- [ ] 7.6 Draft the `sleap-roots-pipeline` roadmap update (`docs/bloom-integration/roadmap.md`, the
  A4-PIPELINE-E2E-TEST / scans 289/577/1009 note) for the author; do not push.
- [ ] 7.7 The bloomctl message change (4.3) reaches the cluster only when the Argo templates'
  bloomctl image pin next moves; note it for the next pin bump (author's call, cross-repo).
- [ ] 7.8 Archive only after 7.3–7.5 and 2.4.
