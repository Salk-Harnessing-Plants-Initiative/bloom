## 0. Pre-work — resolve the two open design decisions

- [ ] 0.1 Get @blm3886 (Benfica)'s confirmation on design.md Decision D1 (`bigint` throughout, diverging
      from bloom#625's literal `int` sketch) before starting section 1 — the tests in section 2 are
      written against the `bigint` signature. If she prefers `int`, revise D1 and section 1-2 before
      continuing.
- [ ] 0.2 Benchmark a realistic `load_experiment`/`get_experiment_traits` call against the largest current
      experiment (staging's `experiment_id=1`, 13.8M trait rows, or an equivalent local-Postgres seed at
      that scale) to pick design.md D5's `_DEFAULT_POSTGREST_TIMEOUT_SECONDS` value — replace the 30s
      placeholder with a benchmarked number before task 4.x lands. Record the measured call duration and
      chosen margin in design.md D5.

## 1. Test scaffolding (RED first)

- [ ] 1.1 Add `tests/integration/test_cyl_experiment_summary_counts.py` using the `pg_conn` fixture and
      `test_cyl_experiment_traits.py`'s/`test_cyl_read_path.py`'s helpers (`_seed_experiment_scan`,
      `_trait`, `_deliver`).
- [ ] 1.2 Add `_assert_matches_get_experiment_traits(cur, experiment_id, *, source_id=None, run_id=None)`:
      call `get_experiment_summary_counts` and `get_experiment_traits` with the same pin, assert
      `(n_plants, n_traits)` equals `(len({r["plant_id"] for r in traits_rows}),
    len({r["trait_name"] for r in traits_rows}))` — the direct oracle for design.md's "must match
      `load_experiment`'s latest-selection semantics" requirement.

## 2. Failing tests covering every spec scenario (RED; one `def test_...` per assertion)

