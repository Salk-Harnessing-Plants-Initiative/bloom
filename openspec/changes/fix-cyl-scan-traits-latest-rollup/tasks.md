## Landing plan — one PR, not phased (see design.md D3/D8 and the Migration Plan)

Unlike PR #654 (this change's superseded predecessor), nothing here requires a batched backfill, an
operator runbook, or a gated view cutover — the backfill is a single ~2.5s aggregate query run inside the
migration transaction itself (design.md D3). This lands as one PR against `staging`, opened off a fresh
branch (`eberrigan/bloommcp-list-experiments-timeout-637-v2`).

## 0. Pre-work

- [x] 0.1 Archive `fix-bloommcp-list-experiments-summary-rpc` (bloom#625) first — it was fully
      implemented and deployed (its migration is already on `staging`) but never archived, so
      `openspec/specs/cyl-trait-read/spec.md` was missing the "Aggregate experiment summary counts"
      requirement this change modifies. **Done** — archived as
      `openspec/changes/archive/2026-08-14-fix-bloommcp-list-experiments-summary-rpc/`; its one
      incomplete task (0.2, benchmarking a client-side timeout around the unfixed query cost) is marked
      superseded by this change rather than left open.
- [x] 0.2 Re-check `supabase/migrations/`'s newest file on `origin/staging` immediately before opening the
      PR (this proposal was drafted against `20260807000000_get_experiment_summary_counts.sql` as the
      tip) and choose migration timestamps later than that. **Done — confirmed `20260807000000` still
      the tip; this change's three migrations use `20260817010000`/`20260817020000`/`20260817030000`.**
- [x] 0.3 Confirm PR #654 is closed with a comment pointing to this PR before this PR opens (or
      immediately after) — needs the user's own action per repo convention (Claude does not close PRs
      without explicit confirmation, already given for this change). **Done — PR #654 closed with a
      pointer comment to PR #684 (this change's PR).**

## 1. `cyl_scan_latest_source` table + trigger (RED first) — design.md D1-D3

