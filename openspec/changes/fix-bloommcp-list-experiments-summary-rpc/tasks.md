## 0. Pre-work

- [x] 0.1 Implement design.md D1's recommended `bigint` signature now (not `int`) and flag it explicitly
      in the PR description for @blm3886 (Benfica)'s review — per this repo's own precedent (Tier 1/#546,
      PR #548: the recommended option shipped directly, and gate resolution was recorded via the PR
      review itself rather than as a blocking pre-implementation round-trip). If she prefers `int` during
      review, that's a follow-up fix commit on this same PR, not a blocker to starting section 1.
      **Implemented as `bigint` throughout; flagged in the roadmap doc's "Questions for Benfica" Q4 and
      the PR description — Benfica's actual review is still pending, not resolved by this task.**
- [ ] 0.2 **Not completed in this implementation pass — this sandboxed dev environment has no access to
      staging and no 13.8M-row local fixture.** Shipped with a considered interim default (30s, see
      design.md D5) instead. Before treating that default as final: benchmark `get_experiment_traits(1)`
      (staging's largest experiment, 13.8M `cyl_scan_traits` rows, or an equivalent local-Postgres seed at
      that scale — write a seeding-loop script if no such fixture exists yet) via `psql`'s `\timing` or a
      `time.perf_counter()`-wrapped call, over 10 runs. Record the p99 duration. Set
      `_DEFAULT_POSTGREST_TIMEOUT_SECONDS = ceil(3 * p99, nearest 5s)` in both design.md D5 and
      `supabase_client.py`, replacing 30s.

## 1. SQL integration test scaffolding (RED first)

