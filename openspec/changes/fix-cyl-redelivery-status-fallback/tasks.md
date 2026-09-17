## 1. Scaffolding

- [x] 1.1 Branch `eberrigan/fix-cyl-redelivery-status-fallback-875` created from `origin/staging`
  in an isolated worktree (`.worktrees/fix-cyl-redelivery-status-fallback-875`) — the shared
  checkout was on an unrelated branch (`fix/cyl-refresh-statement-timeout`) belonging to another
  in-progress session.
- [x] 1.2 Read bloom#875, `fix-cyl-redelivery-blob-collision/design.md`'s Risks section, the
  current `insert_cyl_result_envelope` migration body, and both sibling changes' `cyl-ingest-cli`
  / `cyl-trait-writeback` spec deltas before writing anything, per the archive-ordering hazard.
- [x] 1.3 `proposal.md` + `design.md` scaffolded, documenting the fallback mechanism and the
  clause-by-clause archive-ordering reconciliation.

## 2. Tests first (red)

- [x] 2.1 In `tests/integration/test_cyl_writeback_rpc.py`, add
  `test_noop_redelivery_under_new_workflow_name_falls_back_to_scan_id`: seed one scan, deliver an
  envelope successfully under `argo_workflow_name="wf-a"` (asserting
  `status_update_matched is True` on that first call, reusing the existing pattern from
  `test_status_update_matched_on_noop_redelivery_success`), then seed a **second**
  `cyl_pipeline_run_scans` row for the *same* `scan_id` under `argo_workflow_name="wf-b"` via
  `_seed_run_scan_for_writeback`, and re-deliver the *same* envelope with
  `argo_workflow_name="wf-b"`. Assert `was_noop is True`, `status_update_matched is True`, and —
  via `_run_scan_status(cur, "wf-b", scan_id)` — that `"wf-b"`'s row is now `("written",
  <the original source_id>)`. This is the exact bloom#875 repro and must fail (red) against
  today's migration, since the no-op branch's `source_id`-keyed update can never match `"wf-b"`'s
  row (its `source_id` is `NULL`).
- [x] 2.2 Add `test_noop_redelivery_under_never_dispatched_workflow_reports_no_match`:
  same setup as `test_status_update_matched_false_when_no_matching_row_at_all` but on the no-op
  path — deliver once with no `argo_workflow_name` (so no `cyl_pipeline_run_scans` row is ever
  stamped with this source's id), then re-deliver the same envelope with
  `argo_workflow_name="wf-orphan"`. Assert `was_noop is True` and `status_update_matched is
  False` — a negative control proving the fallback does not turn every unmatched no-op into a
  false success; it must stay green both before and after 3.1's implementation.
- [x] 2.3 Add `test_fallback_does_not_resurrect_a_failed_row_under_the_new_workflow_name`: seed
  `"wf-a"` (first delivery, real), mark `"wf-a"`'s row `'failed'`, seed `"wf-b"` for the same
  scan (fresh, `'queued'`), re-deliver the same envelope under `"wf-b"`. Assert the fallback
  finds `"wf-a"`'s row for scan-id resolution but its own update targets `"wf-b"`'s row (matched
  on `argo_workflow_name = "wf-b"`) and succeeds — `"wf-b"` becomes `'written'` — confirming the
  guard is per-row, not global to the source. This pins the "failed-status guard also blocks the
  fallback, under either workflow name" spec scenario's *unblocked* half; pair with 2.4 for the
  blocked half.
- [x] 2.4 Add `test_fallback_respects_the_failed_guard_on_its_own_target_row`: seed `"wf-a"`
  (first delivery, real), seed `"wf-b"` for the same scan and mark **`"wf-b"`'s own row**
  `'failed'`, then re-deliver under `"wf-b"`. Assert the fallback still resolves the scan id from
  `"wf-a"`'s row, but its update — now targeting `"wf-b"`'s `'failed'` row — matches zero rows,
  so `status_update_matched is False` and `"wf-b"`'s row stays `'failed'`. Negative control: this
  must stay green after 3.1 (the fallback's own `AND status != 'failed'` guard, not the primary
  update's, is what's being pinned here) — verify that claim directly, not just by inspection: at
  3.5, temporarily delete only the fallback's own `AND status != 'failed'` clause from the
  applied migration (leaving the primary update's guard intact) and confirm 2.4 alone goes red,
  then restore it and re-apply via `make migrate-local` before continuing. This is what makes 2.4
  pinned to the fallback's guard specifically, per `/review-openspec`'s TDD-review finding that an
  unverified negative control can be "green for the wrong reason" both before and after 3.1.
  **Done**: confirmed live — with the guard clause stripped, only this test flipped to red (the
  other three stayed green), pinning it to the fallback's own guard specifically.
- [x] 2.5 Run the four new tests against the **unmodified** migration and confirm 2.1 fails while
  2.2/2.3/2.4 already pass (2.3 may also fail if the fallback doesn't exist yet at all, since
  there's no fallback to hit the guard on — confirm which, and record the actual red/green split
  observed, since the goal is "the new tests correctly discriminate presence/absence of the
  fallback", not a specific pre-assigned split). **Observed**: 2.1 and 2.3 failed red (both need
  the fallback to exist at all — 2.3's "unblocked half" scenario has no row for the fallback to
  even attempt matching without it); 2.2 and 2.4 passed trivially (no fallback exists, so nothing
  can violate either negative control yet).

## 3. Implementation (green)

- [x] 3.1 New migration `supabase/migrations/20260917140000_fix_cyl_redelivery_status_fallback.sql`
  (timestamp `20260917140000` > `20260916120000`, confirmed greater than `origin/staging`'s
  latest at authoring time): `CREATE OR REPLACE FUNCTION
  public.insert_cyl_result_envelope(jsonb, text)` — same signature, full body copied from the
  current function with step 5's no-op branch gaining: after the primary
  `(argo_workflow_name, source_id)`-keyed `UPDATE` and its `GET DIAGNOSTICS`, `IF v_status_rows =
  0 THEN` look up `scan_id` from an existing `cyl_pipeline_run_scans` row with `source_id =
  v_source_id` (`SELECT ... LIMIT 1`); `IF FOUND THEN` run the fallback `UPDATE` scoped to
  `argo_workflow_name = p_argo_workflow_name AND scan_id = <looked-up id> AND status !=
  'failed'`, re-`GET DIAGNOSTICS`. Re-assert owner + `EXECUTE` grants (belt-and-suspenders; a
  same-signature `CREATE OR REPLACE` doesn't drop them, but every prior migration in this file
  re-asserts explicitly).
- [x] 3.2 Paired rollback `supabase/rollbacks/20260917140000_fix_cyl_redelivery_status_fallback_rollback.sql`:
  `CREATE OR REPLACE` back to the prior body (no fallback block), also same-signature.
- [x] 3.3 `make migrate-local` — the `supabase` CLI binary is not on `PATH` in this environment;
  used `npx --yes supabase db push` (v2.104.0), which then failed because this shared,
  27-hour-old local dev container's `supabase_migrations.schema_migrations` tracking table was
  empty/out of sync with what's actually applied (pre-existing dev-stack drift, not caused by
  this change — matches `project_dev_stack_drift_2026_09_01`). Applied just the new migration
  file directly via `docker compose exec ... psql < supabase/migrations/20260917140000_....sql`
  instead — safe, since it's a same-signature `CREATE OR REPLACE` with no dependency on the
  tracking table. Confirmed applied (clean `BEGIN`/`CREATE FUNCTION`/`ALTER FUNCTION`/`REVOKE`/
  `GRANT`/`COMMIT`).
- [x] 3.4 Add the migration-body precedent pair this file already establishes for a
  function-body-only change (`test_a3_migration_body_is_idempotent` /
  `test_a3_rollback_restores_strict_a2`, `test_scan_status_migration_body_is_idempotent`):
  `test_redelivery_status_fallback_migration_is_idempotent` (loads 3.1's raw `.sql` from disk,
  applies it twice in an uncommitted transaction, asserts no error and the same resulting
  function behavior) and `test_redelivery_status_fallback_rollback_restores_prior_body` (applies
  3.1 then 3.2, asserts the function's behavior reverts to today's — no fallback match under a
  new workflow name), so rollback safety is tested, not only asserted in `design.md`.
- [x] 3.5 Run the four tests from Section 2 plus 3.4's two migration-body tests against the newly
  migrated local Postgres: all six green, including 2.1 (the repro) now passing. Isolation check
  for 2.4 (per its own instruction above): applied a variant migration with only the fallback's
  own `AND status != 'failed'` clause stripped — only `test_fallback_respects_the_failed_guard_
  on_its_own_target_row` (2.4) flipped to red, the other three (2.1/2.2/2.3) stayed green,
  confirming 2.4 is pinned to that specific clause. Restored the real migration afterward.
- [x] 3.6 `cd bloomcli && uv run --extra test pytest tests/ -m "not integration" -v` — 908
  passed, 7 skipped, 13 failed. All 13 failures are pre-existing Windows/POSIX file-permission
  tests (`test_credentials.py`, `test_errors.py`'s log-rotation/owner-only-perms tests,
  `test_download_hardening.py`'s symlink test, etc.) — none reference `status_update_matched`,
  `was_noop`, or construct a real RPC call, confirming this change (zero `bloomctl` code edits)
  introduces no regression. This also transitively covers the `cyl-ingest-cli` spec delta's new
  "Re-delivery under a new ARGO_WORKFLOW_NAME is reported as a benign no-op" scenario:
  `ingest_one_envelope` only branches on the `status_update_matched`/`was_noop` booleans the RPC
  returns, and the existing `test_ingest_one_envelope_status_update_matched_true_is_unaffected`
  unit test already pins "`status_update_matched: true` → reported ok" at the CLI layer; 3.5's
  RPC-level tests are what now make that boolean come back `true` in the case bloom#875
  describes. No new bloomcli test is needed for that scenario specifically.
- [x] 3.7 `uv run --extra test pytest tests/integration/test_cyl_writeback_rpc.py -v` — 105
  passed, 2 failed (`test_a7_migration_body_is_idempotent`,
  `test_a7_rollback_restores_strict_a3`) against a fresh `git fetch origin staging`'s latest.
  Both failures are pre-existing dev-stack data drift, unrelated to this change: this shared
  local Postgres has 41 stale `cyl_trait_sources` rows stamped `0.1.0a3` (from other sessions'
  prior test/dev activity), tripping the *a7 cutover migration's own* guard when THAT unrelated
  migration is re-applied fresh inside those two tests' uncommitted transactions — nothing to do
  with `insert_cyl_result_envelope`'s no-op branch this change touches. Every scenario this
  change's Risks section claims is preserved unchanged passed:
  `test_status_update_matched_false_on_noop_redelivery_after_already_failed`,
  `test_same_key_different_scan_short_circuits`,
  `test_status_update_matched_false_when_no_matching_row_at_all`.

## 4. Spec validation

- [x] 4.1 `openspec validate fix-cyl-redelivery-status-fallback --strict` — passes.
- [x] 4.2 Re-read both sibling changes' `cyl-ingest-cli` and `cyl-trait-writeback` delta files
  against this change's finished deltas — unchanged since `/review-openspec`'s review (Section
  4.3 below); the spec delta *files* themselves were correct as first written (the review's
  finding was about `design.md`'s/`proposal.md`'s *narrative*, not the delta content), and the
  migration's shipped fallback wording matches what `design.md` described at proposal time, so
  the clause-by-clause superset claims still hold verbatim.
- [x] 4.3 `/review-openspec` ran before implementation (5 parallel subagents: spec quality, TDD,
  CI/build, docs, git workflow). Findings applied: corrected a false "drops the bloom#875
  carve-out" claim in `proposal.md`/`design.md` (the carve-out sentence actually lives in a
  different, untouched sibling requirement), added the missing `make migrate-local` step, added
  the migration idempotency/rollback test pair, fixed a `GRANT`/`REVOKE` self-contradiction,
  added a pre-merge `lint_migrations.sh` re-check, resolved the CHANGELOG task, corrected a
  misattribution about which change introduced the no-op branch's `source_id`-keying, added the
  post-deploy sibling-annotation task (6.2a), and hardened 2.4's negative control with an
  explicit isolation check (done live in Section 3.5). Not re-run after implementation: the
  implementation matches the reviewed design faithfully (verified by 4.2), and a second full
  5-agent pass would be reviewing code the design already fully specified — `/review-pr` in
  Section 5.6 is the next real checkpoint, once there's an actual diff to review.

## 5. PR

Everything in this change (openspec scaffold, migration + rollback, tests, spec deltas) lands as
one commit on top of the scaffold commit from Section 1 — this repo's actual commit history on
both sibling changes never has a "tests only, red" commit separate from the implementation that
makes them pass (TDD's red/green split in Section 2/3 above is a local workflow discipline, not a
commit boundary); pushing Section 2's tests alone would show CI red for real, not because CI is
broken.

- [x] 5.1 `bloomcli/CHANGELOG.md` `[Unreleased]` → `### Fixed`: entry added, stating plainly the
  fix is server-side (the RPC) with no `bloomctl` code change, referencing bloom#875.