- [ ] 2.1 Unpinned call (`{}`) for a multi-scan, multi-trait experiment returns one row whose
      `(n_plants, n_traits)` matches `_assert_matches_get_experiment_traits`. (Scenario: Unpinned counts
      match load_experiment's latest semantics)
- [ ] 2.2 `source_id_` pins an older source; matches `_assert_matches_get_experiment_traits` with the same
      pin. (Scenario: Pinning a source matches get_experiment_traits byte-for-byte)
- [ ] 2.3 `run_id_` groups by pipeline run; matches `_assert_matches_get_experiment_traits`, including a
      run superseded by a newer run. (Scenario: Run grouping matches get_experiment_traits byte-for-byte)
- [ ] 2.4 Both `source_id_` and `run_id_` set raises, same as `get_experiment_traits`. (Scenario:
      Supplying both is rejected)
- [ ] 2.5 An experiment with zero matching `cyl_scan_traits` rows returns **zero rows** from the function,
      not a zero-valued row (pins design.md D2's "no LEFT JOIN" decision).
- [ ] 2.6 `experiment_id_` set, unpinned source/run: returns exactly one row for that experiment, no rows
      for any other experiment (cross-experiment isolation, mirrors Tier 1's 2.8/2.13).
- [ ] 2.7 `experiment_id_ = NULL` (bulk case): returns one row per experiment that has matching data
      (verified against a fixture with ≥2 such experiments plus ≥1 with none), each row's counts matching
      `_assert_matches_get_experiment_traits` independently.
- [ ] 2.8 A non-finite (`NULL`-valued) latest trait reading still counts toward `n_traits` (the trait name
      is present even though its value is null) — matches `get_experiment_traits`'s "non-finite values
      surfaced as NULL, not omitted" semantics.
- [ ] 2.9 Role reads: `SET LOCAL ROLE bloom_agent`/`bloom_user`/`bloom_admin` can call the function
      end-to-end through the full join chain; `authenticated` via
      `has_function_privilege('authenticated', 'get_experiment_summary_counts(bigint,bigint,text)',
    'EXECUTE')` (D3's grant spot-check, mirroring Tier 1's 2.14/2.14a).
- [ ] 2.10 `test_migration_adds_no_write_capability` — regex static-scan of the migration SQL text (no
      `CREATE POLICY` / `GRANT INSERT|UPDATE|DELETE|ALL`), mirroring Tier 1's 7.8-strengthened check.
- [ ] 2.11 `test_migration_body_is_idempotent` — re-apply the migration body on already-applied state;
      `get_experiment_traits`/`get_scan_traits`/the existing views are unchanged after re-apply.
- [ ] 2.12 `test_rollback_restores_prior_state` — apply the rollback; `get_experiment_summary_counts` no
      longer exists, every pre-existing read object is unchanged; re-apply the forward migration and
      confirm the function is back.
- [ ] 2.13 `test_get_experiment_summary_counts_reachable_over_postgrest` — PostgREST/HTTP-layer smoke test
      mirroring Tier 1's 7.7, skipped locally, run in CI's `compose-health-check`.
- [ ] 2.14 Confirm every 2.x test above FAILS (`UndefinedFunction`) before implementation.

## 3. Implementation — migration (GREEN)

Gated on task 0.1.

- [ ] 3.1 Create `supabase/migrations/20260807000000_get_experiment_summary_counts.sql` (re-check this
      timestamp is later than `main`'s and `staging`'s newest migration immediately before opening the
      PR — both were at `20260803000000_add_cyl_experiment_search.sql` when this proposal was written),
      wrapped in `BEGIN; … COMMIT;`.
- [ ] 3.2 `CREATE OR REPLACE FUNCTION public.get_experiment_summary_counts(experiment_id_ bigint DEFAULT
    NULL, source_id_ bigint DEFAULT NULL, run_id_ text DEFAULT NULL) RETURNS TABLE (experiment_id
    bigint, n_plants int, n_traits int) LANGUAGE plpgsql STABLE SECURITY INVOKER` per design.md D2: same
      mutual-exclusion guard and join chain as `get_experiment_traits`, `GROUP BY cyl_experiments.id`,
      `COUNT(DISTINCT cyl_plants.id)`/`COUNT(DISTINCT src.trait_name)`.
- [ ] 3.3 `REVOKE EXECUTE ON FUNCTION public.get_experiment_summary_counts(bigint, bigint, text) FROM
    PUBLIC;` then `GRANT EXECUTE ... TO bloom_agent, bloom_user, bloom_admin, authenticated;` per
      design.md's Migration section.

## 4. Implementation — bloommcp caller + timeout (GREEN)

- [ ] 4.1 Add `_RPC_GET_EXPERIMENT_SUMMARY_COUNTS = "get_experiment_summary_counts"` constant to
      `supabase_reader.py`, alongside the existing RPC/table name constants.
- [ ] 4.2 Rewrite `list_experiments()` per design.md D3: one `_sc.call_rpc(_RPC_GET_EXPERIMENT_SUMMARY_COUNTS,
    {"experiment_id_": None, "source_id_": None, "run_id_": None})` call, wrapped through `_safe_rpc`
      (D4) so a failure raises `ExperimentReadError` instead of being caught per-row; merge onto the
      existing `cyl_experiments` listing by `experiment_id`, defaulting missing entries to
      `n_plants=0, n_traits=0`.
- [ ] 4.3 Add `get_postgrest_client(*, timeout_seconds: float | None = None)` per design.md D5, using the
      benchmarked `_DEFAULT_POSTGREST_TIMEOUT_SECONDS` from task 0.2 (not the 30s placeholder) via
      `supabase.ClientOptions(postgrest_client_timeout=...)`. `call_rpc()` stays unchanged (calls
      `get_postgrest_client()` with no override).
- [ ] 4.4 Update `test_supabase_client.py`'s `test_client_accessors_accept_no_caller_credential_parameter`
      to assert `{"timeout_seconds"}` for `get_postgrest_client`; add a test proving a small
      `timeout_seconds` override actually bounds a slow call (not merely accepted and ignored).

## 5. bloommcp-side test updates (fakes only, no live DB)

- [ ] 5.1 Add a `"get_experiment_summary_counts"` branch to `bloommcp/tests/conftest.py`'s
      `FakeSupabaseDB.call_rpc` dispatcher, deriving rows from the existing `self._traits` seed dict with
      the same `source_id_`/`run_id_` filtering the `"get_experiment_traits"` branch already applies.
- [ ] 5.2 Update `test_list_experiments_enumerates_database_experiments` for the new one-call shape.
- [ ] 5.3 Add a test: an experiment with no seeded traits appears in `list_experiments()`'s result with
      `rows=0, trait_columns=0` (D3's default-to-zero merge), not excluded.
- [ ] 5.4 Delete `test_list_experiments_excludes_a_failing_experiment` (its per-experiment-failure premise
      no longer holds) and replace with a test that a `get_experiment_summary_counts` failure raises
      `ExperimentReadError` from `list_experiments()` (D4).
- [ ] 5.5 Confirm `test_list_experiments_excludes_a_malformed_row` still passes unmodified (unaffected by
      this change).

## 6. Rollback + types (GREEN)

- [ ] 6.1 Add `supabase/rollbacks/20260807000000_get_experiment_summary_counts_rollback.sql`:
      `DROP FUNCTION IF EXISTS public.get_experiment_summary_counts(bigint, bigint, text);`.
- [ ] 6.2 Hand-edit all five tracked `database.types.ts` copies (`web/lib`, `web/types`,
      `packages/bloom-js/src/types`, `packages/bloom-fs/src/types`, `packages/bloom-nextjs-auth/src/lib`),
      matching each file's own existing style; `n_plants`/`n_traits` typed non-null `number`.

## 7. Validate (GREEN)

- [ ] 7.1 Run `tests/integration/test_cyl_experiment_summary_counts.py` against local dev Postgres; no
      regression in `test_cyl_experiment_traits.py`/`test_cyl_read_path.py`.
- [ ] 7.2 Run `bloommcp`'s full test suite (`test_supabase_reader.py`, `test_supabase_client.py`); no
      regression elsewhere.
- [ ] 7.3 `openspec validate fix-bloommcp-list-experiments-summary-rpc --strict` passes.
- [ ] 7.4 `cd web && npx tsc --noEmit`; `packages/bloom-js`/`packages/bloom-fs` `tsc -p .` — no new errors
      beyond any pre-existing, unrelated failures (confirm via `git stash` diff, per Tier 1's precedent).
- [ ] 7.5 Migration lint (`scripts/lint_migrations.sh origin/staging`); `black`/`ruff` on new/changed
      Python files; `openspec validate --strict` repo-wide.
- [ ] 7.6 Full `pre-merge` suite before opening the PR.

## 8. Docs + follow-up

- [ ] 8.1 Update `bloommcp/docs/data-access-roadmap.md`: note bloom#625 as a Tier-2-follow-up fix, not a
      new tier, since it corrects Tier 2's own D4 rather than adding new roadmap scope.
- [ ] 8.2 Update `_WIKI/BLOOMMCP/README.md`'s "Supabase data access" section to mention
      `get_experiment_summary_counts` alongside `get_experiment_traits`/`get_scan_traits`, cross-referencing
      the spec for the selection rule rather than restating it.