- [x] 1.1 Add `tests/integration/test_cyl_experiment_summary_counts.py` using `test_cyl_experiment_traits.py`'s
      actual fixtures/helpers: `pg_conn`, `_seed_experiment` (returns `experiment_id, wave_id` — NOT
      `_seed_experiment_scan`, which creates one experiment with one scan per call and cannot build the
      multi-scan fixtures these tests need), repeated `_seed_scan_in(cur, wave_id, ...)` calls for
      multiple scans in one experiment, `_trait`, `_deliver`. Also added `_seed_scan_no_accession` (not
      anticipated by this task when written — needed for 2.2's regression test).
- [x] 1.2 Add `_assert_matches_get_experiment_traits(cur, experiment_id, *, source_id=None, run_id=None)`:
      call `get_experiment_summary_counts` and `get_experiment_traits` with the same pin, assert
      `(n_plants, n_traits)` equals `(len({r["plant_id"] for r in traits_rows}),
    len({r["trait_name"] for r in traits_rows if r["trait_name"] is not None}))` — **the `is not None`
      filter is required**: `COUNT(DISTINCT trait_name)` ignores SQL `NULL`s, but a naive
      `len({... for r in rows})` counts a Python `None` member once, so an unfiltered comparison would
      spuriously disagree for any fixture including a legacy row with an unresolved `trait_id` (design.md
      D2's caveat).

## 2. Failing tests covering every spec scenario (RED; one `def test_...` per assertion)

- [x] 2.1 Unpinned call (`{}`) for a multi-scan, multi-trait experiment returns one row whose
      `(n_plants, n_traits)` matches `_assert_matches_get_experiment_traits`. (Scenario: Unpinned counts
      match load_experiment's latest semantics)
- [x] 2.2 **Regression test for the `accessions` join (design.md D2):** seed an experiment where one
      plant has `accession_id = NULL` (direct-insert seed, mirroring Tier 1's own
      `test_null_species_id_experiment_still_returns_traits` direct-insert pattern) alongside plants that
      do have an accession. Assert the no-accession plant's scans/traits are **excluded** from both
      `n_plants` and `n_traits`, matching `get_experiment_traits`'s own inner-join exclusion of the same
      plant (`_assert_matches_get_experiment_traits` against this fixture should already catch a dropped
      join, but this test names the exact failure mode explicitly so it can't be casually deleted as
      "redundant" later).
- [x] 2.3 `source_id_` pins an older source; matches `_assert_matches_get_experiment_traits` with the same
      pin. (Scenario: Pinning a source matches get_experiment_traits byte-for-byte)
- [x] 2.4 `run_id_` groups by pipeline run; matches `_assert_matches_get_experiment_traits`, including a
      run superseded by a newer run. (Scenario: Run grouping matches get_experiment_traits byte-for-byte)
- [x] 2.5 Both `source_id_` and `run_id_` set raises, same as `get_experiment_traits`. (Scenario:
      Supplying both is rejected)
- [x] 2.6 An experiment with zero matching `cyl_scan_traits` rows returns **zero rows** from the function,
      not a zero-valued row (pins design.md D2's "no LEFT JOIN" decision).
- [x] 2.7 `experiment_id_` set, unpinned source/run: returns exactly one row for that experiment, no rows
      for any other experiment (cross-experiment isolation, mirrors Tier 1's 2.8/2.13).
- [x] 2.8 `experiment_id_ = NULL` (bulk case): returns one row per experiment that has matching data
      (verified against a fixture with ≥2 such experiments plus ≥1 with none), each row's counts matching
      `_assert_matches_get_experiment_traits` independently.
- [x] 2.9 A `NULL`-valued latest trait reading still counts toward `n_traits` (the trait name is present
      even though its value is null) — `COUNT(DISTINCT trait_name)` never reads `value`, so this doesn't
      distinguish `NULL` from a literal non-finite float actually stored in the `real` column (bypassing
      the write-back RPC's own NaN/Infinity→NULL normalization); named/tested as a NULL-value case, not a
      non-finite-value case, per PR review (renamed from an earlier, more sweeping claim).
- [x] 2.10 Role reads: `SET LOCAL ROLE bloom_agent`/`bloom_user`/`bloom_admin` can call the function
      end-to-end through the full join chain; `authenticated` via
      `has_function_privilege('authenticated', 'get_experiment_summary_counts(bigint,bigint,text)',
    'EXECUTE')` (D3's grant spot-check, mirroring Tier 1's 2.14/2.14a).
- [x] 2.11 `test_migration_adds_no_write_capability` — regex static-scan of the migration SQL text (no
      `CREATE POLICY` / `GRANT INSERT|UPDATE|DELETE|ALL`), mirroring Tier 1's 7.8-strengthened check.
- [x] 2.12 `test_migration_body_is_idempotent` — re-apply the migration body on already-applied state;
      `get_experiment_traits`/`get_scan_traits`/the existing views are unchanged after re-apply.
- [x] 2.13 `test_rollback_restores_prior_state` — apply the rollback; `get_experiment_summary_counts` no
      longer exists, every pre-existing read object is unchanged; re-apply the forward migration and
      confirm the function is back.
- [x] 2.14 `test_get_experiment_summary_counts_reachable_over_postgrest` — PostgREST/HTTP-layer smoke test
      mirroring Tier 1's 7.7, skipped locally, run in CI's `compose-health-check`.
- [x] 2.15 Confirm every 2.x test above FAILS (`UndefinedFunction`) against a database with no migration
      applied, before starting section 3.

## 3. Implementation — migration (GREEN)

- [x] 3.1 Create `supabase/migrations/20260807000000_get_experiment_summary_counts.sql` (re-check this
      timestamp is later than `main`'s and `staging`'s newest migration immediately before opening the
      PR — both were at `20260803000000_add_cyl_experiment_search.sql` when this proposal was written),
      wrapped in `BEGIN; … COMMIT;`.
- [x] 3.2 `CREATE OR REPLACE FUNCTION public.get_experiment_summary_counts(experiment_id_ bigint DEFAULT
    NULL, source_id_ bigint DEFAULT NULL, run_id_ text DEFAULT NULL) RETURNS TABLE (experiment_id
    bigint, n_plants int, n_traits int) LANGUAGE plpgsql STABLE SECURITY INVOKER` per design.md D2: same
      mutual-exclusion guard and join chain as `get_experiment_traits` — **including the `JOIN
    public.accessions ON cyl_plants.accession_id = accessions.id` join** (do not drop it; see design.md
      D2's and Risks' explicit note on why this is not the same situation as Tier 1's `species`-join
      removal) — `GROUP BY cyl_experiments.id`, `COUNT(DISTINCT cyl_plants.id)`/
      `COUNT(DISTINCT src.trait_name)`.
- [x] 3.3 `REVOKE EXECUTE ON FUNCTION public.get_experiment_summary_counts(bigint, bigint, text) FROM
    PUBLIC;` then `GRANT EXECUTE ... TO bloom_agent, bloom_user, bloom_admin, authenticated;` per
      design.md's Migration section.
- [x] 3.4 Confirm every 2.x test now passes against the migrated local dev Postgres, including 2.2 (the
      accessions-join regression) — this is the test that would have caught the join omission found
      during this proposal's own review; do not skip re-running it here.

## 4. Rollback + types (GREEN)

- [x] 4.1 Add `supabase/rollbacks/20260807000000_get_experiment_summary_counts_rollback.sql`:
      `DROP FUNCTION IF EXISTS public.get_experiment_summary_counts(bigint, bigint, text);`.
- [x] 4.2 Hand-edit all five tracked `database.types.ts` copies (`web/lib`, `web/types`,
      `packages/bloom-js/src/types`, `packages/bloom-fs/src/types`, `packages/bloom-nextjs-auth/src/lib`),
      matching each file's own existing style; `n_plants`/`n_traits` typed non-null `number`.

## 5. bloommcp-side RED tests (fakes only, no live DB) — written before section 6, not after

**These must be written and confirmed failing against TODAY's per-experiment implementation before
section 6 touches any production code.** Sections 5 and 6 are a matched pair: `FakeSupabaseDB.call_rpc`
(`bloommcp/tests/conftest.py`) raises `AssertionError("unfaked RPC function: ...")` for any RPC name it
doesn't recognize, so 5.1's new dispatcher branch and 6.2's rewrite of `list_experiments()` must land in
the **same commit** — splitting them across two commits breaks CI's `python-audit` job on the
intermediate commit (every existing `list_experiments()` test would hit that `AssertionError` the moment
`list_experiments()` calls a name the fake doesn't know, or conversely be untested against the new
behavior if the fake gains the branch first with no caller). Do not split this pair.

- [x] 5.1 Add a `"get_experiment_summary_counts"` branch to `bloommcp/tests/conftest.py`'s
      `FakeSupabaseDB.call_rpc` dispatcher, deriving `{experiment_id, n_plants, n_traits}` rows from the
      existing `self._traits` seed dict with the same `source_id_`/`run_id_` filtering the
      `"get_experiment_traits"` branch already applies.
- [x] 5.2 Update `test_list_experiments_enumerates_database_experiments`: seed via
      `fake_supabase_db.seed_traits(...)`, assert exactly **one** call to `get_experiment_summary_counts`
      (not one per experiment), correct `rows`/`trait_columns` mapping. Confirm this fails against
      today's code (which never calls that RPC name at all, so the call-count assertion is unmet).
- [x] 5.3 Add a test: an experiment with no seeded traits appears in `list_experiments()`'s result with
      `rows=0, trait_columns=0` (D3's default-to-zero merge), not excluded. Note: this already holds for
      today's implementation too (a successful zero-row RPC response yields `len(set())=0` either way) —
      keep it as a regression/characterization test that must also pass after the rewrite, not evidence
      the rewrite is needed.
- [x] 5.4 Delete `test_list_experiments_excludes_a_failing_experiment` (its per-experiment-failure premise
      no longer holds) and add a new test asserting a `get_experiment_summary_counts` failure raises
      `ExperimentReadError` from `list_experiments()` (design.md D4) — confirm this fails against today's
      code (no such path exists yet).
- [x] 5.5 Confirm `test_list_experiments_excludes_a_malformed_row` still passes against today's code
      unmodified (sanity check — it should, since section 6 doesn't touch this path).
- [x] 5.6 In `tests/unit/test_supabase_client.py`, update `test_get_postgrest_client_returns_a_fresh_client`
      (lines 93-107)'s `fake_create_client(url, key)` stub to `fake_create_client(url, key, options=None)`
      — confirm this test still passes as-is today (it doesn't yet need the third parameter, but adding
      it now means section 6.3 won't break it as a side effect).
- [x] 5.7 Add `test_get_postgrest_client_default_uses_the_bounded_module_default` and
      `test_get_postgrest_client_timeout_override_builds_client_options` to the same file, per design.md
      D5's exact code (mirroring `test_get_storage_client_default_passes_no_options_override`/
      `test_get_storage_client_timeout_override_builds_client_options` in
      `bloommcp/tests/test_storage_backend.py:400-431`). Confirm both fail against today's
      `get_postgrest_client()` (no `timeout_seconds` parameter exists yet, so calling with
      `timeout_seconds=5.0` raises `TypeError`, and the no-override case has no `_DEFAULT_POSTGREST_TIMEOUT_SECONDS`
      to assert against).

## 6. bloommcp implementation (GREEN) — lands together with section 5 in one commit

- [x] 6.1 Add `_RPC_GET_EXPERIMENT_SUMMARY_COUNTS = "get_experiment_summary_counts"` constant to
      `supabase_reader.py`, alongside the existing RPC/table name constants.
- [x] 6.2 Rewrite `list_experiments()` per design.md D3's corrected code: one bulk
      `_sc.call_rpc(_RPC_GET_EXPERIMENT_SUMMARY_COUNTS, {"experiment_id_": None, "source_id_": None,
    "run_id_": None})` call wrapped in its own `try/except Exception: raise ExperimentReadError(...)
    from exc` (**not** through `_safe_rpc` — its `name` parameter has no fit for a bulk call; see
      design.md D3); merge onto the existing `cyl_experiments` listing by `experiment_id`, defaulting
      missing entries to `n_plants=0, n_traits=0`; **keep the existing per-row `try/except` around the
      merge loop** so a malformed `cyl_experiments` row is still skipped (task 5.5 depends on this).
- [x] 6.3 Add `get_postgrest_client(*, timeout_seconds: float | None = None)` per design.md D5, via
      `supabase.ClientOptions(postgrest_client_timeout=...)`, always passed (unlike `get_storage_client`,
      since the un-overridden default itself is changing — see design.md D5). `call_rpc()` stays
      unchanged (calls `get_postgrest_client()` with no override). **Uses the 30s interim default, not a
      benchmarked value — task 0.2 was not completed in this pass; see its note.**
- [x] 6.4 Run all of section 5's tests; confirm every one now passes.

## 7. Validate (GREEN)

- [x] 7.1 Run `tests/integration/test_cyl_experiment_summary_counts.py` against local dev Postgres; no
      regression in `test_cyl_experiment_traits.py`/`test_cyl_read_path.py`. **Note on staging
      verification:** bloom#625's acceptance criterion ("well under a second... verified against
      staging's current 224-experiment scale") cannot be checked pre-merge against local dev Postgres
      alone — record local-dev timing here, and re-verify directly against staging after this deploys
      (before closing #625), per design.md's Risks section.
- [x] 7.2 Run `bloommcp`'s full test suite (`test_supabase_reader.py`, `test_supabase_client.py`,
      `tests/unit/test_supabase_client.py`); no regression elsewhere.
- [x] 7.3 `openspec validate fix-bloommcp-list-experiments-summary-rpc --strict` passes.
- [x] 7.4 `cd web && npx tsc --noEmit`; `packages/bloom-js`/`packages/bloom-fs` `tsc -p .` — no new errors
      beyond any pre-existing, unrelated failures (confirm via `git stash` diff, per Tier 1's precedent).
- [x] 7.5 Migration lint (`scripts/lint_migrations.sh origin/staging`); `black`/`ruff` on new/changed
      Python files; `openspec validate --strict` repo-wide.

## 8. Docs + follow-up

- [x] 8.1 Update `bloommcp/docs/data-access-roadmap.md`: add this change as a new dated entry under
      "Questions for Benfica" (mirroring Q1-Q3's precedent — a Q4 for D1's bigint-vs-int confirmation,
      struck through and marked "Resolved" once she reviews the PR) and cross-reference bloom#625 in the
      Tier 2 row's Tracking cell alongside the existing #476 cross-reference (same shape of thing: a
      narrower fix on the same file/tier, not a new tier).
- [x] 8.2 Update `_WIKI/BLOOMMCP/README.md`'s "Supabase data access" section to mention
      `get_experiment_summary_counts` alongside `get_experiment_traits`/`get_scan_traits`, cross-referencing
      the spec for the selection rule rather than restating it.
- [x] 8.3 In the same `_WIKI/BLOOMMCP/README.md` section, document `get_postgrest_client()`'s new
      `timeout_seconds` override and the module's chosen default (the section's existing code example,
      `client = get_postgrest_client()`, is the exact call site whose default timeout this change
      changes — a developer reading that example has no other place to learn this changed).
- [x] 8.4 Run `prettier --check` (or `--write`) on the two edited doc files after 8.1-8.3, before the PR
      is opened — the validate pass in section 7 runs before these doc edits exist, so it does not cover
      them.