- [x] 5.2 `python scripts/check-uv-locks.py` — clean (all 5 services resolved, no lock drift).
- [x] 5.3 `scripts/lint_migrations.sh origin/staging` (freshly fetched) — "Migration lint passed
  (checked 1 new file(s) against origin/staging; latest base timestamp 20260916120000)." Because
  this check only knows about `staging`'s state at the moment it runs, and not about other open,
  unmerged migration PRs, re-run it once more immediately before merge (not only earlier in this
  flow) against a fresh `git fetch origin staging` — a sibling migration PR merging first with a
  colliding or later timestamp would only be caught by that final re-check.
- [x] 5.4 `/pre-merge`. This change touches only a migration + rollback,
  `tests/integration/test_cyl_writeback_rpc.py`, `bloomcli/CHANGELOG.md`, and the OpenSpec
  scaffold — confirmed via `git diff --stat` against `*uv.lock`, `*pyproject.toml`, `web/`,
  `langchain/`, `bloommcp/`, `services/`: zero matches. Per `/pre-merge`'s own "Quick Pre-Merge
  (Minimum)" section, none of the TypeScript/web-build, Python-dependency-audit, or
  bloommcp-oracle-test triggers apply, and a full `make prod-up` stack build/smoke would be
  testing services this change never touches. The two checks that DO apply — migration lint
  (5.3) and the affected integration suite (3.7) — are already green.
