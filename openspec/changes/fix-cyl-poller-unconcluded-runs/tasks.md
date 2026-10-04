Each "Red" section is written and run before its "Green" section.
- Save each red run's output to the scratchpad and paste it into the PR body.
- Don't create a standalone red commit: no workflow runs on a feature-branch push.
- Each red section lists which tests are expected to fail and which are guards expected to pass
  already.

## 1. Red: SQL (integration, real Postgres via `pg_conn`)

New file `tests/integration/test_cyl_pipeline_run_workflows.py`. It reuses `_seed_run` /
`_seed_scan` helpers from `test_cyl_pipeline_status_polling.py` and `test_cyl_writeback_rpc.py`;
copy them only if importing them would create a cycle.

- [ ] 1.1 `test_record_phase_inserts_a_first_terminal_phase`: returns `true`; the row holds
      `(run, 'wf-a', 'Succeeded')`.
- [ ] 1.2 `test_record_phase_same_phase_is_a_noop`: second call returns `false`; `observed_at`
      unchanged.
- [ ] 1.3 `test_record_phase_different_phase_replaces_and_advances_observed_at`.
- [ ] 1.4 `test_record_phase_unknown_workflow_for_run_writes_nothing`. Cover both a name no row
      carries and a name another run's row carries.
- [ ] 1.5 `test_record_phase_rejects_non_terminal_phase`: parametrize over `'Running'`,
      `'Pending'`, `''` and `NULL`.
- [ ] 1.6 `test_record_phase_execute_and_table_write_grants`. Check EXECUTE is false for `anon`,
      `authenticated`, `PUBLIC`, `bloom_user`, `bloom_writer` and `bloom_admin`, and true for
      `bloom_workflows`. Check `bloom_workflows` has no INSERT/UPDATE/DELETE on the table, and
      SELECT is true for `bloom_workflows`, `bloom_user` and `bloom_agent`.
- [ ] 1.7 `test_run_workflows_migration_is_idempotent` and
      `test_run_workflows_rollback_restores_previous_status_rpc`. Follow the existing idempotency
      and rollback tests (`test_cyl_pipeline_status_polling.py` L368–L428).

In `tests/integration/test_cyl_pipeline_status_polling.py`:
- [ ] 1.8 Replace `test_update_accepts_partial_as_a_source_state_and_advances_completed_at`
      (L201) with `test_dispatch_settled_partial_is_confirmed_once_then_final`.
  - First `'partial'` call: sets `completed_at` and `poller_concluded_at`.
  - Second call: parametrized over `'partial'`, `'failed'`, `'complete'` and `'running'`, with
    different counts. It changes nothing.
- [ ] 1.9 `test_terminal_write_stamps_poller_concluded_at_and_running_does_not`.
- [ ] 1.10 `test_concluded_complete_or_failed_run_is_untouched`. This is a guard: the existing
      L156/L166 tests should already pass; add `poller_concluded_at` to their assertions.
- [ ] 1.11 Full-flow guard in `test_cyl_writeback_rpc.py`, next to L1013–1100:
  - reconcile, then status write `'partial'`, then a later `'failed'` write is a no-op;
  - counts stay as first written.

