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
      the tip; this change's three migrations use `20260817130000`/`20260817140000`/`20260817150000`.**
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

## 5. On-demand refresh, both environments (design.md D8 — redesigned post-round-8)

- [x] 5.1 Add a GitHub Actions workflow
      (`.github/workflows/refresh-cyl-experiment-trait-counts.yml`) that calls
      `refresh_cyl_experiment_trait_counts()` via the PostgREST RPC endpoint using a service-role key,
      not SSH+psql (avoids re-triggering the "manual DB access is emergency-only" policy question PR
      #654's D8 ran into for its own backfill). **Redesigned after rounds 7-8's findings (see below):
      no `on: schedule` trigger at all — `workflow_dispatch`-only, with an `environment` choice input
      (`staging`/`production`, mirroring `deploy.yml`'s own convention). Staging doesn't need frequent
      automatic refreshes right now, and a schedule would have sat inert pre-promotion anyway (5.4's
      original finding). **Corrected (bloom#708 investigation, post-merge): manual `workflow_dispatch`
      does NOT work against any branch without promotion either — it needs this file on `main` exactly
      like `schedule:` does; see 5.3/5.4/5.6's corrections and design.md's D8 addendum.**
      (Hardened post-`/review-pr`: explicit `permissions: {}`, `timeout-minutes: 2` on the job,
      `--connect-timeout 5 --max-time 30` on the `curl` call so a hung endpoint can't occupy the runner
      indefinitely.)**
- [x] 5.2 **No `STAGING_API_URL`/`PROD_API_URL` secrets needed.** Both values are the same public, stable
      hostnames already committed as each environment's own `API_EXTERNAL_URL` in
      `.env.staging.defaults`/`.env.prod.defaults`, and already documented non-sensitive by the Committed
      Defaults contract (`tests/unit/test_env_defaults.py`) — nothing about either needs secret-store
      protection. Hardcoded as literals in the workflow instead (this job deliberately never checks out
      the repo, so it can't read either `.env.*.defaults` file at runtime).
      `STAGING_SERVICE_ROLE_KEY`/`PROD_SERVICE_ROLE_KEY` — the actual credentials this job needs — both
      already existed as secrets before this workflow was added, so no new provisioning is required at
      all. Guarded by `tests/unit/test_refresh_workflow_shape.py`, which fails if either literal drifts
      from its `.env.*.defaults` or if a `secrets.STAGING_API_URL`/`secrets.PROD_API_URL` reference
      reappears.
- [ ] 5.3 **CORRECTED (bloom#708 investigation): genuinely blocked on promotion to `main`, not just
      deferred to "after this PR merges."** `workflow_dispatch` is gated on default-branch presence
      exactly like `schedule:` (GitHub's own docs: "To trigger the workflow_dispatch event, your
      workflow must be in the default branch") — confirmed against the live repo while this file
      existed only on `staging`: `gh api .../actions/workflows/refresh-cyl-experiment-trait-counts.yml`
      returned 404, `gh workflow list --all` didn't list it. So verifying the workflow's authenticated
      call against staging via `workflow_dispatch` (`environment: staging`) cannot happen at all — not
      via the UI, `gh workflow run`, or the REST API — until this repo's next `chore: promote staging to
      main` PR carries this file to `main`. Do this once that promotion has happened, not merely "after
      this PR merges."
- [ ] 5.4 **CORRECTED (bloom#708 investigation): the original round-7 finding was resolved by redesign
      in name only — the promotion dependency was never actually closed.** GitHub Actions `schedule:`
      triggers only fire from the workflow file's copy on the repo's default branch, so a cron here would
      have sat inert on `staging` until a separate promotion PR landed it on `main`. Dropping
      `on: schedule` for `workflow_dispatch`-only was reasoned (at the time) to close this gap by
      construction — but `workflow_dispatch` is gated on the exact same default-branch requirement as
      `schedule:` (see 5.3's correction); it was never actually dispatchable pre-promotion, contrary to
      what this task originally claimed. Nothing about the redesign closed 5.4; it is still gated on this
      repo's normal `staging -> main` promotion practice, same as 5.6 below.
- [x] 5.5 **Found in round 8 — resolved by an `environment` input, not a second workflow.** The original
      staging-only version would never have refreshed production's cache even once promoted (a genuinely
      separate host per `.env.prod.defaults`'s `API_EXTERNAL_URL`, and `deploy.yml` only ever populates it
      once, at deploy time, via the migration's inline call). Closed by adding a `choice` input,
      `environment` (`staging`/`production`), that resolves to the right hardcoded URL/secret pair inside
      the run script — no new secrets needed (`PROD_SERVICE_ROLE_KEY` already existed).
- [ ] 5.6 **CORRECTED (bloom#708 investigation):** verify the workflow's authenticated call also succeeds
      against production via `workflow_dispatch` (`environment: production`) once this workflow file has
      been **promoted to `main`** — not merely "live" on any branch. Same blocker as 5.3: genuinely
      undispatchable pre-promotion, confirmed against the live repo (see 5.3's correction).
- [ ] 5.7 **Follow-up tracked, now scoped by Section 14 below, not deferred as originally written:**
      [bloom#708](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/708) tracks adding an
      automatic (scheduled) trigger for production once its write volume grows enough that on-demand
      dispatch stops being sufficient. Implemented on this same change (Section 14) rather than in a
      separate change-id, since `cyl-experiment-summary-rollup`'s refresh-mechanism requirement was still
      unarchived (this change hadn't been archived yet) and this is a direct continuation of D8's own
      still-open refresh-scheduling decision, not an unrelated new capability.
- [x] 5.8 **Found in round 9 — two real gaps in the redesign itself, both fixed.** (1)
      `concurrency.group` was a single string shared by both environments, so a `staging` dispatch and a
      `production` dispatch could cancel each other despite touching independent databases — fixed by
      including `${{ github.event.inputs.environment }}` in the group name. (2) The job declared no
      `environment:` key, so it bypassed this repo's GitHub Environment approval rules entirely (confirmed
      via the GitHub API that both `staging`/`production` Environments here carry `required_reviewers`,
      the same gate `deploy.yml`'s own jobs opt into) — anyone able to dispatch could have fired an
      RLS-bypass RPC at production with zero approval. Fixed by adding
      `environment: ${{ github.event.inputs.environment }}` to the job (confirmed no environment-scoped
      secret shadows either service-role key, so this only adds the approval gate). Also removed the
      `environment` input's `default: 'staging'` — forces an explicit choice every dispatch rather than
      silently refreshing staging when production was intended. All three guarded by new/updated tests
      in `tests/unit/test_refresh_workflow_shape.py`.
- [x] 5.9 **Found in round 9 (no named owner/cadence), resolved by the user's own direction:**
      **staging** needs no fixed cadence or owner at all — dispatch it manually, as needed, whenever
      testing calls for a fresher count. **production** stays on-demand only until bloom#708's
      automatic-refresh follow-up ships; no interim manual-dispatch owner is being named for it either,
      since that's exactly what #708 is for. Nothing further to spec here.

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
      to the two-int form; design.md D5's code block and D5c both updated. - The rollback guard for `20260817130000` (M1) strengthened to check `cyl_experiment_trait_counts`
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
- [x] 10.5 Post round 3's synthesized review to PR #684. **First attempt hit `HTTP 503: No server is
currently available` on `gh pr review` — a transient GitHub API outage, not the local
      permission-classifier block that stopped 8.5/9.5.** Confirmed via a lightweight `gh pr view` read
      succeeding immediately after, ruling out an auth/permission cause. A second attempt (at the user's
      request) succeeded; posted as a `COMMENTED` review, confirmed via `gh pr view --json reviews`.

## 11. Fourth `/review-pr` round — verify rounds 1–3's fixes hold up, find what they missed

Same discipline as rounds 2/3: each of the 5 subagents was told rounds 1–3's fixes were already committed
and instructed to verify them fresh rather than trust the summary, and to find only NEW issues. CI showed
two failing checks (`Analyze (python)`, `Pinned Images CVE Summary`) at review start — both traced to the
same `HTTP 503` GitHub API outage seen in 10.5 (CodeQL's `init` step and a PR-comment-posting step,
respectively), confirmed unrelated to this PR's content, not treated as findings.

- [x] 11.1 Run the 5-subagent review. All 5 returned; no subagent failed or returned a suspiciously short
      result.
- [x] 11.2 Verify the two most significant new findings empirically before fixing: - The behavioral-correctness subagent claimed `DROP TRIGGER IF EXISTS` takes `AccessExclusiveLock`
      and blocks readers even on "the very first real production deploy." Reproduced independently
      against a local Postgres and found this characterization WRONG: dropping a NONEXISTENT trigger
      (the actual first-deploy case) takes zero lock at all (confirmed via `pg_locks` and an unblocked
      concurrent `SELECT`, 0.47s). The AccessExclusiveLock + reader-blocking (confirmed: 5.7s blocked
      `SELECT`) only occurs when dropping an EXISTING trigger — i.e. a migration RE-RUN, which this
      repo's own `test_migration_body_is_idempotent` convention already exercises (and which local dev
      iteration can hit), not the real one-time production deploy. The underlying gap was still real and
      worth fixing given a strictly better replacement exists. - The testing subagent's claim that `test_concurrent_opposite_direction_cross_scan_reassignments_do_not_deadlock`
      (round 3) leaks seeded rows on assertion failure — confirmed by reading the test directly: its
      cleanup calls sat in the loop's normal-flow body, not a `finally`. Confirmed empirically: forced a
      failing assertion, and the seeded experiment count in the dev DB grew (73 -> 74) instead of staying
      flat, proving the leak.
- [x] 11.3 Fix everything that survived verification: - `DROP TRIGGER IF EXISTS` + `CREATE TRIGGER` replaced with a single `CREATE OR REPLACE TRIGGER`
      (PG14+; this repo runs PG15) in `20260817130000`. Verified empirically: `CREATE OR REPLACE TRIGGER`
      replacing an EXISTING trigger takes only `ShareRowExclusiveLock` (confirmed via `pg_locks` and an
      unblocked concurrent `SELECT`, 0.48s) — the same lock a bare `CREATE TRIGGER` always took, on both
      a first application and a re-run. Re-ran `test_migration_body_is_idempotent` and the full
      `test_cyl_scan_latest_source.py` suite after the fix — all passing. - `test_concurrent_opposite_direction_cross_scan_reassignments_do_not_deadlock`'s per-attempt cleanup
      moved into its own `try/finally`, not just the outer one. Verified the fix: forced the same failing
      assertion again and confirmed the seeded-experiment count stayed flat (no leak) despite the test
      failing as expected; removed the forced failure and confirmed the test (and the full file, 26
      tests) passes again. - **`database.types.ts` gap (flagged independently by two subagents, code-quality and scientific-rigor,
      and already a documented-but-undone task from section 6.5): all five tracked copies still had zero
      references to either new table or either new function, across all 3 prior rounds.** Ran
      `make gen-types` against the local dev DB to see the correct current shape, but did NOT commit its
      raw output directly — the full regeneration pulled in ~1400 lines of UNRELATED schema drift per
      file (`cyl_plant_search`, `cyl_experiment_accessions`, `bloommcp_usage`, gravi-scan functions, etc.
      — all from migrations already merged to the repo weeks before this PR, just never previously
      synced into these tracked files). Instead hand-edited only this PR's own two tables + two functions
      into all five files, in each file's own existing style, referencing only relations already present
      in that file (omitting FK relationship entries the raw regeneration suggested toward relations,
      like `cyl_plant_search`, that don't yet appear in a given tracked copy) — matching this repo's own
      established precedent for `database.types.ts` (`add-bulk-trait-read-rpc`,
      `add-cyl-trait-source-provenance`: hand-edit the specific addition, don't blindly regenerate).
      Verified with `tsc --noEmit --skipLibCheck` on each file (clean) and a brace-balance check.
      **Running `prettier --write` on these files was tried and reverted** — it reformatted ~1400
      unrelated lines per file, confirming (via the Makefile's own `gen-types` target, which never
      invokes prettier) that these files are not normally prettier-formatted; the final diff is a clean
      93/93/93/93/86-line addition across the five files, not a reformat. - Two `SET search_path`/`prosecdef` regression tests added
      (`test_refresh_function_search_path_is_pinned`,
      parametrized `test_function_search_path_is_pinned`), matching
      `test_cyl_scan_latest_source.py`'s existing `test_trigger_function_metadata` precedent — neither
      `refresh_cyl_experiment_trait_counts` nor `compute_cyl_experiment_summary_counts_live`/
      `get_experiment_summary_counts` had one before. `get_experiment_summary_counts` itself gained a
      pinned `search_path` (the one function in this change without one — not exploitable, since every
      reference in its body is already schema-qualified and it's `SECURITY INVOKER`, but pinned for
      consistency and to satisfy Supabase's linter). Verified: removed the pin, confirmed the new test
      fails; restored it, confirmed it passes. - Two misleading test comments fixed (`test_concurrent_first_insert_to_same_new_scan_converges_to_true_max`
      and its rerun sibling): both claimed `assert not b_done.wait(...)` proves B is "genuinely blocked
      on A's advisory lock" — actually true, but for the wrong reason. Both inserts' `ON CONFLICT
(scan_id)` target the SAME row, so Postgres's native uncommitted-conflicting-tuple wait would
      block B even with the advisory lock removed entirely; the advisory lock's actual job (proven by the
      downstream `true_max` assertion) is ensuring the post-wait value is CORRECT, not that B waits at
      all. Comments corrected to point at the assertion that actually discriminates locked from unlocked. - A comment added to the trigger documenting that a plain `DELETE` takes the cross-scan branch by
      an accidental (if currently harmless) NULL-handling quirk of `IS DISTINCT FROM`/`least`/`greatest`,
      not by design intent — flagged so a future edit assuming both `v_lo`/`v_hi` are always real,
      distinct scan ids doesn't silently misbehave for the DELETE case. - `design.md` updated: D5 (`n_traits`'s staleness section) now states plainly that the "bounded to one
      refresh interval" claim assumes the schedule is actually running, which it currently is NOT (D8's
      `STAGING_API_URL` secret still unprovisioned per tasks.md 5.2/5.3, unchanged from before this
      round) — staleness is presently unbounded, frozen at the migration's one-time initial population.
      The existing "Deadlock note" (D2) updated to acknowledge the multi-scan-pair deadlock shape is
      reachable TODAY via `bloom_admin`'s break-glass path (not just a hypothetical future automated
      writer) — accepted as a safe, self-healing (Postgres aborts one side; operator retries) risk, not
      fixed with a transaction-wide lock-ordering scheme.
- [x] 11.4 Re-run the full `cyl`-scoped integration suite after all fixes — 377 passed, 5 skipped, up from
      374 after round 3 (3 net new tests: the two `search_path` regression tests, one of them
      parametrized over 2 functions).
- [x] 11.5 Post round 4's synthesized review to PR #684. Posted successfully (`COMMENTED`, confirmed
      via `gh pr view --json reviews`) — no GitHub-side blocker this round.

## 12. Fifth `/review-pr` round — verify rounds 1–4's fixes hold up, find what they missed

Same discipline as rounds 2–4: each of the 5 subagents was told rounds 1–4's fixes were already
committed and instructed to verify them fresh rather than trust the summary, and to find only NEW
issues.

- [x] 12.1 Run the 5-subagent review. All 5 returned; no subagent failed or returned a suspiciously
      short result. Two subagents (code quality, security) explicitly found nothing new at
      BLOCKING/IMPORTANT severity after 4 prior rounds — the first time a round has produced a
      "holds up clean" result from more than one reviewer simultaneously.
- [x] 12.2 Verify the most significant new findings empirically before fixing: - `bloom_admin`'s `FOR ALL` RLS policy on both new tables was backed by only a `SELECT` grant, not
      the `INSERT`/`UPDATE`/`DELETE` `cyl_scan_traits` itself has — confirmed via
      `information_schema.role_table_grants` directly against the dev DB. Traced the root cause:
      `cyl_scan_traits`'s bloom_admin CRUD grant was made by the `postgres` role (grantor,
      confirmed via the same catalog view), while these two new tables were created by
      `supabase_admin` — a different role whose default-privileges configuration for
      `bloom_admin` doesn't include CRUD the way `postgres`'s does. Fails closed (bloom_admin has
      LESS access than the policy implies, not more), not exploitable — but confirmed genuinely
      inert for writes, not just a documentation mismatch. - `test_unpinned_call_no_live_join_over_cyl_scan_traits`'s `EXPLAIN`-based assertion was claimed
      to be structurally incapable of proving what it claims — reproduced directly:
      `EXPLAIN (FORMAT TEXT) SELECT * FROM get_experiment_summary_counts(...)` and
      `EXPLAIN (FORMAT TEXT) SELECT * FROM compute_cyl_experiment_summary_counts_live(...)`
      (whose body DOES directly join `cyl_scan_traits_source`) both produce the identical opaque
      `Function Scan on ...` line — Postgres never exposes a PL/pgSQL function's internal query
      plan through `EXPLAIN` on the caller's side, confirmed with `VERBOSE, ANALYZE` too. The
      assertion would pass identically whether the unpinned path reads the cache or was reverted
      to the exact live-join regression bloom#637/#656 exist to prevent. - No test guards the round-4 `CREATE OR REPLACE TRIGGER` fix — confirmed by grepping the whole
      `tests/integration/` tree for `AccessExclusive`/`ShareRowExclusive`/`pg_locks`: zero matches
      in `test_cyl_scan_latest_source.py`. `test_migration_body_is_idempotent` re-runs the
      migration but only asserts the table/trigger exist by name, never what lock was held. - `test_rollback_guard_blocks_out_of_order_rollback`'s two-branch `OR` guard (round 3's fix) was
      claimed to be untestable as-is, since both branches are simultaneously true in the normal
      baseline state. Confirmed by temporarily reverting the guard to its function-only,
      round-2-era form and re-running the existing test unchanged — it still passed, proving it
      genuinely cannot distinguish which branch fires.
- [x] 12.3 Fix everything that survived verification: - `GRANT INSERT, UPDATE, DELETE ON public.cyl_scan_latest_source/cyl_experiment_trait_counts TO
bloom_admin` added to both migrations, matching `cyl_scan_traits`'s own capability and the
      RLS policy's stated intent. Two new tests
      (`test_bloom_admin_can_write_directly_to_cyl_scan_latest_source`,
      `test_bloom_admin_can_write_directly_to_cyl_experiment_trait_counts`) write straight to
      each table AS `bloom_admin`, bypassing the trigger/refresh-function entirely (unlike the
      existing `test_direct_bloom_admin_write_is_maintained`, which writes to `cyl_scan_traits`
      and lets a `SECURITY DEFINER` trigger do the actual write, never exercising bloom_admin's
      own grant). Confirmed both fail against a `REVOKE`d grant and pass against the fix. - `test_unpinned_call_no_live_join_over_cyl_scan_traits` deleted outright — its real property
      (unpinned `n_traits` reads the cache, not a live join) is already correctly proven by
      content, not plan shape, by the two sibling tests immediately above it
      (`test_unpinned_n_plants_is_unaffected_by_a_corrupted_cache`,
      `test_unpinned_n_traits_reads_the_cache_not_a_live_recompute`) — a demonstrably broken
      assertion that gives false confidence is worse than no test when the real property is
      already covered elsewhere. - `test_recreating_the_trigger_does_not_block_concurrent_reads` added: holds the migration's
      exact `CREATE OR REPLACE TRIGGER` statement open (uncommitted) on one connection, times a
      concurrent plain `SELECT` on `cyl_scan_traits` from another, asserts it completes within
      2s. Confirmed this test fails (times out) when the statement is temporarily reverted to
      `DROP TRIGGER IF EXISTS` + `CREATE TRIGGER`, and passes with the fix restored. - `test_rollback_guard_table_check_branch_is_load_bearing` added: drops
      `refresh_cyl_experiment_trait_counts()` directly (leaving `cyl_experiment_trait_counts` the
      table intact) and confirms the rollback guard still raises — isolating the table-check
      branch specifically. Confirmed this test fails against the function-only (round-2-era)
      guard and passes against the current OR'd guard. - `test_concurrent_multi_pair_cross_scan_reassignments_can_deadlock_and_recover` added: two
      disjoint scan-id pairs, reassigned by two transactions in opposite pair order, barrier-
      synced. Confirmed deterministic (5/5 runs reproduced the deadlock, exactly one transaction
      failing with `DeadlockDetected` and the other committing correctly) — unlike the existing
      single-pair test, which is only probabilistic. Locks in design.md's "accepted, self-healing"
      claim about this risk as an actual regression-tested behavior, not just an assertion. - `web/types/database.types.ts`'s `cyl_scan_latest_source` hand-edit (round 4) switched from
      double- to single-quoted string literals, matching this file's own dominant style and its
      own `cyl_experiment_trait_counts` addition 320 lines above it — the only hand-edited block
      in any of the 5 tracked copies that had drifted from its file's convention. - `design.md`'s Goals section corrected: it unconditionally claimed `n_traits` staleness is
      "bounded... one refresh interval," directly contradicting D5's own round-4-added caveat
      that this bound does not currently hold (the refresh schedule isn't running yet). The Goals
      bullet now points to D5's caveat explicitly instead of asserting the bound unconditionally.
- [x] 12.4 Re-run the full `cyl`-scoped integration suite after all fixes — 381 passed, 5 skipped, up
      from 377 after round 4 (net +4: two bloom_admin direct-write tests, the
      `CREATE OR REPLACE TRIGGER` lock test, the rollback-guard branch-isolation test, and the
      multi-pair deadlock test, minus the one deleted trivially-true `EXPLAIN` test).
- [x] 12.5 Post round 5's synthesized review to PR #684. Posted successfully (`COMMENTED`, confirmed
      via `gh pr view --json reviews`).

## 13. External PR-comment review, then a sixth `/review-pr` round

The user posted a review comment directly on PR #684 (not generated by this process) and asked to
triage it alongside another round of subagent review. Triaged first, then launched round 6's 5
subagents informed of the triage results so they wouldn't re-litigate settled points.

- [x] 13.1 Triage the external review's 3 "Blocking" claims individually, verifying each rather than
      accepting or dismissing on the reviewer's word alone: - **`bloom_admin` write grant** — already fixed in round 5 (the external review was looking at an
      older commit, `cd1e7094`, than this branch's actual `HEAD` at review time, `69057ae8`). No
      action needed beyond confirming the fix is still in place. - **`bloom_workflows` loses RLS read access to `cyl_scan_traits_source`** — reproduced the exact
      `permission denied for view` error the review cited, then independently disproved the causal
      claim: swapping in the OLD, pre-this-PR view definition (which has zero dependency on
      `cyl_scan_latest_source`) reproduces the IDENTICAL failure for `bloom_workflows` — it never
      had a `GRANT SELECT` on this view, before or after this PR. A code search (`services/workflows/
pipeline.py`/`video.py`, the only code authenticating as this role) confirmed it never reads
      this view or the trait-reading RPCs at all — its only trait-table access is a narrow,
      column-scoped dedup check (`cyl_scan_traits(scan_id, source_id)`, `cyl_trait_sources(id,
metadata)`), already correctly granted. Pre-existing, out-of-scope, not a regression — not
      fixed. - **`STAGING_API_URL` secret unprovisioned** — already tracked (tasks.md 5.2/5.3, design.md D5's
      round-4 caveat); restated by the external review, not a new finding.
- [x] 13.2 Fix the external review's 2 correctly-identified, previously-unfixed issues: - Unused `import time` in `test_cyl_scan_latest_source.py` (a leftover from an earlier draft of a
      round-5 test that ended up not needing it) — removed; confirmed via `ruff check` (F401) before
      and after. - Stale PR description: the Testing checklist claimed the `database.types.ts` hand-edit "is not
      done in this PR," though the diff has included it since round 4 — corrected via `gh pr edit`,
      along with the stale "351 passed"/"structural EXPLAIN check" claims (both from before rounds
      4-5's changes) and the open questions section (`n_traits` unbounded staleness restated
      explicitly).
- [x] 13.3 Add the external review's 2 correctly-identified missing tests: - `test_concurrent_writes_to_different_scans_do_not_block_each_other` — proves
      `pg_advisory_xact_lock(scan_id)` is scoped per-scan, not coarsened to something broader. - `test_scan_with_no_trait_rows_excluded_from_n_plants` — proves a scan with zero
      `cyl_scan_traits` rows is excluded from the unpinned `n_plants` `EXISTS` semi-join, not just
      a null-accession plant (the existing test's actual coverage).
- [x] 13.4 Run the 5-subagent round-6 review, each explicitly told about 13.1-13.3's triage results so
      they wouldn't re-litigate the disproven `bloom_workflows` claim or already-fixed items. All 5
      returned. Two (code quality, security) found nothing new at BLOCKING/IMPORTANT — the first time
      more than one reviewer has converged on "holds up clean" simultaneously across 6 rounds.
- [x] 13.5 Verify and fix everything that survived: - **Two independent reviewers (testing, behavioral correctness) found the SAME real bug** in
      13.3's own new `test_concurrent_writes_to_different_scans_do_not_block_each_other`: its
      self-deadlock-in-cleanup fix (added while writing the test, mirroring round 4's own class of
      bug) only ever resolved connection A before the outer cleanup ran — if B stayed blocked for
      any reason OTHER than A's lock, `conn_b`'s still-open transaction would deadlock the cleanup
      just like the already-fixed A-side case. Closed by capturing B's backend pid upfront and, if
      B is still alive after a second join following A's commit, killing that backend directly via
      `pg_terminate_backend` from a third, independent connection (safe cross-thread, unlike closing
      `conn_b`'s own Python object) so its transaction rolls back and any locks release before
      cleanup ever runs. Verified: reproduces cleanly (no hang) against both a deliberately
      coarsened lock and the correct implementation. - **Scientific rigor's explicit recommendation, not a re-flag**: `n_traits`'s staleness had been
      raised in round 4 and again by the external review without either round closing the gap.
      Given `STAGING_API_URL`'s absence means the staleness is currently unbounded, not a bounded
      UI-lag nicety, closed this round rather than deferred a third time: added
      `n_traits_updated_at` to `get_experiment_summary_counts`'s `RETURNS TABLE` (`NULL` for pinned
      calls; the cache row's own `updated_at`, or `NULL` if never populated, for unpinned calls),
      threaded through `ExperimentSummary.trait_columns_updated_at` in `bloommcp`'s
      `supabase_reader.py`, and surfaced in `list_available_experiments`'s printed output
      (`Traits: {n} (as of {ts})` / `(never refreshed)`). Changing the RPC's return shape required
      `DROP FUNCTION` before `CREATE FUNCTION` in both the forward migration and its rollback
      (Postgres refuses `CREATE OR REPLACE FUNCTION` across a return-type change) — this also
      surfaced a latent gap in `test_migration_body_is_idempotent` (a separate, pre-existing
      bloom#625 test), fixed by having that test explicitly reset the function to a droppable state
      first, since it now runs in an environment where a later migration reshapes the same
      function's return type. New tests: `test_n_traits_updated_at_reflects_cache_staleness`,
      `test_n_traits_updated_at_is_null_when_never_refreshed`, plus 3 new `bloommcp` unit tests
      (`test_supabase_reader.py`, `conftest.py`'s fake RPC stub updated to include the new field). - **Scientific rigor's SUGGESTION**: added `test_cyl_traits_name_is_unique_not_null`, a tripwire
      (not a functional test of this change) pinning the `NOT NULL UNIQUE` constraint that makes
      `refresh_cyl_experiment_trait_counts()`'s `DISTINCT trait_id` and
      `compute_cyl_experiment_summary_counts_live`'s `DISTINCT trait_name` counting paths
      equivalent today — both functions' own comments already documented this assumption, but
      nothing previously asserted it. - **Security's SUGGESTION**: added `test_select_only_roles_without_a_raw_grant_cannot_write`
      (`bloom_agent`/`bloom_user`, parametrized) and `test_select_only_roles_with_a_raw_grant_write_zero_rows`
      (`authenticated`/`bloom_writer`, parametrized) to both `test_cyl_scan_latest_source.py` and
      `test_cyl_experiment_trait_counts.py` — symmetric with round 5's `bloom_admin` finding: a
      dedicated direct-write test existed for the one role that's SUPPOSED to write, but not for
      the roles that are supposed to be read-only. Verified empirically that the 4 roles split into
      two genuinely different failure shapes (`bloom_agent`/`bloom_user` have no raw grant at all,
      failing with `InsufficientPrivilege`; `authenticated`/`bloom_writer` retain Supabase's default
      raw grant and are instead silently filtered to zero rows by RLS) — the test suite reflects
      both shapes rather than assuming one. - **Testing's SUGGESTION**: added OpenSpec scenarios for both of 13.3's new tests (lock
      independence across unrelated scans; zero-trait-row-scan exclusion from `n_plants`) plus the
      new `n_traits_updated_at` behavior, and updated the "Aggregate experiment summary counts"
      requirement's text for the new return shape (a MODIFIED requirement, full text per OpenSpec
      convention).
- [x] 13.6 Re-run the full `cyl`-scoped integration suite after all fixes — 394 passed, 5 skipped, up
      from 383 mid-round (net +11 across 13.3/13.5: 2 concurrency/exclusion tests, 2 staleness tests,
      1 canary test, 4 negative-write tests across both files, 2 idempotency-test/migration fixes with
      no new test count of their own). Also ran `bloommcp`'s own unit suite
      (`uv run pytest tests/` from `bloommcp/`) — confirmed the pre-existing 61 `test_umap_analysis_tool.py`
      failures are unrelated to this change (reproduced identically with this round's changes
      stashed out) and that all `data_access`/`supabase_reader` tests, including the 3 new/updated
      ones, pass.
- [ ] 13.7 Post round 6's synthesized review to PR #684.

## 14. bloom#708 — scheduled (cron) refresh for production, plus the promotion-claim correction

PR #684 (this section's own predecessor sections 1-13) merged to `staging` (`cbd28c02`). This section
lands as a **new PR** against `staging` (PR #684 is already closed) but continues this same change-id,
since it directly extends D8's own still-open refresh-scheduling decision rather than introducing an
unrelated capability, and `cyl-experiment-summary-rollup` was never archived into `openspec/specs/` in
the interim (see the note at 5.7).

Triggered by re-investigating tasks.md 5.4's "resolved by redesign" claim before treating bloom#708 as
pure net-new work: `gh api`/`gh workflow list` confirmed this workflow file is still entirely
undispatchable (never promoted to `main`), meaning rounds 7-9's belief that `workflow_dispatch` alone
sidesteps the promotion dependency was never true. That correction lands alongside bloom#708's actual
feature (already applied above at 5.3/5.4/5.6, plus design.md and the workflow file's own header comment
below), since both touch the same file and the same section of design.md.

**`/review-openspec` pass (pre-implementation) found a real design gap in the first draft of this
section, since fixed in design.md's D8 addendum before any code was written:** naively resolving the
job's `environment:` key to `production` for a scheduled run would route every nightly run through
`production`'s `required_reviewers` approval gate — a scheduled run has no human present to click
"Approve," so it would sit "Waiting" indefinitely, and could even get cancelled by the *next* night's run
sharing the same concurrency group before anyone approved it. Resolved by introducing a second, ungated
GitHub Environment (`production-scheduled-refresh`) used only for the job's `environment:` key on a
`schedule`-triggered run — see design.md's D8 addendum for the full reasoning (why a scheduled trigger's
risk shape doesn't need the same per-run human gate a manual dispatch does) and the exact expressions.
This means there are **two distinct resolution expressions**, not one shared by three call sites as the
first draft assumed: a *target-host* expression (`ENVIRONMENT` env var + `concurrency.group`'s host
suffix, both resolving `schedule` → `'production'`) and a separate *job-environment-name* expression
(the job's `environment:` key, resolving `schedule` → `'production-scheduled-refresh'`).

**Landing plan**: one PR against `staging`, two commits — (1) the OpenSpec scaffold edits (this file,
design.md, proposal.md, the spec delta), (2) tests (14.1-14.2) and implementation (14.3-14.4) together,
not split into separate RED/GREEN commits — splitting them would commit an interim state with 5 failing
tests and 2 broken previously-passing ones, a confusing history entry for no real isolation benefit on
this small a diff.

**Do not archive this change until this entire section is complete.** The `cyl-experiment-summary-rollup`
spec delta (14.5, already applied as part of this proposal's own drafting) already asserts production
has an automatic cron cadence — that claim is only true once 14.1-14.4's implementation actually ships.
Running `openspec:archive` before then would copy a false statement into `openspec/specs/`.

**Superseded by Section 15's own, broader archive-gate note below (bloom#736): this section's own
"do not archive" condition is necessary but not sufficient.** 14.1-14.4 shipping makes the cron *exist*
and resolve its approval gate correctly — it does not make the RPC call it makes actually reach either
host. See 14.9's correction and Section 15.

- [x] 14.1 Add failing unit tests to `tests/unit/test_refresh_workflow_shape.py` (RED first — the workflow
      file has none of this yet). **Done — confirmed all 6 new test functions (8 collected items,
      one parametrized ×3) failed against the pre-implementation workflow file (10 failures total
      including 14.2's 2 updated tests), before any YAML change.**
      - `test_schedule_trigger_exists_for_production_only` — the workflow's `on` block has exactly one
        `schedule` entry with `cron: '17 0 * * *'` (once daily, per the user's own direction; the minute
        is deliberately non-zero — GitHub's own docs flag top-of-hour schedules, including midnight UTC,
        as exposed to elevated scheduler load/delay — not a benchmarked figure, explicitly flagged in
        design.md as revisitable once real production write-cadence telemetry exists).
      - `test_workflow_dispatch_still_present_alongside_schedule` — both trigger keys coexist in `on`.
      - `test_target_host_expression_matches_between_env_var_and_concurrency_group` — the step's
        `ENVIRONMENT` env var equals the expression `${{ github.event_name == 'schedule' && 'production'
        || github.event.inputs.environment }}` exactly, and `concurrency.group` contains that identical
        expression as a substring after stripping its literal `refresh-cyl-experiment-trait-counts-`
        prefix (not an exact-equality check like `ENVIRONMENT`'s, since `concurrency.group`'s value is
        prefixed).
      - `test_resolution_truth_table_for_schedule_vs_dispatch` — **must extract the two expressions'
        literal branch values directly from the live YAML (e.g. via a regex over the raw expression
        strings pulled from `ENVIRONMENT` and the job's `environment:` key), not hardcode an
        independently-asserted expected truth table** — a test that just restates the intended logic in
        parallel Python, decoupled from the actual file content, would pass unchanged even if the real
        expression were wrong (the exact "false confidence" failure mode this same tasks.md already
        killed once at section 12.3's deleted `EXPLAIN`-based test). Parametrize over the 3 real cases:
        (`event_name='schedule'` → target-host `'production'`, job-environment-name
        `'production-scheduled-refresh'`), (`event_name='workflow_dispatch'`, `inputs.environment=
        'staging'` → target-host `'staging'`, job-environment-name `'staging'`), (`event_name=
        'workflow_dispatch'`, `inputs.environment='production'` → target-host `'production'`,
        job-environment-name `'production'` — the case that specifically proves a manual dispatch to
        production is NOT silently routed to the ungated `production-scheduled-refresh` Environment).
        This is the right layer for this specific property since GitHub Actions expressions are
        evaluated server-side before the job's shell ever starts — no local bash subprocess can exercise
        the `&&`/`||` ternary the way the existing `test_environment_input_resolves_to_the_right_url_and_key`
        exercises the bash-level `case`/`esac` logic.
      - `test_scheduled_environment_name_appears_only_in_the_schedule_branch` — a structural check
        (independent of the truth-table test above) that the literal `production-scheduled-refresh`
        appears exactly once in the job's `environment:` expression, and only inside the
        `github.event_name == 'schedule'` branch — guards against a copy-paste that duplicates it into
        the `workflow_dispatch` fallback branch, which would silently strip production's approval gate
        for a manual dispatch (the more dangerous direction, opposite of the bug this section fixes).
      - `test_cron_expression_is_structurally_valid` — the cron string has exactly 5 whitespace-separated
        fields, each either `*` or a numeric value within that field's valid range (a copy-paste typo —
        wrong field count or an out-of-range value — would otherwise only surface after promotion to
        `main`, possibly not until a missed run).
      Confirm all six fail against the current (dispatch-only, single-expression) workflow file.
- [x] 14.2 Two explicit test-file edits (still RED, no workflow YAML touched yet). **Done.**
      - **Delete** `test_no_schedule_trigger_only_workflow_dispatch` outright — its premise (no schedule
        trigger exists) is now false, and its dispatch-still-present half is redundant with 14.1's new
        `test_workflow_dispatch_still_present_alongside_schedule`.
      - **Update** `test_job_has_environment_protection_gate`'s assertion from the bare
        `${{ github.event.inputs.environment }}` to the job-environment-name expression (`schedule` →
        `'production-scheduled-refresh'`, else the dispatch input), and
        `test_concurrency_group_is_scoped_by_environment`'s assertion to check for the *target-host*
        expression's substring instead (not the job-environment-name one — `concurrency.group` stays
        keyed by which database is actually hit, not by which Environment gates the job, so a scheduled
        production run and a manual `workflow_dispatch` production run still serialize against each
        other; see design.md's D8 addendum for why that's the correct choice). Confirm both now fail
        (they currently pass against the old bare expression, which is about to change).
- [x] 14.3 Implement in `.github/workflows/refresh-cyl-experiment-trait-counts.yml`. **Done —
      confirmed all of 14.1/14.2's tests pass (23/23).**
      - Add `on: schedule` with `cron: '17 0 * * *'`, alongside the existing `workflow_dispatch`.
      - `ENVIRONMENT` (the step's env var) and `concurrency.group`'s host suffix resolve off
        `${{ github.event_name == 'schedule' && 'production' || github.event.inputs.environment }}`.
      - The job's `environment:` key resolves off the **separate**
        `${{ github.event_name == 'schedule' && 'production-scheduled-refresh' ||
        github.event.inputs.environment }}` expression — a `schedule` event has no `github.event.inputs`
        context at all, so a bare `github.event.inputs.environment` read would resolve to an empty string
        for a scheduled run either way, breaking both the approval gate and the bash `case`/`esac`
        dispatch.
      - `production-scheduled-refresh` needs no new secret provisioning (`PROD_SERVICE_ROLE_KEY` is a
        repository-level secret, visible regardless of which Environment name is referenced) and no
        manual repository-settings step (GitHub auto-creates a referenced Environment with no protection
        rules on first use, once this file is on the default branch).
      - `workflow_dispatch` against either host is otherwise unchanged — still requires the explicit
        `environment` choice, still gated by that Environment's existing protection rules.
      Confirm all of 14.1/14.2's tests now pass.
- [x] 14.4 Correct the wrong "workflow_dispatch avoids promotion" claim everywhere it still appears:
      the workflow file's own header comment, AND `tests/unit/test_refresh_workflow_shape.py`'s module
      docstring (found by `/review-openspec` round 1's spec-quality pass — the first task draft of this
      section only named the workflow file's comment, missing the test file's own equally-wrong
      docstring claim). State plainly in both: both trigger types require this file on `main` before
      either can fire at all, and that neither this PR nor its predecessor made staging/production
      dispatch actually work — both remain gated on this repo's normal `staging -> main` promotion
      practice. (tasks.md 5.3/5.4/5.6, proposal.md's Impact bullet, and design.md's Goals/Non-Goals
      bullet, D5's cross-reference, and the Open Questions D8 section already carry this correction as
      of this change.) **Also fold in the two Python source comments `/review-openspec` round 2's
      documentation pass found describing the same now-superseded "no automatic cadence for either
      environment" design** (not a "workflow_dispatch avoids promotion" claim specifically, but the same
      class of now-false "as of this change, production stays on-demand only, bloom#708 not yet built"
      framing): `bloommcp/src/bloom_mcp/data_access/ports.py`'s docstring (~lines 142-147) and
      `bloommcp/src/bloom_mcp/sections/core/list_available_experiments.py`'s module comment (~lines
      13-19). Update both to reflect production's new automatic cadence. **Done — and `/review-pr`'s
      code-level pass on the implemented diff found a real bug this comment-only framing missed: the
      actual user-facing string `_traits_note()` returns (not just the module comment above it) still
      unconditionally asserted "trait counts refresh on demand only, not automatically" — false for a
      production row once this section ships. Fixed the string to environment-neutral wording ("may be
      older than the environment's own refresh cadence"), and updated
      `bloommcp/tests/sections/core/test_list_available_experiments.py`'s three assertions (and its
      module docstring) that pinned the old string verbatim — confirmed all 6 tests in that file fail
      against the old string and pass against the fix.**
- [x] 14.5 Update the `cyl-experiment-summary-rollup` capability's spec delta
      (`openspec/changes/fix-cyl-scan-traits-latest-rollup/specs/cyl-experiment-summary-rollup/spec.md`):
      since this capability is still `ADDED` (never archived), edit the existing requirement text and its
      "The cache is invoked on a schedule, not on every write" scenario in place — production now has an
      automatic cron cadence, staging remains dispatch-only — rather than layering a `MODIFIED` delta on
      top of an unarchived `ADDED` one (`/review-openspec`'s spec-quality pass confirmed this is the
      correct convention here, precisely because no archived base text exists yet to modify against).
      Added a scenario asserting `refresh_cyl_experiment_trait_counts()` is never invoked directly from a
      raw `cyl_scan_traits` write regardless of which trigger fired, and a second scenario asserting
      production's automatic-schedule / staging's dispatch-only split. **Done, ahead of 14.1-14.4's
      implementation** — see the standing archive-gate warning above.
- [x] 14.6 Update the two docs `/review-openspec`'s documentation pass found would otherwise go stale
      once production gets an automatic schedule (both currently describe the workflow as
      dispatch-only-for-both-environments, which becomes wrong). **Round 2's re-verification found round
      1's task named only one stale sentence per file when each file's whole surrounding paragraph is
      stale** — broadened accordingly:
      - `bloommcp/docs/data-access-roadmap.md` (~lines 79-88, the "Questions for Benfica" #5 entry): not
        just the trigger-design sentence but also "Production is expected to eventually need its own
        automatic (scheduled) cadence... tracked as bloom#708" — that future-tense framing is wrong once
        #708 ships. Cross-reference design.md's D8 addendum rather than restate it, matching this
        change's own established doc convention.
      - `_WIKI/BLOOMMCP/README.md` (~lines 186-196): not just the "unbounded staleness... until someone
        dispatches a refresh" sentence, but the whole paragraph — "refreshed on demand only via a
        manually-dispatched GitHub Action... rather than an automatic schedule" is flatly false for
        production once implemented, and the paragraph's own "production is expected to get its own
        automatic cadence eventually" framing needs the same past-tense correction as the roadmap doc.
        Qualify the "unbounded staleness" language to staging-only.
      Run `prettier --check`/`--write` on both after editing.
- [x] 14.7 Run the full `tests/unit/test_refresh_workflow_shape.py` file; confirm all tests pass (23
      expected: 16 existing, minus 1 removed (`test_no_schedule_trigger_only_workflow_dispatch`), plus 8
      net new collected items from 14.1's 6 new test functions (one parametrized ×3) —
      `test_cron_expression_is_structurally_valid`,
      `test_workflow_dispatch_still_present_alongside_schedule`,
      `test_schedule_trigger_exists_for_production_only`,
      `test_target_host_expression_matches_between_env_var_and_concurrency_group`, and
      `test_scheduled_environment_name_appears_only_in_the_schedule_branch` each contribute 1, and the
      parametrized `test_resolution_truth_table_for_schedule_vs_dispatch` contributes 3 — the two tests
      updated in 14.2 keep their existing collected-item count).
- [x] 14.8 `openspec validate fix-cyl-scan-traits-latest-rollup --strict` passes. **Confirmed.**
- [ ] 14.9 Verify the promoted-to-`main` dispatch, the live cron, the approval-gate resolution, and actual
      RPC delivery. **Promotion has since happened (2026-08-24/25) and the workflow's first live scheduled
      run occurred — see the CORRECTED note below for exactly what it confirmed and what it didn't.** The
      bullets immediately below were the original pre-promotion verification checklist (still relevant for
      the Environment-config checks); the RPC-delivery half is superseded by the CORRECTED note and by
      Section 15. While verifying:
      - Confirm via `gh api repos/.../environments/production-scheduled-refresh` that it still carries
        zero protection rules (`/review-openspec` round 2's CI/CD pass flagged that nothing prevents a
        repo admin from later adding `required_reviewers` to this Environment, silently reintroducing the
        exact bug this section fixes — an accepted, not eliminated, risk per design.md's D8 addendum).
      - **(Added after `/review-pr` round 2, against the pushed PR)** Also check for an **org-level**
        default environment-protection policy, not just this one Environment's own settings — a
        repo-scoped check alone wouldn't catch an org-wide default that auto-attaches
        `required_reviewers` to any newly-created Environment.
      - **(Added after `/review-pr` round 2)** Checking the Environment's static configuration isn't the
        same as confirming the actual first scheduled firing succeeded — check the Actions tab the
        morning after the first post-promotion midnight run specifically, not only the Environment's
        protection-rule config beforehand.
      - **CORRECTED (Section 15, bloom#736): split into what this task actually verified vs. what it
        never could have.** The promoted-to-`main` dispatch and the approval-gate/environment-name
        resolution ARE confirmed working — the workflow's first live scheduled run (2026-08-25T01:51:22Z,
        run 32799136668) started executing immediately under `production-scheduled-refresh` with no
        pending-approval wait, and `ENVIRONMENT` resolved to `production` correctly. **The actual RPC
        delivery has never once succeeded via GitHub Actions**: that same run failed in 12s with
        `curl: (28) Failed to connect to bloom.salk.edu port 443 after 5001 ms: Timeout was reached` —
        `runs-on: ubuntu-latest` (a GitHub-hosted runner) has no network route to either host, the same
        limitation `deploy.yml` already documents for its own jobs. This task is left unchecked; do not
        check it off until Section 15's own 15.7 confirms an actual successful RPC call post-fix. Section
        15 owns closing this gap.
- [x] 14.10 Run the 5-subagent `/review-pr` pass against the implemented diff (not yet a GitHub PR — run
      locally against the working tree). All 5 returned; **no BLOCKING findings.** One real, already-fixed
      bug found: see 14.4's note above (the `_traits_note()` user-facing string, not just its module
      comment, still unconditionally asserted the old "not automatically" claim). IMPORTANT findings, all
      fixed: a test docstring's meta-reference to "`/review-openspec` round 1 finding" replaced with the
      actual reason (future readers won't have this conversation's context); `type: choice`'s lack of
      server-side enforcement (a crafted `workflow_dispatch` could execute the job under the ungated
      Environment, though traced and confirmed the bash `case` guard still blocks the actual RPC call —
      documented as an accepted risk in design.md's D8 addendum, not code-fixed, since the real call path
      is already closed); two more residual risks (a scheduled run can be silently dropped entirely under
      GitHub platform load with zero visibility; no coordination with `deploy.yml`'s production migrations)
      — both documented as accepted, self-healing risks. SUGGESTIONS applied: trimmed the workflow header's
      debug-journal-style investigation notes to a durable one-line summary pointing at design.md; added a
      comment explaining why the target-host expression is repeated rather than centralized in a
      workflow-level `env:` (the `concurrency:` context can't read it).
- [x] 14.11 **Round 2 of `/review-pr`, run against the actual pushed PR #728** (not the local working
      tree this time) — real CI status, real diff, checked for existing Copilot comments (none). All 5
      subagents independently re-verified round 1's fixes hold up on the committed/pushed code (traced
      character-by-character, confirmed zero drift from the comment-only polish commits) and confirmed CI
      is genuinely green (the relevant test job is misleadingly named "Python Security Audit for CVEs" —
      pulled its raw log directly rather than trusting the label). **No BLOCKING findings.** Three real,
      non-blocking findings, all documented in design.md's D8 addendum rather than code-fixed (no actual
      bug reachable, just follow-up verification gaps): the `_STALE_AFTER` 2-day threshold's tension with
      the new 24h production cadence; an org-level environment-protection-policy check folded into 14.9;
      and an explicit first-live-firing check (not just static config) folded into 14.9. One transient,
      false-alarm finding investigated and resolved: a system notification mid-review showed the
      workflow file's on-disk content reverted to its pre-#708 state — traced to a review subagent's own
      scratch `.bak_current` copy (visible as an untracked file), not an actual revert; confirmed via
      `git diff HEAD` (empty) and a byte-for-byte comparison against the committed blob that the real file
      was never altered, then removed the stray untracked file. Posted the synthesized review to PR #728
      as a comment (GitHub disallows self-approval/self-request-changes).
- [x] 14.10 Run the 5-subagent `/review-pr` pass against the implemented diff (not yet a GitHub PR — run
      locally against the working tree). All 5 returned; **no BLOCKING findings.** One real, already-fixed
      bug found: see 14.4's note above (the `_traits_note()` user-facing string, not just its module
      comment, still unconditionally asserted the old "not automatically" claim). IMPORTANT findings, all
      fixed: a test docstring's meta-reference to "`/review-openspec` round 1 finding" replaced with the
      actual reason (future readers won't have this conversation's context); `type: choice`'s lack of
      server-side enforcement (a crafted `workflow_dispatch` could execute the job under the ungated
      Environment, though traced and confirmed the bash `case` guard still blocks the actual RPC call —
      documented as an accepted risk in design.md's D8 addendum, not code-fixed, since the real call path
      is already closed); two more residual risks (a scheduled run can be silently dropped entirely under
      GitHub platform load with zero visibility; no coordination with `deploy.yml`'s production migrations)
      — both documented as accepted, self-healing risks. SUGGESTIONS applied: trimmed the workflow header's
      debug-journal-style investigation notes to a durable one-line summary pointing at design.md; added a
      comment explaining why the target-host expression is repeated rather than centralized in a
      workflow-level `env:` (the `concurrency:` context can't read it). Still outstanding, matching this
      change's own established discipline: this section still needs to be **posted as an actual PR** (not
      yet opened) and go through the normal GitHub review flow before merging — this local pass replaces
      neither.

## 15. bloom#736 — `runs-on: ubuntu-latest` has no network route to either host; every RPC delivery has failed

Section 14 shipped a working *approval-gate and target-host resolution* design — confirmed by the
workflow's first live scheduled run actually starting immediately under `production-scheduled-refresh`,
with no pending-approval wait, once this file was finally promoted to `main` (2026-08-24/25). But that
same run (`2026-08-25T01:51:22Z`, run
[32799136668](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/actions/runs/32799136668))
failed in 12 seconds:

```
curl: (28) Failed to connect to bloom.salk.edu port 443 after 5001 ms: Timeout was reached
```

`runs-on: ubuntu-latest` (line 91) is a GitHub-hosted runner. `.github/workflows/deploy.yml` already
documents, in its own comment (lines 38-40), that GitHub-hosted runners "have no route to the Salk
server" — `deploy.yml`'s real deploy jobs run on a self-hosted runner labeled
`["self-hosted", "linux", "salk-network"]` instead, falling back to `ubuntu-latest` only as a deliberate
fail-fast escape hatch for unwedging a stuck required check (`workflow_dispatch`-only, not this
workflow's `schedule`/always-on-`workflow_dispatch` shape). `refresh-cyl-experiment-trait-counts.yml`
never adopted that pattern, from its introduction in PR #684 through Section 14/bloom#708's cron
addition in PR #728 — so every run of this workflow, manual or scheduled, against either host, has been
unreachable on the network hop, on every invocation, since the workflow first existed. Filed as
[bloom#736](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/736).

**Why this lands as a section on this change-id rather than a direct, un-proposed fix, despite
qualifying under `openspec/AGENTS.md`'s "Skip proposal for: Bug fixes (restore intended behavior)" /
"Configuration changes" guidance (`/review-openspec` round 1's spec-quality pass flagged the absence of
this justification):** the behavior this bug prevents is exactly what this same change's own, still
unarchived `cyl-experiment-summary-rollup` scenario ("Production refreshes on an automatic schedule")
asserts. Fixing the workflow outside this change-id would let that scenario's claim — true only once the
schedule actually *delivers* a refresh, not merely fires — get archived into `openspec/specs/` without
ever having been made true, the same false-statement-at-archive risk Section 14's own archive-gate note
already exists to prevent. Continuing here, under the same gate, is what keeps that risk closed.

**Precisely what 14.9 did and did not verify, so this section doesn't re-litigate the settled half:**
confirmed working — `ENVIRONMENT` resolving to `production`, and the job starting immediately under the
ungated `production-scheduled-refresh` Environment (Section 14's whole point). Never verified, because
nothing before this section's own investigation surfaced the gap — the RPC call itself reaching either
host over GitHub Actions. See 14.9's own correction, added above, for the split.

**Do not treat a manual `curl https://bloom.salk.edu/api` succeeding from a local/dev machine as
evidence this was ever transient, or is already fixed without this section's change** — that traffic
rides a trusted Salk network path GitHub-hosted runners lack by design; this exact check was already run
and gave a false-reassuring result before the real cause (documented in `deploy.yml` itself) was found.

**Decided scope (confirmed with the user; not re-opened during implementation):**
- Change `runs-on: ubuntu-latest` to `runs-on: ["self-hosted", "linux", "salk-network"]` — the same
  label `deploy.yml` uses — applied **unconditionally**, to the single `refresh` job. This workflow has
  exactly one job serving all three trigger paths (staging dispatch, production dispatch, production
  schedule) via the `ENVIRONMENT` bash `case`; there is no per-host job boundary that would let staging
  and production carry different runners without a larger restructuring this bug doesn't call for. The
  user chose applying the label to both hosts over deliberately leaving staging on `ubuntu-latest` as a
  negative control.
- No `ubuntu-latest` escape-hatch `workflow_dispatch` input, unlike `deploy.yml`'s own `runner` input:
  this job is not a required/blocking check the way `deploy.yml`'s jobs are, and an escape hatch would
  not help this workflow's unattended `schedule` trigger's failure mode anyway.
- New failure mode accepted as-is, not mitigated: if the self-hosted `salk-network` runner is offline, a
  run now queues instead of failing fast in ~12s the way it does today. Same trade-off `deploy.yml`
  already accepts for its own real deploys, and this job is lower-stakes than those (not a required
  check). **Correction from `/review-openspec`'s CI/CD pass — the bound on this is NOT
  `concurrency.group`'s `cancel-in-progress: true`** (that flag only cancels an already-**running** job;
  GitHub's own docs state a **queued/pending** job in the same group is already superseded by a newer
  trigger under the group's *default* behavior, independent of `cancel-in-progress`). The actual bound
  differs by host: for `production`, the next day's cron supersedes a stuck queued run (default
  pending-job supersession, not `cancel-in-progress`) on top of GitHub's own independent ~24h
  hard-cancel-queued-job limit; for `staging`, nothing re-triggers automatically, so a queued manual
  dispatch relies solely on that same ~24h hard limit.
- **New risk, found by `/review-openspec`'s CI/CD pass, not previously documented: this job now shares a
  runner pool with `deploy.yml`'s deploy jobs, which is a single physical machine, not an autoscaling
  fleet** (`deploy.yml`'s own `concurrency.group: deploy-bloom` comment states this explicitly — "single
  Salk server, single docker daemon"). The two workflows use different concurrency groups, so nothing
  prevents a `deploy.yml` run (`timeout-minutes: 30`) from occupying the only matching runner while this
  job queues behind it for up to ~30 minutes — a distinct failure mode from "runner offline," not
  previously called out anywhere in this design. Accepted, not mitigated: this job has no fixed deadline
  a human is waiting on, and `timeout-minutes: 2` only bounds *execution* time once a runner picks the job
  up, not queued time — call out both facts explicitly in the workflow's own comment (15.3) rather than
  leaving `timeout-minutes: 2` to misleadingly imply the queuing exposure is bounded by it.
  **Caveat, found by `/review-openspec` round 2's CI/CD pass: "single physical machine" is
  `deploy.yml`'s own comment's characterization, not something 15.2's runner-registration check actually
  counts.** If more than one runner process/host is ever registered under this exact label set (self-hosted
  runner setups commonly support this), the two workflows could run concurrently and this ~30-minute
  contention risk wouldn't apply — not re-verified here, since 15.2 only confirms a runner online, not
  how many.
- This job needs no checkout (`permissions: {}`, no `actions/checkout` step) and only runs `curl`/`bash`
  against two hardcoded `API_URL` literals — low blast radius; the self-hosted runner gains no new
  capability this job could misuse.

- [x] 15.1 Add failing unit tests to `tests/unit/test_refresh_workflow_shape.py` (RED first — the
      current file has no assertion on `runs-on` at all):
      - `test_job_runs_on_self_hosted_salk_network` — asserts
        `workflow["jobs"][JOB]["runs-on"] == ["self-hosted", "linux", "salk-network"]`.
      - **(Added per `/review-openspec`'s testing pass, guarding the "no escape hatch" scoping decision
        above against a future copy-paste of `deploy.yml`'s ternary pattern)**
        `test_job_runs_on_is_unconditional_not_a_ternary` — asserts `runs-on` is a plain `list` (not a
        `${{ ... }}` expression string) and that `"ubuntu-latest"` does not appear in it.
      Confirm both fail against the current `runs-on: ubuntu-latest`. **Done — both failed as expected
      (`AssertionError`s against `'ubuntu-latest'`) before 15.3's implementation.**
- [x] 15.2 **Independently doable now — does not require this PR to merge or promote, since `deploy.yml`
      already depends on this same label today.** `/review-openspec` round 2's testing pass found the
      original wording of this task ambiguous between two real possibilities, neither confirmed anywhere
      in this repo: the runner could be registered at the **repo** level or the **org** level, and if
      org-level, could be scoped to a runner group that may or may not include `bloom` as an authorized
      repository. Resolve, in order, rather than guessing:
      - `gh api repos/Salk-Harnessing-Plants-Initiative/bloom/actions/runners` first — a non-empty result
        listing a runner with exactly the labels `self-hosted`, `linux`, `salk-network` settles this
        directly (repo-level registration, visible to this repo by construction).
      - If that returns empty, the runner is org-level: `gh api orgs/Salk-Harnessing-Plants-Initiative/actions/runners`
        to find it, THEN `gh api orgs/Salk-Harnessing-Plants-Initiative/actions/runner-groups` to confirm
        its runner group's repository access actually includes `bloom` — an org-level runner that's
        online but scoped to a different runner group would look "available" while still being
        unreachable by this workflow, the same false-confidence gap 15.1's YAML-shape test already has
        for a typo'd label.
      Either way, confirm the runner's status is `online`, not just that it's registered. **Done —
      `gh api repos/Salk-Harnessing-Plants-Initiative/bloom/actions/runners` returned one runner
      (`bloom-prod-runner`, `status: online`, `busy: false`) registered at the repo level with exactly
      the labels `self-hosted`/`Linux`/`X64`/`salk-network` — settled directly, no org-level fallback
      needed.**
- [x] 15.3 Implement, in one commit:
      - `.github/workflows/refresh-cyl-experiment-trait-counts.yml`: change line 91's
        `runs-on: ubuntu-latest` to `runs-on: ["self-hosted", "linux", "salk-network"]`. Update the
        file's header comment with **one short paragraph** stating plainly that GitHub-hosted runners
        have no network route to either host, cross-referencing `deploy.yml`'s own comment and bloom#736
        — **do not restate the run ID, timestamp, or curl output in this comment; those already live in
        design.md/tasks.md** (`/review-openspec`'s documentation pass: this file's own header already
        favors pointing at design.md over re-narrating it, and this fix's evidence is detailed enough
        elsewhere that copying it a third time would drift out of sync eventually). Also add a one-line
        comment on `timeout-minutes: 2` noting it bounds only execution time once a runner is assigned,
        not queued-for-a-runner time.
      - `deploy.yml`: add a one-line cross-reference near its `runs-on` ternary (lines ~37-41) noting
        that `refresh-cyl-experiment-trait-counts.yml` now also depends on the
        `["self-hosted", "linux", "salk-network"]` runner, **and that the two workflows use different
        `concurrency.group`s, so a deploy job occupying the (single-machine) runner can make this refresh
        job queue behind it for up to `deploy.yml`'s own `timeout-minutes: 30`** — the contention risk
        found by `/review-openspec`'s CI/CD pass, not just an availability note. Confirm via `git diff`
        that no `runs-on:` value in `deploy.yml` itself is touched, only the comment.
      Confirm 15.1's tests now pass. **Done — both new tests pass; `git diff deploy.yml` confirmed
      only the comment changed, no `runs-on:` value touched.**
- [x] 15.4 Run the full `tests/unit/test_refresh_workflow_shape.py` file; confirm zero regressions from
      15.1/15.3's change (no other test in that file asserts anything about `runs-on`, so none should be
      affected). **Done — 25 passed, 0 failed (23 pre-existing + 2 new).**
- [x] 15.5 `openspec validate fix-cyl-scan-traits-latest-rollup --strict` passes. **Confirmed.**
- [x] 15.6 **Part of the same combined commit as 15.1/15.3** (`/review-openspec` round 2's git-workflow
      pass: this task was added during the review-fix pass without saying which commit it belongs to —
      it's a tiny, thematically identical doc caveat with no conflict risk, so it lands with the rest
      rather than as a separate commit). Two doc sites assert the same now-falsified claim as settled
      fact — production's `n_traits` staleness "bounded to roughly one refresh interval" — when the RPC
      has never once actually reached production (bloom#736):
      - `_WIKI/BLOOMMCP/README.md`'s "Supabase data access" section (lines ~187-194): "Staleness is
        therefore bounded to roughly one refresh interval on production, but still unbounded on staging
        until someone dispatches a refresh there." Caveat pending this section's own 15.7, e.g.
        "...bounded to roughly one refresh interval on production, once bloom#736 (Section 15) confirms
        an actual successful refresh — unbounded until then, identically to staging today."
      - **(Found by `/review-openspec` round 2's documentation pass, missed in round 1)**
        `bloommcp/src/bloom_mcp/sections/core/list_available_experiments.py`'s module-level comment
        (lines ~13-21, directly above `_STALE_AFTER`): "a PRODUCTION row's lag is bounded to roughly one
        refresh interval, but a missed or delayed scheduled run would otherwise look identical to
        ordinary lag too" — the same claim, in code-comment form, that 14.4 already fixed once for the
        user-facing `_traits_note()` string but never touched here. Caveat identically. (The
        `_traits_note()` string itself, "may be older than the environment's own refresh cadence," stays
        as-is — already appropriately hedged, no change needed.)
      Run `prettier --check`/`--write` on the markdown file after editing, matching this change's own
      established convention (7.3, 14.6); run `black`/`ruff` on the Python file per this repo's normal
      pre-commit scope. **Done — both files caveated; `prettier --check` clean on the markdown file;
      `black --check` clean on the Python file (comment-only edit); `ruff check` on the Python file
      shows 2 pre-existing `UP045` findings on an untouched function signature (line 27, not part of
      this edit's diff) — confirmed via `git diff` unrelated to this change, left as-is.**
- [ ] 15.7 **Genuinely blocked on this section's own PR merging, promoting to `main`, AND a self-hosted
      runner actually being available — same "verify the real thing, not just static config" discipline
      as 14.9, not something this PR can complete on its own. Not part of this PR's own commit(s) —
      tracked here as a required follow-up, landed in a separate commit/PR once it actually happens.**
      Once promoted:
      - Manually dispatch (`workflow_dispatch`, `environment: staging`) and confirm, via
        `gh run view <id> --json jobs -q '.jobs[].runner_name'` or the Actions UI's "Runner" field (not
        just timing), that the job was picked up by a self-hosted runner rather than left queued, and
        that the `curl` call succeeds (HTTP 200/204) against `staging.bloom.salk.edu`.
      - Do the same against `environment: production`.
      - Check the Actions tab the morning after the next scheduled (`17 0 * * *`, i.e. 00:17 UTC) run and
        confirm it succeeded end-to-end — the check 14.9 could never actually complete, since every prior
        opportunity failed on the network hop before this fix existed.
      - Mark 14.9 checked off only once this task confirms an actual successful RPC delivery — not before.
- [ ] 15.8 Once 15.7 confirms a real successful run against both hosts (in its own separate follow-up
      commit/PR, not this section's implementation PR): update 14.9's own checkbox to `[x]` with a
      one-line pointer to 15.7's evidence, remove 15.6's "once bloom#736 confirms..." caveat now that
      it's resolved, and update this change's standing archive-gate note (Section 14's, and this
      section's own below) to reflect that both are now closed.

**`/review-pr` pass against PR #738 (the implementation PR for 15.1-15.6): 5 subagents, zero BLOCKING
findings, several IMPORTANT ones — two closed here with new regression tests (TDD), two closed with
design.md documentation, one filed as a separate follow-up (out of this PR's own scope).**

- [ ] 15.9 **(Testing finding: doc-caveat wording had no regression test, despite in-repo precedent —
      `tests/unit/test_bloommcp_local_mode_docs.py` already pins required/banned phrases in
      `_WIKI/BLOOMMCP/README.md`, and 14.4 already treated this exact module's staleness wording as
      test-worthy.)** Add `tests/unit/test_refresh_workflow_staleness_docs.py` (RED first), mirroring
      `test_bloommcp_local_mode_docs.py`'s banned/required-phrase pattern (whitespace-normalized) across
      both `_WIKI/BLOOMMCP/README.md` and `bloommcp/src/bloom_mcp/sections/core/list_available_experiments.py`:
      - Banned: the pre-15.6 unconditional claim that production staleness "is [therefore] bounded to
        roughly one refresh interval" (without the bloom#736/15.7 caveat).
      - Required: the corrected, conditional wording — staleness is bounded only once bloom#736/Section
        15 confirms an actual successful refresh, unbounded until then.
      Confirm both new test functions FAIL against each file's pre-15.6 wording (temporarily revert each
      file to confirm), then PASS against the current (post-15.6) text; restore. **Done — both files
      independently confirmed to fail when reverted (checked one file at a time so each failure is
      attributable), pass when restored.**
- [x] 15.10 **(Testing finding: the two new `runs-on` tests in 15.1 assert against a hardcoded expected
      value — neither catches this workflow's label list silently drifting out of sync with
      `deploy.yml`'s own copy of the same three labels, the one drift this repo CAN detect without a
      live network call to GitHub's runner-registration API.)** Add
      `test_runner_label_matches_deploy_ymls_self_hosted_label` to
      `tests/unit/test_refresh_workflow_shape.py` (RED first): extract the label list from
      `deploy.yml`'s `fromJSON('[...]')` literal via regex (matching
      `test_resolution_truth_table_for_schedule_vs_dispatch`'s own "extract from the live YAML, don't
      hardcode an independently-asserted value" discipline) and assert it equals this workflow's
      `runs-on` list exactly. Confirm it passes today (both currently
      `["self-hosted", "linux", "salk-network"]`); then verify it actually catches drift by temporarily
      changing one file's label list and confirming the test fails, then restoring. **Explicitly out of
      scope, and not fixable by a unit test**: live verification that `salk-network` still matches an
      actually-registered, `online` GitHub Actions runner — that requires the network call 15.2 already
      made once, manually; periodic re-verification of the live registration remains a 15.7-adjacent
      manual/operational check, not something `tests/unit/` (deliberately offline, per this repo's own
      convention) can own. **Done — passes today; confirmed it catches drift by temporarily changing
      one label to `salk-network-TYPO`, confirming the test failed with the exact mismatch, then
      restoring.**
- [x] 15.11 **(Security finding: the shared, long-lived `salk-network` runner is a materially different
      trust boundary for `SERVICE_ROLE_KEY` than the previous ephemeral `ubuntu-latest` VM was, and
      design.md never named this.)** Add a paragraph to design.md's Section 15 addendum: the `curl`
      call's `Authorization: Bearer ${SERVICE_ROLE_KEY}` argument is visible via `ps`/`/proc/<pid>/cmdline`
      to any other process on the same host for the few seconds the command runs; on the previous
      single-tenant, throwaway `ubuntu-latest` VM that was a non-issue, but the shared `salk-network`
      runner also runs `deploy.yml`'s own deploy jobs, and a host-level compromise now yields a path to a
      live, full-RLS-bypass credential across every future run, not just one. Accepted, not mitigated
      (GitHub already masks the value in *logs*; the process-list exposure is a materially smaller,
      already-existing risk `deploy.yml`'s own jobs accept for their own secrets on this same host) —
      documented so a future reader isn't the first to notice it. **Done.**
- [x] 15.12 **(Behavioral-correctness finding: design.md documents `deploy.yml`-vs-refresh runner
      contention but not refresh-vs-refresh contention.)** Add a one-line addendum to the same design.md
      section: with exactly one registered `salk-network` runner (confirmed via 15.2's `gh api` check), a
      `staging` dispatch and a `production` dispatch/schedule now also serialize against each other
      purely by runner scarcity — new behavior, since elastically-scaled `ubuntu-latest` runners never
      contended with each other. Not a correctness bug (each host's own `concurrency.group` is untouched
      and still independent, so nothing races or corrupts) — noted for a future capacity-planning reader.
      **Done.**
- [x] 15.13 Run the full `tests/unit/` suite (or at minimum `test_refresh_workflow_shape.py` and the new
      `test_refresh_workflow_staleness_docs.py`); confirm zero regressions. `openspec validate
      fix-cyl-scan-traits-latest-rollup --strict` passes. Push as an additional commit to PR #738 (same
      branch, same PR — these are review fixes to already-open work, not a new proposal). **Done — 28/28
      passed across both files; `openspec validate --strict` passes.**
- [x] 15.14 **(Scientific-rigor finding, explicitly out of this PR's own scope — not fixed here.)** File
      a separate GitHub issue: spot-check whether `cyl_experiment_trait_counts` currently holds any row
      with a deceptively recent `updated_at` (e.g. from the migration's one-time initial population, per
      design.md's Migration Plan M2) that would let `_traits_note()`'s 2-day threshold show a plain
      `(as of TIMESTAMP)` with no staleness caveat, despite the automated refresh pipeline never having
      actually succeeded. A live-database check, not a code fix — tracked separately rather than
      expanded into this CI-runner PR's scope. **Done — filed as
      [bloom#740](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/740).**

**Do not archive this change until Section 14 AND this section are both complete.** Section 14 makes the
scheduled cron *exist* and resolve its approval gate correctly; this section makes the RPC call it
issues actually reach a host. The `cyl-experiment-summary-rollup` spec delta's "production refreshes on
an automatic schedule" scenario is only true in the sense that matters — the schedule actually delivering
its refresh — once both sections ship. Running `openspec:archive` before 15.7 confirms a real successful
run would leave that scenario asserting behavior that has, to date, never once actually happened.
