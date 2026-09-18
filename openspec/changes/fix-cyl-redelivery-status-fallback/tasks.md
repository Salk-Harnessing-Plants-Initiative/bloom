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
  <the original source_id>)`. This is the Bloom-dispatched-original shape of the bloom#875
  symptom (NOT the exact hand-submitted-original shape the issue's own live reproduction
  measured — see design.md's Verification section, corrected during `/review-pr`) and must fail
  (red) against today's migration, since the no-op branch's `source_id`-keyed update can never
  match `"wf-b"`'s row (its `source_id` is `NULL`).
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
- [x] 5.6 `/review-pr` on PR #880 (5 parallel subagents: code quality, testing strategy, data
  integrity, security, behavioral correctness). No BLOCKING correctness bugs in the shipped SQL
  itself — every adversarial scenario traced by hand against the actual code (three+ workflow
  chaining, concurrent re-delivery, empty-string workflow name, the bloom#875 repro end-to-end)
  held up. Real findings, all fixed (see Section 7): a genuine test gap the issue itself named,
  a fidelity gap between the PR's "byte-identical" claim and the actual retyped files, a
  spec-scenario/test mismatch, and a documentation gap for a pre-existing (not introduced here)
  constraint ambiguity.

## 6. Review findings applied (post `/review-pr`)

- [x] 6.1 **Fixed (testing, flagged BLOCKING):** bloom#875's own "Test gap" section explicitly
  asks for a `bloomcli`-level test combining `was_noop=True` + `status_update_matched=True`
  under `ARGO_WORKFLOW_NAME` — no existing test did (confirmed: `RESULT_NOOP` never carried
  `status_update_matched`, no test set the env var on a noop result). Added
  `test_ingest_one_envelope_noop_redelivery_under_new_workflow_reports_skipped` to
  `bloomcli/tests/test_cyl_ingest.py`; passes.
- [x] 6.2 **Fixed (code quality, IMPORTANT):** the new migration's and rollback's copied sections
  were retyped by hand rather than copied, silently flattening every em-dash to `--` and (in the
  rollback) dropping all 9 step-comments and rationale blocks. Rebuilt both files by literally
  extracting the exact prior-migration text (`sed`) and splicing in only the new fallback block —
  verified byte-identical to the extracted reference via `diff`. Re-applied to the local dev
  Postgres and re-ran the full suite: unchanged (105→107 passed after 6.3/6.4 below, same 2
  pre-existing unrelated failures).
- [x] 6.3 **Fixed (testing + behavioral-correctness, IMPORTANT/SUGGESTION):** added
  `test_fallback_finds_nothing_for_a_never_dispatched_workflow` (the new workflow's own row was
  never seeded at all, distinct from the existing "original had no workflow name" negative
  control) and `test_fallback_chains_across_a_third_workflow_redelivery` (a third re-delivery
  after the fallback has already run once, confirming the `LIMIT 1` over two now-existing
  same-source rows is safe by the single-scan-per-source invariant, not just by assertion).
- [x] 6.4 **Fixed (testing, IMPORTANT):** `cyl-trait-writeback/spec.md`'s "under either workflow
  name" scenario only ever described the *unblocked* half (2.3's shape). Split into two
  scenarios — one per half — plus two new scenarios matching 6.3's tests, keeping spec
  scenarios and tests 1:1.
- [x] 6.5 **Documented, not fixed — pre-existing, out of scope (behavioral-correctness,
  IMPORTANT):** `(argo_workflow_name, scan_id)` has no DB-level uniqueness (only
  `UNIQUE (run_id, scan_id)` exists); both the fallback's and step 9's identical `WHERE` shape
  inherit this. Not introduced by this change — step 9 has carried it since
  `fix-cyl-pipeline-run-scan-status`. Added a `design.md` Risks bullet recording it.
- [x] 6.6 Re-ran the full affected suites after all of the above: `tests/integration/
  test_cyl_writeback_rpc.py` (107 passed, same 2 pre-existing unrelated failures) and
  `bloomcli` `not integration` (909 passed, up from 908, same 13 pre-existing unrelated
  failures). Re-ran `openspec validate --strict` and `scripts/lint_migrations.sh
  origin/staging` (freshly fetched) — both clean.
- [x] 6.7 Push the fixes as a new commit (the first commit is already pushed to the open PR;
  per this session's git conventions, amend only unpushed local history).

## 7. Post-merge (do not archive until these clear)

- [ ] 7.1 Verify the migration applied on staging: re-run bloom#875's own reproduction query
  (`SELECT count(*) FROM cyl_pipeline_run_scans WHERE source_id IS NOT NULL AND status =
  'queued'` — still expected `0`, unaffected by this change) plus a direct check that the new
  function body (via `pg_get_functiondef`) contains the fallback block.
  This change has no cluster/Argo-image dependency (pure RPC), so — unlike #871 — there is no
  pin-bump or image-rebuild step blocking a staging verification.
- [ ] 7.2 Note in bloom#875 that a full live Argo re-test (dispatch, observe `failed_count`) is
  still blocked on minting fresh synthetic scan_ids (design.md's Verification section) — do not
  claim the live symptom is fixed until that separate work closes it; the SQL-level fix and its
  tests are what this change can honestly claim.
- [ ] 7.2a Once 7.1 confirms the migration is live on staging, add a short "RESOLVED — see
  fix-cyl-redelivery-status-fallback" pointer next to the two now-stale, time-bound sentences in
  `fix-cyl-redelivery-blob-collision`'s own files —
  `specs/cyl-ingest-cli/spec.md:49-53` ("...currently always does... tracked as bloom#875") and
  `design.md:153-155` ("Filed as bloom#875; until then..."). Annotate only; do not rewrite either
  sentence's normative text (that text is properly superseded whenever `fix-cyl-redelivery-blob-collision`
  is itself next revisited or archived — see this change's own `design.md` Risks section for why
  editing it from here would be a blind cross-change edit).
- [ ] 7.3 Do **not** archive `fix-cyl-pipeline-run-scan-status` or `fix-cyl-redelivery-blob-collision`
  as part of this change — both remain blocked on unrelated tasks in their own `tasks.md` (roadmap/
  issue-closing housekeeping; staging-grant + Argo-pin verification, respectively). When either
  is eventually archived, re-read this change's `design.md` Risks section first and apply the
  archive-ordering guidance there.
- [ ] 7.4 `/openspec:archive fix-cyl-redelivery-status-fallback` once 7.1 is confirmed.

## 8. Second review — scope-limit finding (eberrigan, PR #880 comment, 2026-09-17)

A human/peer-session review on the open PR independently re-verified the SQL diff and spec
coverage (both confirmed clean), then found that this change's own cited live evidence
overclaims what the fix covers.

- [x] 8.1 **Verified independently before acting**, per this project's "verify every claim"
  convention: read bloom#875's full issue comment ("the two good scans had been ingested
  earlier the same day by two hand-submitted runs"), then traced `services/workflows/
  pipeline.py:322` (the sole inserter of `cyl_pipeline_run_scans` rows) and
  `complete_cyl_pipeline_batch` (`20260817120000_add_cyl_pipeline_dispatch_functions.sql:194-207`,
  which only ever UPDATEs `argo_workflow_name` on an already-inserted row, never inserts one).
  Confirmed: a hand-submitted `argo submit` run's scan never gets a `cyl_pipeline_run_scans` row
  at all, so `source_id` is never stamped for a source whose original delivery was hand-submitted
  — this change's fallback has nothing to resolve `scan_id` from for that source, on any later
  re-delivery, no matter how many times retried. The reviewer's finding holds.
- [x] 8.2 **Fixed:** `proposal.md`'s "Why" section and `design.md`'s Context/Goals-Non-Goals/
  Verification sections all reworded — the live-measured case is now framed as evidence of the
  symptom's severity, not as the case this fallback closes. Added an explicit scope-limit
  statement (closes: re-delivery when the original was Bloom-dispatched; does not close:
  re-delivery when the original was hand-submitted) in three places (Context, a new Non-Goals
  bullet correcting an inaccurate earlier claim, and Verification).
- [x] 8.3 **Fixed, incidentally:** while correcting the above, caught and fixed a second,
  narrower inaccuracy of my own — design.md's Context claimed `cyl_pipeline_run_scans` rows are
  "inserted by `complete_cyl_pipeline_batch`"; they are inserted by `pipeline.py` at run-creation
  time, and only *updated* (their `argo_workflow_name`) by `complete_cyl_pipeline_batch` later.
  Corrected with the exact line references.
- [x] 8.4 **Fixed:** reworded `test_noop_redelivery_under_new_workflow_name_falls_back_to_scan_id`'s
  docstring (was: "The exact bloom#875 repro") and task 2.1's description to state plainly this
  test covers the Bloom-dispatched-original shape, not the hand-submitted-original shape the
  issue's live reproduction actually measured — and points at
  `test_fallback_finds_nothing_for_a_never_dispatched_workflow` for the shape that isn't fixed.
- [x] 8.5 **Added:** an operational-consequence note in `design.md`'s Verification section —
  `sleap-roots-pipeline#56`'s task 7.4b will still fail on the existing `A4-PIPELINE-E2E-TEST`
  scans after this merges, since sources 83-90 there all originated from hand-submitted runs;
  that task needs fresh synthetic scans regardless, but anyone expecting 7.4b to go green
  *because of this merge* would be surprised for the wrong reason otherwise.