**Expected red:** 1.1–1.9 and 1.11 (the table, RPC and column don't exist).
**Expected green:** 1.10's original assertions.

## 2. Green: migration

Use the `database-migration` skill.

- [ ] 2.1 `supabase/migrations/<ts>_add_cyl_pipeline_run_workflows.sql`, written idempotent
      (`IF NOT EXISTS`, `DROP POLICY IF EXISTS`, `CREATE OR REPLACE`). It contains:
  - the table, RLS policies and grants (design D1);
  - `record_cyl_pipeline_workflow_phase`, with its triple REVOKE and a single GRANT;
  - `ALTER TABLE cyl_pipeline_runs ADD COLUMN IF NOT EXISTS poller_concluded_at TIMESTAMPTZ`;
  - `CREATE OR REPLACE update_cyl_pipeline_run_status`, same signature, with the D5 source guard
    and stamping. Re-issue its grants.
- [ ] 2.2 `supabase/rollbacks/<ts>_add_cyl_pipeline_run_workflows_rollback.sql`. It:
  - restores the `20260912111000` body of `update_cyl_pipeline_run_status`;
  - drops the RPC and the table;
  - drops the column.
- [ ] 2.3 Hand-edit `web/lib/database.types.ts`:
  - `poller_concluded_at` on `cyl_pipeline_runs` Row/Insert/Update;
  - the `cyl_pipeline_run_workflows` table;
  - the RPC's Args/Returns.

  Do **not** bulk-regenerate the file.
- [ ] 2.4 Run `scripts/lint_migrations.sh` (or `make lint-migrations`) and §1's tests. All green.

## 3. Red: `k8s_client`

All in `services/workflows/tests/test_k8s_client.py`. Use `_FakeResp(404, payload, text=...)`.

- [ ] 3.1 `test_get_workflow_status_returns_none_on_verified_not_found`: a body that is a `Status`
      with `reason: NotFound` and details `{name, kind: workflows, group: argoproj.io}`. This
      replaces the empty-body version at L1009.
- [ ] 3.2 `test_get_workflow_status_raises_on_unverified_404`. Parametrize over:
  - an HTML text body;
  - `{}`;
  - `reason: "Forbidden"`;
  - `details.name` set to another name;
  - `details.name` absent (a missing CRD);
  - `details.kind: "pods"`;
  - `details.group` set to another group.

  Assert `K8sStatusError` with the generic message, and that the body appears only in the log.
- [ ] 3.3 Guard: `test_get_workflow_still_returns_none_on_any_404`, using the same parametrization
      as 3.2 (L1098 stays).

**Expected red:** 3.1 (only if the old test asserted nothing about the body; otherwise a guard)
and 3.2. **Expected green:** 3.3.

## 4. Green: `k8s_client`

- [ ] 4.1 Add a private `_is_verified_not_found(resp, name)` and have `get_workflow_status` use
      it, through a private fetch that `get_workflow` shares. `get_workflow`'s public behaviour
      does not change.
- [ ] 4.2 Update the docstrings. §3 is green, and `test_rnaseq_status_poller.py` is unchanged and
      green.

## 5. Red: poller

All in `services/workflows/tests/test_status_poller.py`.
- Extend `_FakeClient` with a recording `.rpc()`, or keep monkeypatching the new helpers
  (`_fetch_stored_phases`, `_record_phase`) the way the existing tests patch
  `_reconcile_unresolved_scans`.
- Give rows a `created_at`.
- Inject a clock for the grace tracker: `time.monotonic` and wall-clock `now` are patched, never
  slept.

- [ ] 5.1 `test_terminal_phase_is_recorded_once_and_used_after_gc` (spec scenario "A terminal phase is
      recorded once…"): two cycles; record called once; second cycle writes `'complete'`.
- [ ] 5.2 `test_same_stored_phase_makes_no_record_call`.
- [ ] 5.3 `test_live_phase_wins_over_stored_phase`: stored `Failed`, live `Running`, so the run
      is `'running'` and no record call is made.
- [ ] 5.4 `test_gone_never_seen_workflow_is_removed_after_grace`. Assert:
  - the cycle count;
  - the reconcile call carries the removed message;
  - `'failed'` with counts 2/1;
  - earlier cycles make no calls.
- [ ] 5.5 `test_removed_workflow_with_all_rows_written_counts_succeeded`, which writes
      `'complete'` with no reconcile call.
- [ ] 5.6 `test_not_found_within_ttl_of_row_creation_is_never_removed`.
- [ ] 5.7 `test_not_found_count_resets_on_any_other_result`. Cover both a `K8sStatusError` and a
      live phase in between.
- [ ] 5.8 `test_failed_record_call_keeps_live_phase_and_marks_cycle_unclean`, plus a `PGRST202`
      variant that stays clean.
- [ ] 5.9 `test_poller_concluded_partial_is_not_a_candidate` and
      `test_dispatch_settled_partial_is_still_a_candidate`. These replace L235's assertion that
      every partial run is a candidate.
- [ ] 5.10 `test_gcd_succeeded_sibling_keeps_partial_run_partial` (the rollup scenario).
- [ ] 5.11 `test_stored_phase_workflow_queued_rows_closed_while_running`.
- [ ] 5.12 `test_grace_and_ttl_env_resolution`: malformed or non-positive values fall back with a
      warning, matching the `_resolve_poll_interval` tests.
- [ ] 5.13 Update the tests that pin today's 404 behaviour so they describe an *unresolved*
      workflow (a verified NotFound, no stored phase, inside the grace period):
  - L169, L199, L533, L1041, L1617, L1656;
  - L1772 (`no-rollup` / `withheld-complete`).

  Their assertions stay the same; this is a guard that unresolved still behaves as 404 did.

**Expected red:** 5.1–5.12. **Expected green:** 5.13.

## 6. Green: poller

- [ ] 6.1 Resolvers for `WORKFLOWS_NOT_FOUND_GRACE_SECONDS` and `WORKFLOWS_K8S_TTL_SECONDS`.
- [ ] 6.2 `_fetch_stored_phases` and `_record_phase`.
- [ ] 6.3 The in-memory `_NotFoundTracker`, with reset and pruning.
- [ ] 6.4 Extend `_fetch_effective_phases`:
  - select `created_at`;
  - resolve effective phases;
  - close out removed workflows with the new message;
  - widen `settled_workflow_names` to stored and removed phases;
  - redefine `any_unknown` as "unresolved".
- [ ] 6.5 `_fetch_candidate_runs`: select `id, status, poller_concluded_at` and filter concluded
      `'partial'` rows in Python.
- [ ] 6.6 Update docstrings and comments that call a 404 permanent or "never settled"
      (`sweep_once` L494–510, `_fetch_effective_phases`, the module docstring).
- [ ] 6.7 §5 is green and the whole `services/workflows` suite is green.

## 7. UI: red, then green

- [ ] 7.1 Red, in `RunDetailLive.test.tsx`'s "re-run actions" block:
  - "does not offer a failed row whose late result is this run's": 3 failed rows, 1 with a
    late-result note. Expect "Re-run failed scans (2)" and that the submit carries the other 2 ids.
  - "offers every failed row when the latest-source lookup fails".
  - "leaves a late-result row out of Re-run scans without a result".
- [ ] 7.2 Green: in `RunDetailLive.tsx`, `failedIds` and `unresultedIds` exclude rows whose
      `lateResultNote` is non-null (design D6).
- [ ] 7.3 Run the `web` unit tests for `cyl-pipeline-runs` and `lib/cyl-pipeline`. Green.

## 8. Config and docs

- [ ] 8.1 Pass `WORKFLOWS_K8S_TTL_SECONDS` to `cyl-status-poller` in `docker-compose.dev.yml` and
      `docker-compose.prod.yml`. Update the comments that call it submission-only. Leave
      `WORKFLOWS_NOT_FOUND_GRACE_SECONDS` unset, as with the poll interval.
- [ ] 8.2 `services/workflows/README.md`:
  - the poller and reconciliation section (L382–461);
  - replace the "render unknown" note (L489–497);
  - the env table (L620–627).
- [ ] 8.3 Check that the `BACKSTOP_MESSAGE` source-equality test in `failure-hints.test.ts` still
      passes. The poller's existing backstop text is unchanged. The removed-workflow message is a
      new `error_message` that the row already displays as-is; no new hint mapping is added.

## 9. Verification before the PR

- [ ] 9.1 `openspec validate fix-cyl-poller-unconcluded-runs --strict`.
- [ ] 9.2 `/pre-merge`, which runs lint and the full test suite, including integration tests
      against a fresh local DB.
- [ ] 9.3 Run a read-only query on staging and prod (see the `reference_bloom_dev_ssh` access
      notes). It lists candidate runs whose workflows the new poller will remove or already has
      stored, i.e. the runs expected to conclude after deploy. Record the run ids and expected
      outcomes in the PR body.
- [ ] 9.4 The PR body's **Schema changes** section shows the new table, the new column and the
      re-created RPC. Run `make pr-body-check BODY=<file>`.

## 10. After merge

- [ ] 10.1 Deploy to staging, then compare the runs listed in 9.3 with their actual status,
      counts and closed rows. Record the result in this file.
- [ ] 10.2 Confirm a fresh staging run concludes, and that its workflows appear in
      `cyl_pipeline_run_workflows` with their phases.
- [ ] 10.3 Archive `fix-cyl-writeback-retry-reconcile` **before** this change. Both MODIFY "A
      standalone poller periodically reconciles…", and this delta is written on top of that
      change's text.
- [ ] 10.4 Archive this change after promotion to main and the prod check (repeat 10.1 on prod).