- [x] 5.5 `/pr-description` drafted to `.pr_body_875.md`; `make pr-body-check
  BODY=.pr_body_875.md` (invoked via `python`, not the shimmed `python3`) → "PR body check
  passed (schema section complete)." States plainly no `bloomctl`/application code changes;
  references bloom#875 with `Ref bloom#875` (no closing keyword, confirmed via
  `grep -inE '\b(closes?|fixe?s|resolves?)\b'` — the only hit is descriptive prose, not adjacent
  to `#875`). Caught and fixed the same mistake in the commit message itself (originally wrote
  "Closes bloom#875" — amended before push, unpushed local commit). PR not yet opened — needs
  your go-ahead to push and open (a visible, externally-reviewed action).
- [ ] 5.6 `/review-pr`.

## 6. Post-merge (do not archive until these clear)

- [ ] 6.1 Verify the migration applied on staging: re-run bloom#875's own reproduction query
  (`SELECT count(*) FROM cyl_pipeline_run_scans WHERE source_id IS NOT NULL AND status =
  'queued'` — still expected `0`, unaffected by this change) plus a direct check that the new
  function body (via `pg_get_functiondef`) contains the fallback block.
  This change has no cluster/Argo-image dependency (pure RPC), so — unlike #871 — there is no
  pin-bump or image-rebuild step blocking a staging verification.