- [x] 8.6 Not changed: no code change was needed or suggested by this review — the reviewer's
  own verdict was "non-blocking for the code," and independently confirmed the SQL diff
  (0 removed lines outside the fallback block) and the spec-scenario coverage (13→18 scenarios,
  three renamed not deleted) were both clean. This section is documentation-only.
- [x] 8.7 Re-validated after all of the above: `openspec validate --strict` passes;
  `git diff --stat` confirms only `.md` files under this change's directory plus the two test
  files changed (no migration/rollback SQL touched by this round).
- [x] 8.8 Pushed as commit `71c81f61`; replied on PR #880 acknowledging the finding and
  summarizing the fix.

## 9. Third review — blm3886 (Benfica), PR #880 comment + APPROVED, 2026-09-17

Approved. Independently re-verified the migration/rollback diffs (byte-identical outside the
fallback block) and the dispatch-side premise (the `bloom_workflows` `INSERT` grant excludes
`source_id`, confirmed via `20260730120000_create_cyl_pipeline_runs.sql:131-136`). Two
"suggestive follow ups," not blocking.

- [x] 9.1 **Added:** `test_redelivery_fallback_fixes_the_batch_level_counts_bloom875_measured` —
  the existing new tests each assert a single row's `status_update_matched`, but bloom#875's
  symptom was measured as `done_count=0, failed_count=3` across a 3-scan batch. This mirrors
  `test_writeback_and_rollup_connect_end_to_end`'s shape (two Bloom-dispatched-original scans
  re-delivered under a new workflow, plus one genuine failure) and pins `(done_count,
  failed_count) == (2, 1)` at the RPC level — the batch-count-level assurance the still-blocked
  live Argo re-test can't currently give.
- [x] 9.2 **Documented, not implemented (schema-change scope decision):** an index on
  `source_id` plus a partial `UNIQUE (argo_workflow_name, scan_id)` would speed up the
  fallback's lookup (currently a sequential scan — `cyl_pipeline_run_scans` has no index beyond
  the PK and `UNIQUE (run_id, scan_id)`) and would also close the `(argo_workflow_name,
  scan_id)` uniqueness gap already documented in `design.md`'s Risks as pre-existing/out of
  scope. Not added here — it's a real schema change (new migration, a genuine constraint change
  needing its own review of whether any legitimate path could ever want two rows sharing that
  pair, not just a perf tweak) beyond this PR's minimal-footprint scope. Recorded in `design.md`
  with Benfica's proposed shape, for whoever picks up the follow-up.
- [x] 9.3 Re-ran the full affected suite: `tests/integration/test_cyl_writeback_rpc.py` — 108
  passed (up from 107), same 2 pre-existing unrelated failures. `openspec validate --strict`
  and `uvx ruff@0.9.9 check`/`format --diff` on the changed test file — both clean.
- [x] 9.4 Filed bloom#881 for the index/uniqueness follow-up; cross-referenced in `design.md`.
  Pushed as a new commit and replied on PR #880.
