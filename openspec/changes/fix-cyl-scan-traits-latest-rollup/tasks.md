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
- [ ] 0.2 Re-check `supabase/migrations/`'s newest file on `origin/staging` immediately before opening the
      PR (this proposal was drafted against `20260807000000_get_experiment_summary_counts.sql` as the
      tip) and choose migration timestamps later than that.
- [ ] 0.3 Confirm PR #654 is closed with a comment pointing to this PR before this PR opens (or
      immediately after) — needs the user's own action per repo convention (Claude does not close PRs
      without explicit confirmation, already given for this change).

## 1. `cyl_scan_latest_source` table + trigger (RED first) — design.md D1-D3

- [ ] 1.1 Add `tests/integration/test_cyl_scan_latest_source.py` using `test_cyl_read_path.py`'s existing
      fixtures/helpers. Write failing tests first (table doesn't exist yet):
      - A fresh insert via `insert_cyl_result_envelope` creates a `cyl_scan_latest_source` row for a
        brand-new scan with `max_source_id` equal to that source's id.
      - A rerun (new, higher `source_id`) updates the existing row's `max_source_id` to the new source.
      - Deleting the current-latest source's rows for a scan promotes the next-highest remaining
        source's id.
      - A direct `bloom_admin`-role write (via `SET LOCAL ROLE bloom_admin`) is also maintained
        correctly — proves the trigger covers the break-glass path.
      - **Concurrent-first-insert race, using `conftest.py`'s `pg_conninfo` second-connection fixture**:
        two connections, both delivering the first-ever rows for the same brand-new `scan_id` under
        different `source_id`s, interleaved so both are in-flight before either commits — assert the
        final `max_source_id` is the true higher of the two, not whichever transaction happened to
        commit last with a value computed before it could see the other's data. **This is the exact race
        reproduced empirically against local Postgres during design (design.md D2) — this test is that
        reproduction, formalized.**
      - **Concurrent-rerun race**, same two-connection shape, for an existing scan with two concurrent
        reruns instead of a brand-new scan.
      - The trigger function's catalog metadata shows `SECURITY DEFINER`, a pinned `search_path`, and
        schema-qualified references throughout its body.
- [ ] 1.2 Confirm every 1.1 test fails against a database with none of this section's migration applied.
- [ ] 1.3 `supabase/migrations/<ts>_create_cyl_scan_latest_source.sql`: `CREATE TABLE
      cyl_scan_latest_source` (D1) + `maintain_cyl_scan_latest_source()` trigger function
      (`pg_advisory_xact_lock(scan_id)` guard, `SECURITY DEFINER`, pinned `search_path`, per D2) +
      `maintain_cyl_scan_latest_source_after_write` `AFTER` trigger + `LOCK TABLE cyl_scan_traits IN
      SHARE MODE` + the one-line backfill (`INSERT ... SELECT ... GROUP BY ... ON CONFLICT (scan_id) DO
      UPDATE`, per D3) + the `cyl_scan_traits_source` view cutover (is_latest via join, not `WindowAgg`,
      per D3) — all in one transaction. Confirm all of section 1's tests now pass.
- [ ] 1.4 Companion `supabase/rollbacks/<ts>_create_cyl_scan_latest_source_rollback.sql`: restore the
      live-`WindowAgg` view definition, then drop the trigger, function, and table, in that order.
- [ ] 1.5 Add to the same test file: a test seeding data directly (bypassing the trigger, simulating
      pre-existing un-backfilled data) and confirming the backfill's result matches a hand-computed
      `max(source_id)` oracle per scan.
- [ ] 1.6 Run `test_cyl_read_path.py`/`test_cyl_experiment_traits.py`; confirm zero regressions — the
      view's external contract (columns, values) is unchanged.
- [ ] 1.7 `test_migration_adds_no_write_capability`, `test_migration_body_is_idempotent`,
      `test_rollback_restores_prior_state` (mirroring this repo's existing migration-test conventions).

## 2. `n_plants` semi-join rewrite (RED first) — design.md D4

- [ ] 2.1 In `tests/integration/test_cyl_experiment_summary_counts.py`, before changing the function body:
      confirm the existing equivalence tests (`test_unpinned_counts_match_get_experiment_traits`,
      `test_accession_null_plant_excluded_from_counts`, etc.) pass against the *current*
      `COUNT(DISTINCT ...)` implementation — this is the oracle the rewrite must keep matching.
      - Add a fixture covering every edge case the semi-join rewrite must preserve: null-accession-plant
        exclusion, reruns/multiple sources, legacy NULL-source-only scans, and scans with zero trait rows
        — run each against **both** the current implementation (before 2.2) and the rewritten one (after
        2.2), asserting identical results, not just asserting the rewritten one "looks right."
      - Run the fixture against every real experiment in the local dev DB as an additional check
        (`test_semi_join_matches_current_implementation_across_all_experiments`), not just the
        hand-built fixture.
- [ ] 2.2 Rewrite the unpinned branch's `n_plants` computation to the `EXISTS` semi-join (D4) —
      `accession_id IS NOT NULL` in place of `JOIN accessions`, `cyl_experiments` join dropped. Confirm
      2.1's tests still pass with identical results.
- [ ] 2.3 `EXPLAIN (ANALYZE, BUFFERS)` on the rewritten unpinned call shows no full materialization of
      `cyl_scan_traits` rows feeding the plant count (structural confirmation the semi-join actually
      short-circuits, not just a timing measurement).

## 3. `cyl_experiment_trait_counts` cache (RED first) — design.md D5

- [ ] 3.1 Add `tests/integration/test_cyl_experiment_trait_counts.py` (RED first — table doesn't exist,
      `UndefinedTable`):
      - `refresh_cyl_experiment_trait_counts()` populates one row per experiment with matching data,
        `n_traits` matching a hand-computed distinct-trait-id count.
      - An experiment with no matching data gets no row.
      - An experiment that had a row and loses all its trait data has that row removed by the next
        refresh (not left stale).
      - A rerun that changes which source is latest is reflected in the *next* refresh, not before it
        (this is the deliberate staleness design.md D5 describes — assert the pre-refresh value is the
        *old* state, then assert post-refresh it's the *new* state).
      - Cross-experiment isolation.
      - The null-accession-plant exclusion, matching `get_experiment_traits`.
      - **No trigger on `cyl_scan_traits` invokes this function** — insert trait rows, assert the cache
        table is unchanged until `refresh_cyl_experiment_trait_counts()` is explicitly called.
- [ ] 3.2 `supabase/migrations/<ts>_create_cyl_experiment_trait_counts.sql`: `CREATE TABLE
      cyl_experiment_trait_counts` (D5) + `refresh_cyl_experiment_trait_counts()` function + an initial
      `SELECT public.refresh_cyl_experiment_trait_counts();` call in the same migration (so the cache
      isn't empty until the first scheduled run — design.md's Migration Plan, M2). `REVOKE`/`GRANT
      EXECUTE ... TO service_role` only (not the four read roles — this is a maintenance job, not a
      user-facing call). Confirm 3.1's tests pass.
- [ ] 3.3 Companion rollback: drop the function and table.

## 4. `get_experiment_summary_counts` rewrite (RED first) — design.md D6-D7

- [ ] 4.1 Add tests to `test_cyl_experiment_summary_counts.py`:
      - Unpinned call: `n_plants` matches a live computation (corrupt the cache table's `n_traits` for
        one experiment to confirm `n_plants` is unaffected — proves it's computed live, not read from any
        cache); `n_traits` matches whatever `cyl_experiment_trait_counts` currently holds (corrupt it to
        a known-wrong value and confirm the RPC returns that wrong value for `n_traits`, proving it reads
        the cache rather than recomputing — then restore and re-test the correct case).
      - Pinned (`source_id_`/`run_id_`) calls: both counts still match `get_experiment_traits` exactly,
        computed live (re-run the existing pinned-branch equivalence tests unmodified — their *behavior*
        doesn't change, only incidental cleanups per D6/D7).
      - Both `source_id_` and `run_id_` set still raises.
      - An experiment absent from both the live `n_plants` computation and the cache is absent from the
        result (not zero-valued).
- [ ] 4.2 `supabase/migrations/<ts>_rewrite_get_experiment_summary_counts.sql` (D6/D7):
      `compute_cyl_experiment_summary_counts_live` (pinned-branch-only helper) +
      `get_experiment_summary_counts` rewrite (unpinned → live semi-join + cache read; pinned →
      delegates to the helper). Same signature, same grants. Confirm 4.1's tests pass.
- [ ] 4.3 Companion rollback: restore the prior (bloom#625) live-join-only function body.
- [ ] 4.4 Run all of sections 1-4's tests plus `test_cyl_read_path.py`/`test_cyl_experiment_traits.py`;
      confirm no regressions. No `bloommcp`/Python test changes expected — `list_experiments()`'s
      contract is unchanged.

## 5. Scheduled refresh (design.md D8 — proposed default, flagged for confirmation)

- [x] 5.1 Add a scheduled GitHub Actions workflow
      (`.github/workflows/refresh-cyl-experiment-trait-counts.yml`, `on: schedule`, every 10 min) that
      calls `refresh_cyl_experiment_trait_counts()` via the PostgREST RPC endpoint using a service-role
      key, not SSH+psql (avoids re-triggering the "manual DB access is emergency-only" policy question
      PR #654's D8 ran into for its own backfill). **Done — flagged for confirmation per design.md's
      Open Questions; if a `workflows`-service-hosted job is preferred instead, delete this file and
      file a follow-up issue against that service.**
- [ ] 5.2 **Blocked on new secrets, not yet provisioned.** Unlike every other staging secret in this
      repo (all consumed server-side by `deploy.yml`), calling staging's PostgREST endpoint directly
      from a GitHub Action needs a new `STAGING_API_URL` secret — no existing secret covers this (only
      `STAGING_SERVICE_ROLE_KEY` already exists, from `deploy.yml`'s own use). Add `STAGING_API_URL`
      before this schedule can actually run; the workflow fails loudly (not silently) if it's missing.
- [ ] 5.3 Verify the workflow's one authenticated call succeeds against staging once 5.2's secret is
      added, before relying on the schedule (`workflow_dispatch` is wired for a manual first run).

## 6. Validate

- [ ] 6.1 Run the full section 1-4 test suite against local dev Postgres; no regressions in
      `test_cyl_read_path.py`, `test_cyl_experiment_traits.py`, `test_cyl_experiment_summary_counts.py`.
- [ ] 6.2 Run `bloommcp`'s full test suite; confirm zero changes needed.
- [ ] 6.3 `openspec validate fix-cyl-scan-traits-latest-rollup --strict` passes.
- [ ] 6.4 Migration lint (`scripts/lint_migrations.sh origin/staging`); `black`/`ruff` on changed Python;
      `openspec validate --strict` repo-wide.
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

- [ ] 7.1 Update `bloommcp/docs/data-access-roadmap.md`: dated entry noting `is_latest`'s new storage
      mechanism and the `cyl_experiment_trait_counts` cache; note that this change supersedes PR #654 and
      folds in bloom#656; add a "Questions for Benfica" entry for D7's un-benchmarked pinned branches and
      D8's proposed GitHub Action refresh schedule, both flagged for her confirmation.
- [ ] 7.2 Update `_WIKI/BLOOMMCP/README.md`'s "Supabase data access" section to note the storage change
      and the scheduled-cache behavior for `n_traits`.
- [ ] 7.3 Run `prettier --check`/`--write` on edited doc files before opening the PR.