- [x] 1.1 Add `tests/integration/test_cyl_scan_latest_source.py` using `test_cyl_read_path.py`'s existing
      fixtures/helpers. Write failing tests first (table doesn't exist yet): - A fresh insert via `insert_cyl_result_envelope` creates a `cyl_scan_latest_source` row for a
      brand-new scan with `max_source_id` equal to that source's id. - A rerun (new, higher `source_id`) updates the existing row's `max_source_id` to the new source. - Deleting the current-latest source's rows for a scan promotes the next-highest remaining
      source's id. - A direct `bloom_admin`-role write (via `SET LOCAL ROLE bloom_admin`) is also maintained
      correctly — proves the trigger covers the break-glass path. - **Concurrent-first-insert race, using `conftest.py`'s `pg_conninfo` second-connection fixture**:
      two connections, both delivering the first-ever rows for the same brand-new `scan_id` under
      different `source_id`s, interleaved so both are in-flight before either commits — assert the
      final `max_source_id` is the true higher of the two, not whichever transaction happened to
      commit last with a value computed before it could see the other's data. **This is the exact race
      reproduced empirically against local Postgres during design (design.md D2) — this test is that
      reproduction, formalized. `/review-pr` caught that the first draft's construction (letting the
      last-to-resolve connection hold the numerically higher id) couldn't actually distinguish
      locked-vs-unlocked behavior — fixed by pre-minting both ids and assigning the lower one to the
      last-to-resolve connection; verified by temporarily removing the lock and confirming the test
      then fails (see design.md's Risks section).** - **Concurrent-rerun race**, same two-connection shape, for an existing scan with two concurrent
      reruns instead of a brand-new scan. Same construction fix applied. - The trigger function's catalog metadata shows `SECURITY DEFINER`, a pinned `search_path`, and
      schema-qualified references throughout its body. - **(Added post-`/review-pr`)** Boundary values: an all-legacy-NULL-source scan resolves
      `max_source_id IS NULL` and `is_latest = true`; deleting all of a scan's trait rows leaves a
      harmless NULL "ghost" row, no error. - **(Added post-`/review-pr`)** A write-back call concurrent with a simulated backfill migration
      (`LOCK TABLE ... IN SHARE MODE` held on a separate connection) blocks, then completes correctly
      once the lock releases — the cyl-trait-writeback spec scenario this covers had no test before. - **(Added post-`/review-pr`)** RLS: the four intended read roles see real rows; `anon` sees zero
      rows (RLS-filtered, not an error) despite real data existing; `anon`'s raw table-level
      `INSERT`/`UPDATE`/`DELETE`/`TRUNCATE` grant (confirmed to genuinely exist — Supabase's default for
      every new public-schema table) is blocked by RLS, not by any grant.
- [x] 1.2 Confirm every 1.1 test fails against a database with none of this section's migration applied.
- [x] 1.3 `supabase/migrations/<ts>_create_cyl_scan_latest_source.sql`: `CREATE TABLE
cyl_scan_latest_source` (D1) + `maintain_cyl_scan_latest_source()` trigger function
      (`pg_advisory_xact_lock(scan_id)` guard, `SECURITY DEFINER`, pinned `search_path`, per D2) +
      `maintain_cyl_scan_latest_source_after_write` `AFTER` trigger + `LOCK TABLE cyl_scan_traits IN
SHARE MODE` + the one-line backfill (`INSERT ... SELECT ... GROUP BY ... ON CONFLICT (scan_id) DO
UPDATE`, per D3) + the `cyl_scan_traits_source` view cutover (is_latest via join, not `WindowAgg`,
      per D3) — all in one transaction. Confirm all of section 1's tests now pass. **(Added
      post-`/review-pr`)** `ALTER TABLE ... ENABLE ROW LEVEL SECURITY` + the same four-role policy set
      `cyl_scan_traits` itself uses (D2a) — the table originally shipped without RLS, which review caught
      as a real gap, not a stylistic one (see D2a/Risks). Migration comments correcting the lock-level
      claim (`ShareRowExclusiveLock`, not `ACCESS EXCLUSIVE`) also added post-review.
- [x] 1.4 Companion `supabase/rollbacks/<ts>_create_cyl_scan_latest_source_rollback.sql`: restore the
      live-`WindowAgg` view definition, then drop the trigger, function, and table, in that order.
      **(Added post-`/review-pr`)** A precondition guard (`RAISE EXCEPTION` if
      `refresh_cyl_experiment_trait_counts` still exists) — review found the documented "M3 before M1"
      rollback order was enforced only in prose, not in the SQL itself.
- [x] 1.5 Add to the same test file: a test seeding data directly (bypassing the trigger, simulating
      pre-existing un-backfilled data) and confirming the backfill's result matches a hand-computed
      `max(source_id)` oracle per scan.
- [x] 1.6 Run `test_cyl_read_path.py`/`test_cyl_experiment_traits.py`; confirm zero regressions — the
      view's external contract (columns, values) is unchanged.
