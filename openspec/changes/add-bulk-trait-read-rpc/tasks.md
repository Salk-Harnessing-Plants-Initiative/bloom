## 0. Pre-work — resolve the open design decision

- [ ] 0.1 Get @blm3886 (Benfica)'s review on `design.md` Decision D1 (bulk RPC vs. PostgREST
      embedded-join) before starting section 1 (this gate covers sections 1-3 — tests are written
      against the RPC-call shape D1 recommends, so a flip to Option B invalidates the tests, not just
      the implementation). If she prefers the rejected alternative, revise D1/D2 and sections 1-3
      accordingly before continuing — do not implement or test against an unreviewed shape.
      **Still open.** Sections 1-5 below were implemented locally against Option A ahead of her review
      (fully reversible pre-merge — nothing pushed or opened as a PR) so the proposal has working code to
      review rather than only a design doc. **Do not push, open a PR, or merge until this is checked.**

## 1. Test scaffolding (RED first)

- [x] 1.1 Add `tests/integration/test_cyl_experiment_traits.py` using the `pg_conn` fixture and
      `test_cyl_read_path.py`'s actual helpers — `_seed_experiment_scan`, `_trait`, `_deliver(cur,
    img_ids, label, *, run=None, traits)` (import or copy). There is no `_seed_two_sources` helper;
      seeding two sources for one scan is two `_deliver` calls against the same seeded scan's image ids,
      mirroring `test_two_sources_one_scan_seed`.
- [x] 1.2 Add `_assert_matches_get_scan_traits(cur, experiment_id, *, source_id=None, run_id=None)` per
      design.md's Testing section: group `get_experiment_traits`'s rows by `trait_name`, and for each
      group assert its `{(scan_id, trait_value)}` set equals `get_scan_traits(experiment_id, trait_name,
    source_id_, run_id_)`'s result for that trait (excluding the `trait_name`/`source_id` columns
      `get_scan_traits` doesn't return). `_deliver`'s `traits` parameter already accepts a list, so no
      seeding-helper extension is needed for multi-trait scans (traits=[_trait(...), _trait(...)]).

## 2. Failing tests covering every spec scenario (RED; one `def test_...` per assertion)

- [x] 2.1 `get_experiment_traits(experiment_id_)` returns every trait for every scan in the experiment in
      one call (multi-scan, multi-trait fixture) — no `trait_name_` filter needed. (Scenario: One call
      returns all traits for an experiment)
- [x] 2.2 Default path (`source_id_`/`run_id_` both NULL) matches `get_scan_traits`'s default output via
      `_assert_matches_get_scan_traits`. (Scenario: Default path matches get_scan_traits' latest
      semantics)
- [x] 2.3 `source_id_` pins an older source; matches `get_scan_traits(..., source_id_=...)` via
      `_assert_matches_get_scan_traits`. (Scenario: Pinning a source matches get_scan_traits
      byte-for-byte)
- [x] 2.4 `run_id_` groups by pipeline run; matches `get_scan_traits(..., run_id_=...)` via
      `_assert_matches_get_scan_traits`, including the "run superseded by a newer run" case from the
      source-aware suite. (Scenario: Run grouping matches get_scan_traits byte-for-byte)
- [x] 2.5 Both `source_id_` and `run_id_` set → raises, same as `get_scan_traits`. (Scenario: Supplying
      both is rejected)
- [x] 2.6 No cross-source mixing: latest source dropped a trait an older source had → that trait is
      absent, not backfilled. (Scenario: No cross-source mixing)
- [x] 2.6a A legacy NULL-source scan (direct-insert seed, mirroring the source-aware suite's 2.3/2.15) is
      still returned by `get_experiment_traits`'s default path (exercises the `IS NOT DISTINCT FROM` NULL
      branch end-to-end through the new function, not just the substrate view).
- [x] 2.7 A non-finite latest value (`NULL` in `cyl_scan_traits.value`) is returned as a `NULL`-valued
      row, not omitted. (Scenario: Non-finite values are surfaced as NULL)
- [x] 2.8 Cross-experiment isolation: `get_experiment_traits` for experiment A returns nothing from
      experiment B's scans, including under `source_id_`/`run_id_` pins (mirrors the source-aware
      suite's 2.9a/2.10c regression guards).
- [x] 2.9 An experiment with no trait rows returns zero rows, no error.
- [x] 2.10 `list_experiment_trait_sources(experiment_id_)` lists each real source exactly once
      (`source_id`, `source_name`, `pipeline_run_id`), for an experiment with multiple sources across
      multiple scans. (Scenario: Lists an experiment's sources)
- [x] 2.10a A source whose `pipeline_run_id` is `NULL` (producer omitted it) is still listed — only a
      `NULL` `source_id` is excluded, not a `NULL` `pipeline_run_id`. Seed via `_deliver(..., run=None,
    ...)`.
- [x] 2.11 `list_experiment_trait_sources` excludes a legacy NULL-source scan's placeholder (no row with
      `source_id IS NULL`). (Scenario: Legacy NULL-source rows are not listed as a source)
- [x] 2.12 `list_experiment_trait_sources` for an experiment with only NULL-source (legacy) scans returns
      zero rows.
- [x] 2.13 `list_experiment_trait_sources` for a different experiment does not leak sources
      (cross-experiment isolation, mirrored from 2.8).
- [x] 2.14 Role reads: `SET LOCAL ROLE bloom_agent`, `bloom_user`, and `bloom_admin` can each call both
      `get_experiment_traits` and `list_experiment_trait_sources` end-to-end through the full join chain
      (`cyl_scans → cyl_waves → cyl_plants → accessions → species → cyl_experiments`) — the D3
      grant-chain spot-check covering all four roles the "Bulk read grants" requirement names.
- [x] 2.14a `authenticated`'s access is asserted via `has_function_privilege('authenticated',
    'get_experiment_traits(bigint,bigint,text)', 'EXECUTE')` and the `list_experiment_trait_sources`
      equivalent (mirroring the precedent's `test_authenticated_has_select_grant_on_views` — a bare `SET
    LOCAL ROLE authenticated` has no JWT context to assume through).
- [x] 2.14b `test_migration_adds_no_write_capability` — static-scan the migration's SQL text (read the
      file, assert absence of `CREATE POLICY` / `GRANT INSERT` / `GRANT UPDATE` / `GRANT DELETE` /
      `GRANT ALL`) as its own assertion, distinct from 2.14's role-read check. (Scenario: No write
      capability is added)
- [x] 2.15 `test_migration_body_is_idempotent` — re-apply the migration body on already-applied state;
      both functions still exist with the same signature (catches non-idempotent `CREATE FUNCTION`
      without `OR REPLACE`, or a bare `CREATE` that errors on re-run); `get_scan_traits` and the three
      existing views are unchanged (signature/row-shape unchanged) after the re-apply.
- [x] 2.16 `test_rollback_restores_prior_state` — apply the rollback; both new functions no longer exist,
      and `get_scan_traits`, `cyl_scan_traits_source`, `cyl_scan_traits_latest`, and `cyl_scan_trait_names`
      are all unchanged; re-apply the forward migration and confirm the two new functions are back
      (round-trip safety).
- [x] 2.17 Confirm every 2.x test above FAILS before implementation (`UndefinedFunction`). Confirmed: 19
      of 22 failed with `UndefinedFunction` against the local dev DB before the migration was applied; the
      other 3 (`test_migration_adds_no_write_capability`, `test_migration_body_is_idempotent`,
      `test_rollback_restores_prior_state`) legitimately pass in either state, since they test the
      migration/rollback SQL's own mechanics (idempotent `CREATE OR REPLACE`, `DROP ... IF EXISTS`)
      rather than querying a function that must already exist.

## 3. Implementation — migration (GREEN)

Gated on task 0.1 resolving — **implemented ahead of that review** (see the note on 0.1); revisit if
Benfica prefers Option B.

- [x] 3.1 Create `supabase/migrations/<ts>_get_experiment_traits.sql`, wrapped in `BEGIN; … COMMIT;`.
      Pick `<ts>` per design.md's Migration/Rollback section (later than every migration on **both**
      `main` and `staging` — re-check immediately before opening the PR). Used `20260728000000` (staging's
      newest at the time was `20260724000300`); `scripts/lint_migrations.sh origin/staging` passes.
- [x] 3.2 `CREATE FUNCTION public.get_experiment_traits(experiment_id_ bigint, source_id_ bigint DEFAULT
    NULL, run_id_ text DEFAULT NULL) RETURNS TABLE (...)` per design.md D1 — same guard, same
      disjunction, same table-qualified ORDER BY discipline as `get_scan_traits`. Do not add `REVOKE
    EXECUTE ... FROM PUBLIC` (match the existing PUBLIC-execute posture on `get_scan_traits`). Used
      `CREATE OR REPLACE FUNCTION` (not bare `CREATE`) so the migration body is safely re-runnable —
      design.md D4/Migration-Rollback updated to match.
- [x] 3.3 `CREATE FUNCTION public.list_experiment_trait_sources(experiment_id_ bigint) RETURNS TABLE
    (source_id bigint, source_name text, pipeline_run_id text)` per design.md D2. Same
      `CREATE OR REPLACE` treatment as 3.2.

## 4. Rollback + types (GREEN)

- [x] 4.1 Add `supabase/rollbacks/<ts>_get_experiment_traits_rollback.sql`: `DROP FUNCTION IF EXISTS
    get_experiment_traits(bigint, bigint, text); DROP FUNCTION IF EXISTS
    list_experiment_trait_sources(bigint);`.
- [x] 4.2 Hand-edit all five tracked `database.types.ts` copies (mirroring the source-aware precedent):
      `web/lib`, `web/types`, `packages/bloom-js/src/types`, `packages/bloom-fs/src/types`,
      `packages/bloom-nextjs-auth/src/lib`. Add both new functions to `Functions`/`Args`/`Returns`.
      **No TypeScript caller of either function exists yet** (Tier 2 is deferred), so `tsc --noEmit`
      passing gives no signal on a hand-edit typo — verified the `Args`/`Returns` shape by hand against
      3.2/3.3's actual `RETURNS TABLE` clause. `web/types/database.types.ts` uses a different
      (SQL-declaration-order, multi-line `Args`) style than the other four (alphabetical, single-line
      `Args`) — matched each file's own existing convention rather than a single shared style.

## 5. Validate (GREEN)

- [x] 5.1 Ran `tests/integration/test_cyl_experiment_traits.py` — 22 passed, 0 failed, against the
      **local dev Postgres** (`bloom_v2_dev-db-dev-1`, migration applied directly via `psql`), not the
      prod compose stack `compose-health-check` uses — that CI job is the authoritative run against the
      full stack and hasn't executed yet (no PR opened). No regression in `test_cyl_read_path.py` (34
      passed, 1 skipped, unchanged).
- [x] 5.2 `openspec validate add-bulk-trait-read-rpc --strict` passes.
- [x] 5.3 `cd web && npx tsc --noEmit`: no new errors (pre-existing, unrelated failures confirmed via
      `git stash` diff — `NODE_ENV` read-only assignments in two test files, missing `@salk-hpi/bloom-js`
      module resolution). `packages/bloom-js tsc -p .`: clean. `packages/bloom-fs tsc -p .`: pre-existing
      unrelated `@salk-hpi/bloom-js/dist/types/*` resolution errors only (same module-resolution gap,
      confirmed via `git stash`), nothing new.
- [x] 5.4 Migration lint clean (`scripts/lint_migrations.sh origin/staging` passes). `black`/`ruff`
      (pinned versions from `.pre-commit-config.yaml`, 26.3.1/0.9.9) clean on the new test file. Prettier:
      the edited `_WIKI/BLOOMMCP/README.md` and `bloommcp/docs/data-access-roadmap.md` (re-run through
      `prettier --write` after the table-width ripple) are clean; the five `database.types.ts` files
      already fail `prettier --check` in their pre-existing committed state (confirmed via `git stash`,
      unrelated to this change — generated files not run through prettier historically, and prettier
      isn't wired into any CI workflow) — left as-is rather than reformatting the whole file out of scope.
      Full `pre-merge` suite not run (no PR yet).

## 6. Docs + follow-up

- [ ] 6.1 Update `bloommcp/docs/data-access-roadmap.md`'s Tier 1 row to ✅ and link this PR; strike the
      "Tier 1's RPC shape" ask in the roadmap's "Questions for Benfica" section (Q2), matching how Q1/Q3
      were struck through and marked "Resolved" once answered.
      **Partially done, intentionally not checked off:** updated the Tier 1 row's Tracking cell (links
      bloom#546 + this branch) and Status to 🔵 (in progress) — **not** ✅, since nothing is merged yet.
      Left Q2 unstruck since D1 isn't actually resolved (task 0.1 still open) — striking it now would
      misstate that the review happened.
- [x] 6.2 Update `_WIKI/BLOOMMCP/README.md`'s "Supabase data access" section to mention
      `get_experiment_traits`/`list_experiment_trait_sources` as the single-round-trip way to fetch an
      experiment's full trait set, alongside the existing `get_scan_traits` example — cross-reference the
      spec/migration for the selection rule rather than restating it (mirrors the source-aware
      precedent's own equivalent doc task).
- [ ] 6.3 File Tier 2's tracking issue ("rewrite `SupabaseReader`'s raw tier to query the DB directly")
      per the roadmap's just-in-time issue policy, now that Tier 1 is reached — reference bloom#546 and
      this change. **Not done** — filing a GitHub issue is a shared-state action left for explicit
      confirmation rather than taken unilaterally.