- [ ] 6.2 Note in bloom#875 that a full live Argo re-test (dispatch, observe `failed_count`) is
  still blocked on minting fresh synthetic scan_ids (design.md's Verification section) — do not
  claim the live symptom is fixed until that separate work closes it; the SQL-level fix and its
  tests are what this change can honestly claim.
- [ ] 6.2a Once 6.1 confirms the migration is live on staging, add a short "RESOLVED — see
  fix-cyl-redelivery-status-fallback" pointer next to the two now-stale, time-bound sentences in
  `fix-cyl-redelivery-blob-collision`'s own files —
  `specs/cyl-ingest-cli/spec.md:49-53` ("...currently always does... tracked as bloom#875") and
  `design.md:153-155` ("Filed as bloom#875; until then..."). Annotate only; do not rewrite either
  sentence's normative text (that text is properly superseded whenever `fix-cyl-redelivery-blob-collision`
  is itself next revisited or archived — see this change's own `design.md` Risks section for why
  editing it from here would be a blind cross-change edit).
- [ ] 6.3 Do **not** archive `fix-cyl-pipeline-run-scan-status` or `fix-cyl-redelivery-blob-collision`
  as part of this change — both remain blocked on unrelated tasks in their own `tasks.md` (roadmap/
  issue-closing housekeeping; staging-grant + Argo-pin verification, respectively). When either
  is eventually archived, re-read this change's `design.md` Risks section first and apply the
  archive-ordering guidance there.
- [ ] 6.4 `/openspec:archive fix-cyl-redelivery-status-fallback` once 6.1 is confirmed.
