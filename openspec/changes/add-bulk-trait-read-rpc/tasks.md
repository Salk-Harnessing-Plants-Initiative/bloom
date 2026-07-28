## 0. Pre-work — resolve the open design decision

- [ ] 0.1 Get @blm3886 (Benfica)'s review on `design.md` Decision D1 (bulk RPC vs. PostgREST
      embedded-join) before starting section 3 (implementation). If she prefers the rejected
      alternative, revise D1/D2 and this file's section 3 accordingly before continuing — do not
      implement against an unreviewed shape.

## 1. Test scaffolding (RED first)

- [ ] 1.1 Add `tests/integration/test_cyl_experiment_traits.py` using the `pg_conn` fixture
      (`supabase_admin`, BYPASSRLS, per-test rollback), reusing `test_cyl_read_path.py`'s
      `_seed_experiment_scan`/`_seed_two_sources` helpers (import or copy — prefer import if the module
      exposes them without turning the test file into a fixture module of its own).
- [ ] 1.2 Extend seeding to cover **multiple traits per scan** (the existing helpers seed one trait at a
      time) so a single `get_experiment_traits` call has more than one trait to return per scan.

## 2. Failing tests covering every spec scenario (RED; one `def test_...` per assertion)

- [ ] 2.1 `get_experiment_traits(experiment_id_)` returns every trait for every scan in the experiment in
      one call (multi-scan, multi-trait fixture) — no `trait_name_` filter needed. (Scenario: One call
      returns all traits for an experiment)
- [ ] 2.2 Default path (`source_id_`/`run_id_` both NULL) returns only latest-source rows, matching
      `get_scan_traits`'s default output row-for-row on overlapping `(scan, trait)` pairs (build both
      from the same seed and diff). (Scenario: Default path matches get_scan_traits' latest semantics)
- [ ] 2.3 `source_id_` pins an older source; result matches `get_scan_traits(..., source_id_=...)` on
      overlapping rows. (Scenario: Pinning a source matches get_scan_traits byte-for-byte)
- [ ] 2.4 `run_id_` groups by pipeline run; result matches `get_scan_traits(..., run_id_=...)` on
      overlapping rows, including the "run superseded by a newer run" case from the source-aware suite.
      (Scenario: Run grouping matches get_scan_traits byte-for-byte)
- [ ] 2.5 Both `source_id_` and `run_id_` set → raises, same as `get_scan_traits`. (Scenario: Supplying
      both is rejected)
- [ ] 2.6 No cross-source mixing: latest source dropped a trait an older source had → that trait is
      absent, not backfilled. (Scenario: No cross-source mixing)
- [ ] 2.7 A non-finite latest value (`NULL` in `cyl_scan_traits.value`) is returned as a `NULL`-valued
      row, not omitted. (Scenario: Non-finite values are surfaced as NULL)
- [ ] 2.8 Cross-experiment isolation: `get_experiment_traits` for experiment A returns nothing from
      experiment B's scans, including under `source_id_`/`run_id_` pins (mirrors the source-aware
      suite's 2.9a/2.10c regression guards).
- [ ] 2.9 An experiment with no trait rows returns zero rows, no error.
- [ ] 2.10 `list_experiment_trait_sources(experiment_id_)` lists each real source exactly once
      (`source_id`, `source_name`, `pipeline_run_id`), for an experiment with multiple sources across
      multiple scans. (Scenario: Lists an experiment's sources)
- [ ] 2.11 `list_experiment_trait_sources` excludes a legacy NULL-source scan's placeholder (no row with
      `source_id IS NULL`). (Scenario: Legacy NULL-source rows are not listed as a source)
- [ ] 2.12 `list_experiment_trait_sources` for an experiment with only NULL-source (legacy) scans returns
      zero rows.
- [ ] 2.13 `list_experiment_trait_sources` for a different experiment does not leak sources
      (cross-experiment isolation, mirrored from 2.8).
- [ ] 2.14 Role reads: `SET LOCAL ROLE bloom_agent` (and `bloom_user`) can call both
      `get_experiment_traits` and `list_experiment_trait_sources` end-to-end through the full join chain
      (`cyl_scans → cyl_waves → cyl_plants → accessions → species → cyl_experiments`) — the D3
      grant-chain spot-check. No new write policy/grant on any table (drift check).
- [ ] 2.15 `test_migration_body_is_idempotent` — re-apply the migration body on already-applied state;
      both functions still exist with the same signature (catches non-idempotent `CREATE FUNCTION`
      without `OR REPLACE`, or a bare `CREATE` that errors on re-run).
- [ ] 2.16 `test_rollback_restores_prior_state` — apply the rollback; both functions no longer exist;
      re-apply the forward migration and confirm they're back (round-trip safety).
- [ ] 2.17 Confirm every 2.x test above FAILS before implementation (`UndefinedFunction`).

## 3. Implementation — migration (GREEN)

- [ ] 3.1 Create `supabase/migrations/<ts>_get_experiment_traits.sql` (timestamp later than the most
      recent migration at merge time), wrapped in `BEGIN; … COMMIT;`.
- [ ] 3.2 `CREATE FUNCTION public.get_experiment_traits(experiment_id_ bigint, source_id_ bigint DEFAULT
    NULL, run_id_ text DEFAULT NULL) RETURNS TABLE (...)` per design.md D1 — same guard, same
      disjunction, same table-qualified ORDER BY discipline as `get_scan_traits`. Do not add `REVOKE
    EXECUTE ... FROM PUBLIC` (match the existing PUBLIC-execute posture on `get_scan_traits`).
- [ ] 3.3 `CREATE FUNCTION public.list_experiment_trait_sources(experiment_id_ bigint) RETURNS TABLE
    (source_id bigint, source_name text, pipeline_run_id text)` per design.md D2.

## 4. Rollback + types (GREEN)

- [ ] 4.1 Add `supabase/rollbacks/<ts>_get_experiment_traits_rollback.sql`: `DROP FUNCTION IF EXISTS
    get_experiment_traits(bigint, bigint, text); DROP FUNCTION IF EXISTS
    list_experiment_trait_sources(bigint);`.
- [ ] 4.2 Hand-edit all five tracked `database.types.ts` copies (mirroring the source-aware precedent):
      `web/lib`, `web/types`, `packages/bloom-js/src/types`, `packages/bloom-fs/src/types`,
      `packages/bloom-nextjs-auth/src/lib`. Add both new functions to `Functions`/`Args`/`Returns`.

## 5. Validate (GREEN)

- [ ] 5.1 Run `tests/integration/test_cyl_experiment_traits.py` against the compose stack — all green.
- [ ] 5.2 `openspec validate add-bulk-trait-read-rpc --strict` passes.
- [ ] 5.3 `cd web && npx tsc --noEmit` and the Next.js build pass; `packages/bloom-js` + `bloom-fs`
      `tsc -p` clean.
- [ ] 5.4 Migration lint clean (filename `^[0-9]{14}_[a-z0-9_]+\.sql$`, timestamp later than the most
      recent migration); ruff/black/prettier and full pre-merge suite green.

## 6. Docs + follow-up

- [ ] 6.1 Update `bloommcp/docs/data-access-roadmap.md`'s Tier 1 row to ✅ and link this PR.
- [ ] 6.2 File Tier 2's tracking issue ("rewrite `SupabaseReader`'s raw tier to query the DB directly")
      per the roadmap's just-in-time issue policy, now that Tier 1 is reached — reference bloom#546 and
      this change.