- [x] 1.7 `test_migration_adds_no_write_capability`, `test_migration_body_is_idempotent`,
      `test_rollback_restores_prior_state` (mirroring this repo's existing migration-test conventions).
      **(Strengthened post-`/review-pr`)** the rollback test now exercises the full documented
      reverse-chronological chain (rolls back sections 4's then 3's migrations first) and asserts the
      restored view computes `is_latest` correctly, not just that the objects are gone; a new
      `test_rollback_guard_blocks_out_of_order_rollback` proves the 1.4 guard actually fires.

## 2. `n_plants` semi-join rewrite (RED first) — design.md D4

- [x] 2.1 In `tests/integration/test_cyl_experiment_summary_counts.py`, before changing the function body:
      confirm the existing equivalence tests (`test_unpinned_counts_match_get_experiment_traits`,
      `test_accession_null_plant_excluded_from_counts`, etc.) pass against the _current_
      `COUNT(DISTINCT ...)` implementation — this is the oracle the rewrite must keep matching. - Add a fixture covering every edge case the semi-join rewrite must preserve: null-accession-plant
      exclusion, reruns/multiple sources, legacy NULL-source-only scans, and scans with zero trait rows
      — run each against **both** the current implementation (before 2.2) and the rewritten one (after
      2.2), asserting identical results, not just asserting the rewritten one "looks right."
- [x] 2.2 Rewrite the unpinned branch's `n_plants` computation to the `EXISTS` semi-join (D4) —
      `accession_id IS NOT NULL` in place of `JOIN accessions`, `cyl_experiments` join dropped. Confirm
      2.1's tests still pass with identical results.
- [x] 2.3 Structural confirmation the rewritten unpinned call doesn't drag `cyl_scan_traits_source` rows
      through a live join for `n_traits` (`test_unpinned_call_no_live_join_over_cyl_scan_traits`, via
      `EXPLAIN (FORMAT TEXT)` asserting the plan contains no reference to `cyl_scan_traits_source`).

## 3. `cyl_experiment_trait_counts` cache (RED first) — design.md D5

- [x] 3.1 Add `tests/integration/test_cyl_experiment_trait_counts.py` (RED first — table doesn't exist,
      `UndefinedTable`): - `refresh_cyl_experiment_trait_counts()` populates one row per experiment with matching data,
      `n_traits` matching a hand-computed distinct-trait-id count. - An experiment with no matching data gets no row. - An experiment that had a row and loses all its trait data has that row removed by the next
      refresh (not left stale). - A rerun that changes which source is latest is reflected in the _next_ refresh, not before it
      (this is the deliberate staleness design.md D5 describes — assert the pre-refresh value is the
      _old_ state, then assert post-refresh it's the _new_ state). - Cross-experiment isolation. - The null-accession-plant exclusion, matching `get_experiment_traits`. - **No trigger on `cyl_scan_traits` invokes this function** — insert trait rows, assert the cache
      table is unchanged until `refresh_cyl_experiment_trait_counts()` is explicitly called. - **(Added post-`/review-pr`)** RLS: `anon` sees zero rows despite real data; `anon`'s raw
      table-level `INSERT` grant (confirmed to exist) is blocked by RLS.
- [x] 3.2 `supabase/migrations/<ts>_create_cyl_experiment_trait_counts.sql`: `CREATE TABLE
cyl_experiment_trait_counts` (D5) + `refresh_cyl_experiment_trait_counts()` function + an initial
      `SELECT public.refresh_cyl_experiment_trait_counts();` call in the same migration (so the cache
      isn't empty until the first scheduled run — design.md's Migration Plan, M2). `REVOKE`/`GRANT
EXECUTE ... TO service_role` only (not the four read roles — this is a maintenance job, not a
      user-facing call). Confirm 3.1's tests pass. **(Added post-`/review-pr`)**
      `ALTER TABLE ... ENABLE ROW LEVEL SECURITY` + the same four-role policy set (D5a) — same gap as
      section 1's table, same fix.
- [x] 3.3 Companion rollback: drop the function and table. **(Added post-`/review-pr`)** A precondition
      guard (`RAISE EXCEPTION` if `get_experiment_summary_counts` still references
      `cyl_experiment_trait_counts`) — same rollback-ordering gap as section 1's, same fix; a new
      `test_rollback_guard_blocks_out_of_order_rollback` proves it fires, replacing what was previously a
      test that only proved the _unguarded_ breakage (now moot, since the guard prevents it outright).

## 4. `get_experiment_summary_counts` rewrite (RED first) — design.md D6-D7

- [x] 4.1 Add tests to `test_cyl_experiment_summary_counts.py`: - Unpinned call: `n_plants` matches a live computation (corrupt the cache table's `n_traits` for
      one experiment to confirm `n_plants` is unaffected — proves it's computed live, not read from any
      cache); `n_traits` matches whatever `cyl_experiment_trait_counts` currently holds (corrupt it to
      a known-wrong value and confirm the RPC returns that wrong value for `n_traits`, proving it reads
      the cache rather than recomputing — then restore and re-test the correct case). - Pinned (`source_id_`/`run_id_`) calls: both counts still match `get_experiment_traits` exactly,
      computed live (re-run the existing pinned-branch equivalence tests unmodified — their _behavior_
      doesn't change, only incidental cleanups per D6/D7). Also confirmed unaffected by cache staleness
      (`test_pinned_call_unaffected_by_cache_staleness` — no refresh call at all, still correct). - Both `source_id_` and `run_id_` set still raises. - An experiment absent from both the live `n_plants` computation and the cache is absent from the
      result (not zero-valued). - **(Added post-`/review-pr`)** `anon` has no `EXECUTE` grant on either
      `get_experiment_summary_counts` or `compute_cyl_experiment_summary_counts_live`
      (`test_anon_has_no_execute_grant`, parametrized over both).
- [x] 4.2 `supabase/migrations/<ts>_rewrite_get_experiment_summary_counts.sql` (D6/D7):
      `compute_cyl_experiment_summary_counts_live` (pinned-branch-only helper) +
      `get_experiment_summary_counts` rewrite (unpinned → live semi-join + cache read; pinned →
      delegates to the helper). Same signature, same grants. Confirm 4.1's tests pass. **(Fixed
      post-`/review-pr`)** both functions' `REVOKE EXECUTE` now includes `anon` explicitly, not just
      `PUBLIC` — Supabase's default auto-grant to `anon` on new public-schema functions left both callable
      by an unauthenticated caller; confirmed exploitable (`compute_cyl_experiment_summary_counts_live` is
      `SECURITY DEFINER`, so this bypassed table-level grants too) before the fix, blocked after (D6).
- [x] 4.3 Companion rollback: restore the prior (bloom#625) live-join-only function body. **(Fixed
      post-`/review-pr`)** its own `REVOKE` now also excludes `anon` — preserves the anon-EXECUTE fix even
      in the rolled-back state, rather than regressing to the pre-existing (bloom#625) leak.
- [x] 4.4 Run all of sections 1-4's tests plus `test_cyl_read_path.py`/`test_cyl_experiment_traits.py`;
      confirm no regressions. No `bloommcp`/Python test changes expected — `list_experiments()`'s
      contract is unchanged. **Done — 365 passed, 5 skipped across the full `cyl`-scoped integration
      suite after all `/review-pr` fixes.**

## 5. Scheduled refresh (design.md D8 — proposed default, flagged for confirmation)

- [x] 5.1 Add a scheduled GitHub Actions workflow
      (`.github/workflows/refresh-cyl-experiment-trait-counts.yml`, `on: schedule`, every 10 min) that
      calls `refresh_cyl_experiment_trait_counts()` via the PostgREST RPC endpoint using a service-role
      key, not SSH+psql (avoids re-triggering the "manual DB access is emergency-only" policy question
      PR #654's D8 ran into for its own backfill). **Done — flagged for confirmation per design.md's
      Open Questions; if a `workflows`-service-hosted job is preferred instead, delete this file and
      file a follow-up issue against that service. (Hardened post-`/review-pr`: explicit `permissions: {}`
      (CodeQL had flagged its absence — low actual risk since the job never checks out code or uses the
      token, but fixed as defense-in-depth), `timeout-minutes: 2` on the job, `--connect-timeout
5 --max-time 30` on the `curl` call so a hung staging endpoint can't occupy the runner
      indefinitely.)**
- [ ] 5.2 **Blocked on new secrets, not yet provisioned.** Unlike every other staging secret in this
      repo (all consumed server-side by `deploy.yml`), calling staging's PostgREST endpoint directly
      from a GitHub Action needs a new `STAGING_API_URL` secret — no existing secret covers this (only
      `STAGING_SERVICE_ROLE_KEY` already exists, from `deploy.yml`'s own use). Add `STAGING_API_URL`
      before this schedule can actually run; the workflow fails loudly (not silently) if it's missing.
- [ ] 5.3 Verify the workflow's one authenticated call succeeds against staging once 5.2's secret is
      added, before relying on the schedule (`workflow_dispatch` is wired for a manual first run).

## 6. Validate

- [x] 6.1 Run the full section 1-4 test suite against local dev Postgres; no regressions in
      `test_cyl_read_path.py`, `test_cyl_experiment_traits.py`, `test_cyl_experiment_summary_counts.py`.
- [ ] 6.2 Run `bloommcp`'s full test suite; confirm zero changes needed. **Not yet run in this pass —
      Python-side contract is unchanged (no `bloommcp/` files touched), but the actual suite run is still
      outstanding.**
- [x] 6.3 `openspec validate fix-cyl-scan-traits-latest-rollup --strict` passes.
- [x] 6.4 Migration lint (`scripts/lint_migrations.sh origin/staging`) passes (3 new files, timestamps
      after the `20260807000000` base). `openspec validate --strict` repo-wide shows the same 9
      pre-existing, unrelated failures as a clean `git stash` baseline (confirmed identical before/after
      this change's edits) — none caused by this change. `black`/`ruff` do not apply to `tests/integration/`
      per `.pre-commit-config.yaml` (scoped to `langchain|bloommcp|services/workflows|bloomcli` only) —
      confirmed by reading that config directly, not assumed.
- [x] 6.5 Resolve whether `cyl_scan_latest_source` and `cyl_experiment_trait_counts` need entries in the
      five tracked `database.types.ts` copies. **Resolved: YES** — ran
      `npx supabase gen types typescript --db-url ...` against the local dev DB with all three of this
      change's migrations applied; confirmed `cyl_scan_latest_source`/`cyl_experiment_trait_counts`
      (tables) and `compute_cyl_experiment_summary_counts_live`/`refresh_cyl_experiment_trait_counts`
      (functions) all appear in the output — same finding PR #654's own task 0.4 made for its own new
      objects. **The hand-edit of the five ~2000-4000-line tracked copies is not done in this pass**,
      matching that same precedent: each file's existing style needs its own careful pass, not a rushed
      edit at the end of this one. Do this before opening the PR, or as an immediate follow-up commit on
      the PR branch before requesting review.
- [ ] 6.6 (staging-only, post-merge, before closing bloom#637 — a hard gate): time `list_experiments()`
      end-to-end against staging and confirm it's well under a second; post the result on bloom#637
      before archiving this change. Also benchmark the `source_id_`/`run_id_`-pinned branches at
      `experiment_id=1` scale (design.md D7's unresolved item) and record the result.

## 7. Docs + follow-up

- [x] 7.1 Update `bloommcp/docs/data-access-roadmap.md`: dated entry noting `is_latest`'s new storage
      mechanism and the `cyl_experiment_trait_counts` cache; note that this change supersedes PR #654 and
      folds in bloom#656; add a "Questions for Benfica" entry for D7's un-benchmarked pinned branches and
      D8's proposed GitHub Action refresh schedule, both flagged for her confirmation.
- [x] 7.2 Update `_WIKI/BLOOMMCP/README.md`'s "Supabase data access" section to note the storage change
      and the scheduled-cache behavior for `n_traits`.
- [x] 7.3 Run `prettier --check`/`--write` on edited doc files before opening the PR. **Also re-run after
      section 8's `/review-pr` doc updates (design.md's new content triggered a real, non-pre-existing
      prettier diff — fixed via `--write`, confirmed scoped to only this change's own additions via
      `git diff`).**

## 8. `/review-pr` pass (post-PR-#684, pre-merge)

PR #684 opened without running `/review-openspec` or waiting for explicit proposal approval first — an
acknowledged process deviation from `new-feature.md`'s guardrail. Rather than retroactively re-running a
proposal-level review after the code already existed and matched it, this section is a code-level
`/review-pr` pass instead (5 subagents: code quality, testing, scientific rigor, security, behavioral
correctness), run directly against the diff — see design.md's Risks section for the two most significant
findings (the concurrency-test construction bug and the RLS/anon-grant security gap).

- [x] 8.1 Run the 5-subagent review. All 5 returned; no subagent failed or returned a suspiciously short
      result.
- [x] 8.2 Adjudicate a genuine disagreement between two subagents' claims about `CREATE TRIGGER`'s lock
      mode empirically (one claimed `ACCESS EXCLUSIVE`, the other `ShareRowExclusiveLock`) — verified
      directly against a scratch table and `pg_locks` rather than trusting either claim. Confirmed
      `ShareRowExclusiveLock`; corrected the migration's own comment and design.md D3 accordingly (the
      original text was wrong on both the lock name and duration — see design.md's Risks section).
- [x] 8.3 Fix all BLOCKING/IMPORTANT findings that survived verification: RLS on both new tables (D2a/D5a),
      `anon` EXECUTE grant leak on two functions (D6), the concurrency tests' construction bug (this
      section's own note above), the missing "write concurrent with backfill" test, the two rollback
      tests' weak assertions, boundary-value test gaps, the GitHub Actions `permissions:`/timeout gaps, and
      the rollback-ordering guards (SQL-level, not just prose).
- [x] 8.4 Re-run the full `cyl`-scoped integration suite after all fixes — 365 passed, 5 skipped, up from
      351 before this section (14 new tests: RLS × 2 tables, anon-grant × 2 functions, boundary values × 2,
      backfill-concurrency × 1, rollback-guard × 2).
- [ ] 8.5 Post the synthesized review to PR #684 (`gh pr review --comment`, since a self-review can't
      `--request-changes`/`--approve`). **Blocked**: the posting attempt was denied by the local auto-mode
      permission classifier (not a GitHub-side restriction) — the full synthesized review was shown to the
      user directly in-conversation instead. Still outstanding if GitHub-side posting is wanted later.
- [x] 8.6 Iterate with additional `/review-pr` passes if the posted review (or CI) surfaces anything new,
      until it converges. **Done — see section 9**: a second round found three more genuine bugs the first
      missed (D2b, D5b, plus several test-quality items), all fixed. A third round has not yet been run;
      nothing outstanding as of this pass suggests one is needed, but that's exactly what the first two
      rounds also looked like before the next one found something real.

## 9. Second `/review-pr` round — verify round 1's fixes hold up, find what round 1 missed

Explicitly framed as adversarial re-verification, not a rubber-stamp: each of the 5 subagents was told
round 1's fixes were already committed and instructed to verify them fresh rather than trust the summary.

- [x] 9.1 Run the 5-subagent review. All 5 returned; CI (`gh pr checks`) showed all 27 checks green,
      including `Docker Compose Health Check` and CodeQL (confirming round 1's `permissions: {}` fix
      actually resolved the CodeQL finding, not just silenced the comment thread — the lingering
      `github-advanced-security` PR comment was confirmed stale, not a live finding).
- [x] 9.2 Verify the two most significant new findings empirically before fixing, matching this change's
      own established discipline: - `refresh_cyl_experiment_trait_counts()`'s concurrent-call race (D5b) — reproduced directly
      (`UniqueViolation` on `cyl_experiment_trait_counts_pkey`), fixed with
      `pg_advisory_xact_lock(hashtext(...))`, re-verified the fix closes it. - The cross-scan `UPDATE` trigger gap (D2b) — reproduced directly via
      `test_cross_scan_update_recomputes_both_scans` failing against the original (unpatched) trigger
      function (scan A stuck at the departed source instead of falling back), fixed, re-verified passing.
- [x] 9.3 Fix everything that survived verification: - D2b (cross-scan `UPDATE`) and D5b (concurrent refresh race) — both BLOCKING, both fixed with a
      new test each, both confirmed to fail against the pre-fix code and pass against the fix. - The trigger function's own missing `anon` `EXECUTE` revoke (practically inert, fixed for
      consistency with every other `SECURITY DEFINER` function this change adds). - `test_migration_adds_no_read_role_execute_grant` (in `test_cyl_experiment_trait_counts.py`)
      omitted `anon` from its own IN-list check — added an explicit `has_function_privilege('anon', ...)`
      assertion, the same pattern the sibling test in `test_cyl_experiment_summary_counts.py` already
      used correctly. - `test_deleting_all_rows_leaves_null_max_source_no_error` couldn't distinguish "row exists with
      `max_source_id = NULL`" from "row absent" — added an explicit row-count assertion. - Rollback guards (both files) anchored with `AND pronamespace = 'public'::regnamespace`. - The scheduled workflow validates `STAGING_API_URL` is `https://` before calling it, and `curl`
      gained `-S` (surface connection-level failures, not just HTTP-level ones). - The "write concurrent with backfill" test's docstring corrected to describe what it actually
      verifies (generic `LOCK TABLE` blocking behavior) rather than overclaiming it replays the specific
      migration-application hazard design.md D3 describes. - RLS role-parametrized tests extended to cover `bloom_writer`/`authenticated` (previously only
      `bloom_agent`/`bloom_user`/`bloom_admin`), verifying `bloom_writer`'s policy inheritance via
      `GRANT bloom_user TO bloom_writer` rather than just assuming it. - The three real-connection concurrency tests (plus the new refresh-concurrency test) now clean up
      every row `_seed_experiment_scan`/`_mint_source_and_trait` created via a new
      `_cleanup_seeded_experiment` helper, instead of only deleting `cyl_scan_traits`/
      `cyl_scan_latest_source` and leaving `species`/`cyl_experiments`/`cyl_waves`/`accessions`/
      `cyl_plants`/`cyl_scans`/`cyl_images` permanently committed in the local dev DB. Verified flat
      row counts across repeated runs, not just "the code looks like it deletes things." - `refresh_cyl_experiment_trait_counts()`'s `DISTINCT trait_id` (vs. the other two counting paths'
      `DISTINCT trait_name`) equivalence documented with a comment noting the `cyl_traits.name NOT NULL
    UNIQUE` assumption it depends on. - `test_read_roles_can_call_function`'s `count(*) IS NOT NULL` assertion (trivially always true)
      replaced with an assertion on the actual `(n_plants, n_traits)` row content.
- [x] 9.4 Re-run the full `cyl`-scoped integration suite after all fixes — 369 passed, 5 skipped, up from
      365 after round 1 (4 net new tests: cross-scan `UPDATE`, concurrent-refresh race, plus fixes to
      existing tests that didn't add new test functions).
- [ ] 9.5 Post round 2's synthesized review to PR #684 — same GitHub-posting blocker as 8.5; shown to the
      user directly instead.

## 10. Third `/review-pr` round — verify rounds 1 and 2's fixes hold up, find what they missed

Same discipline as round 2: each of the 5 subagents was told rounds 1 and 2's fixes were already committed
and instructed to verify them fresh rather than trust the summary, and to find only NEW issues.

- [x] 10.1 Run the 5-subagent review. All 5 returned; no subagent failed or returned a suspiciously short
      result.
- [x] 10.2 Verify the two most significant new findings empirically before fixing, matching this change's
      own established discipline: - `TRUNCATE` bypasses RLS entirely (D9) — confirmed `SET LOCAL ROLE anon; TRUNCATE public.cyl_scan_latest_source;`
      succeeded despite `anon`'s `INSERT` on the same table already being denied by RLS (D2a); same
      confirmed on `cyl_experiment_trait_counts`. - `refresh_cyl_experiment_trait_counts()`'s single-bigint advisory lock (D5b, round 2's own fix) shares
      a keyspace with D2's per-scan `pg_advisory_xact_lock(scan_id)` (D5c) — found the literal colliding
      `scan_id` (`hashtext('refresh_cyl_experiment_trait_counts') = -124364726`), confirmed the two-int
      form (`pg_advisory_xact_lock(0, hashtext(...))`) is a genuinely disjoint keyspace via a real
      two-connection test, not assumed from the different call shape.
- [x] 10.3 Fix everything that survived verification: - D9 (`TRUNCATE`) — `REVOKE TRUNCATE, REFERENCES, TRIGGER ... FROM anon, authenticated` added to both
      new tables' migrations, following this repo's own `20260504000002_grant_all_scope_reduction.sql`
      precedent (which only ever covered `bloom_admin`). New `test_anon_cannot_truncate` in both test
      files, each confirmed to fail against the pre-fix grant and pass against the fix. Noted, not fixed,
      as a pre-existing repo-wide gap: `anon` can still `TRUNCATE public.cyl_scan_traits` itself today. - D5c (advisory-lock keyspace collision) — `refresh_cyl_experiment_trait_counts()`'s lock call changed
      to the two-int form; design.md D5's code block and D5c both updated. - The rollback guard for `20260817010000` (M1) strengthened to check `cyl_experiment_trait_counts`
      TABLE existence, not just `refresh_cyl_experiment_trait_counts()` FUNCTION existence — a function
      dropped out-of-band without running M2's own rollback would have silently defeated the
      function-only check. Migration Plan section in design.md updated to describe both checks. - The sorted cross-scan lock acquisition (D2b, round 2's own fix) had zero concurrency coverage —
      the existing `test_cross_scan_update_recomputes_both_scans` is purely sequential, so it could never
      have caught a missing or wrongly-ordered lock. New
      `test_concurrent_opposite_direction_cross_scan_reassignments_do_not_deadlock` uses a
      `threading.Barrier` so two connections issue opposite-direction cross-scan `UPDATE`s at the same
      instant — NOT the "A completes, then B starts" shape the sibling concurrency tests use, which this
      section's own first draft used too and which turned out unable to distinguish sorted from unsorted
      lock order (verified: it passed unchanged even with the trigger's lock order reverted to unsorted
      `NEW`-then-`OLD`). Rebuilt with the barrier; reliably reproduced a real `DeadlockDetected` against
      the unsorted trigger (1 in 3 attempts) and zero deadlocks across 15 attempts against the correct
      one, then confirmed the pytest version fails against the reverted trigger and passes against the
      restored one. - `test_multi_row_cross_scan_update_recomputes_both_scans` added (code-quality suggestion): a single
      multi-row `UPDATE` reassigning two rows from one scan to another in one statement, locking in that
      the trigger's multiple per-row firings for the same `(OLD, NEW)` scan pair converge to the correct
      final state. - `test_concurrent_refreshes_do_not_raise_duplicate_key` strengthened: the original version only
      asserted the absence of an error after a fixed `time.sleep`, which would also pass if the lock were
      silently a no-op and the two calls got lucky. Now queries `pg_locks` from a third connection to
      confirm a granted lock (classid=0, objid=hashtext(...), objsubid=2 — the two-int form's actual
      signature, confirmed via direct `psql` introspection) held by A's backend pid, and a non-granted
      waiter row for the same key from a different pid, before letting A commit. Confirmed this version
      fails (waiter-row assertion, `0 == 1`) against a temporarily unlocked refresh function and passes
      against the restored one. - `test_cleanup_seeded_experiment_removes_every_row` added: `_cleanup_seeded_experiment` itself had no
      direct test despite every real-connection concurrency test depending on it to avoid leaking seeded
      rows into the long-lived local dev DB. Seeds one experiment with two deliveries, calls the helper,
      asserts zero rows remain across every table it touches. Confirmed this test fails when the
      `accessions` delete step is temporarily disabled, and passes with it restored.
- [x] 10.4 Re-run the full `cyl`-scoped integration suite after all fixes — 374 passed, 5 skipped, up from
      369 after round 2 (5 net new tests: `TRUNCATE`-denial × 2, opposite-direction cross-scan deadlock × 1,
      multi-row cross-scan × 1, cleanup-helper self-check × 1; the refresh-concurrency strengthening added
      assertions to an existing test rather than a new one).
- [ ] 10.5 Post round 3's synthesized review to PR #684 — same GitHub-posting blocker as 8.5/9.5; shown to
      the user directly instead.
