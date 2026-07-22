## 1. Test scaffolding (RED first)

- [x] 1.1 Add `tests/integration/test_cyl_read_path.py` using the `pg_conn` fixture (supabase_admin,
      BYPASSRLS, per-test rollback). Add helpers mirroring `test_cyl_writeback_rpc.py`
      (`_envelope`/`_trait`/`_call`, `_sql_body` for stripping `BEGIN;/COMMIT;` CRLF-safely) and a
      `_seed_experiment_scan(cur, *, accession=None)` building the full join chain. The bare
      writeback `_seed_scan` (just `INSERT INTO cyl_scans DEFAULT VALUES`) gives `plant_id = NULL`, which
      `get_scan_traits`'s INNER joins exclude → **empty result sets that make tests falsely green**. The
      required INSERT recipe (satisfy every NOT-NULL and required-for-join FK; nullable-in-DDL FKs still
      must be set):
      `species DEFAULT VALUES` → `cyl_experiments(name NOT NULL, species_id)` →
      `cyl_waves(experiment_id, number)` → `accessions(name)` (**`name` is NOT NULL + UNIQUE** — generate
      distinct names per accession, needed for the 2.13 two-accession ordering test) →
      `cyl_plants(wave_id, accession_id, germ_day, qr_code)` →
      `cyl_scans(plant_id, date_scanned, plant_age_days)` → `cyl_images(scan_id)`. Return
      `(experiment_id, scan_id, image_ids)`; generalize to seed **N scans (and N accessions) under one
      experiment**.
- [x] 1.2 Helper `_seed_two_sources(cur, image_ids, *, run_ids=(...), values=(...))` calls
      `insert_cyl_result_envelope` twice with the **same** `image_ids` but **distinct**
      `idempotency_key` (else the 2nd call is a pure no-op) and distinct `pipeline_run_id`/trait values;
      the higher (second) `source_id` is "latest". Keep each trait's `scan_key` equal to the envelope's
      `provenance.scan_key` (the RPC rejects a mismatch; the mirrored `_trait`/`_envelope` helpers both
      default to `SK1`, so this holds unless overridden).

## 2. Failing tests covering every spec scenario (RED; one `def test_...` per assertion)

- [x] 2.0 `test_two_sources_one_scan_seed` — after `_seed_two_sources`, assert exactly 2 distinct
      `cyl_trait_sources` rows, both `cyl_scan_traits.scan_id` = the seeded scan, `max(source_id)` = the
      2nd source (the linchpin every is_latest test stands on).
- [x] 2.1 `cyl_scan_traits_source` exposes `source_id`/`source_name`/`pipeline_run_id`/`trait_name` with
      `value`/`scan_id`/`trait_id` unchanged. (Scenario: View exposes the source dimension)
- [x] 2.2 `is_latest = true` only for the higher-`source_id` rows; original-source rows `false`.
      (Scenario: is_latest marks the max-source rows per scan)
- [x] 2.3 A scan whose rows all have `source_id IS NULL` has `is_latest = true`. Seed via
      `_seed_experiment_scan` (a real plant-chained scan, so 2.15 can read it through `get_scan_traits`)
      then **direct INSERT** the NULL-`source_id` `cyl_scan_traits` rows against that `scan_id` as
      supabase_admin (BYPASSRLS; no FORCE RLS, so the change-E lockdown does not block it) — the RPC
      always mints a source, so a NULL-source row can only be seeded directly. (Scenario: Legacy
      NULL-source rows)
- [x] 2.4 `pipeline_run_id` surfaced from `metadata`; `NULL` when the source omitted it (seed with
      `_envelope(..., pipeline_run_id=None)`). (Scenario: pipeline_run_id is surfaced)
- [x] 2.5 Default path returns the latest source's value only, no duplicate rows. (Scenario: Default
      read returns the latest source's values only)
- [x] 2.6 Latest run wrote only trait A while an older source wrote A+B → default returns A, not B.
      (Scenario: No cross-source mixing when the latest run dropped a trait)
- [x] 2.7 `cyl_scan_traits_latest` returns ≤1 row per `(scan_id, trait_id)` for **source-bearing** data.
      (Scenario: Latest view has no duplicate rows)
- [x] 2.7a Non-finite latest value: seed a latest-source trait whose stored `value` is NULL via the RPC
      with `_trait(name, None)` or `_trait(name, "NaN")` (JSON-legal tokens the RPC stores as NULL) — do
      **not** pass `float('nan')` (`json.dumps` emits the invalid-JSON token `NaN`, which the `::jsonb`
      cast rejects); a direct `INSERT … value=NULL` is the alternative. Assert the default read returns it
      as a `trait_value = NULL` row (not omitted). (Scenario: A non-finite latest value is surfaced as NULL)
- [x] 2.8 `get_scan_traits(exp, trait)` (2-arg) returns latest per scan — **via direct SQL AND via
      PostgREST HTTP** using the `api` fixture with an api*key: `api("/api/rest/v1/rpc/get_scan_traits",
      api_key=service_role_key, method="POST", data={"experiment_id*": ..., "trait*name*": ...})`⇒ 200,
    **not** PGRST203 (this is what proves the no-overload design). Note the harness path prefix is
   `/api/rest/v1/...` and the call MUST pass a key or it 401s. (Scenario: Backward-compatible
      two-argument call returns latest)
- [x] 2.9 `source_id_` set to the older source returns the older values. (Scenario: Pinning an older
      source returns the older values)
- [x] 2.9a `source_id_` set to a source from a **different** experiment returns zero rows (regression
      guard: the experiment filter must stay conjoined with the pin branch — no cross-experiment leak).
- [x] 2.10 Two scans in one experiment share a `pipeline_run_id`; `run_id_` returns rows for **both**
      scans. (Scenario: Run id groups an experiment by pipeline run)
- [x] 2.10a Seed scan A (`run-1` then newer `run-2`) + scan B (`run-1` only); `run_id_='run-1'` returns
      A's `run-1` values (not A's `run-2`) and B's `run-1` values — selection is by run, no implicit
      latest filter. (Scenario: Run id returns a scan's run values even after a newer run superseded it)
- [x] 2.10b Seed one scan/trait delivered twice under the same `pipeline_run_id` (two sources, distinct
      `idempotency_key`); `run_id_` returns a **single** row for that `(scan, trait)` — the latest
      delivery within the run (`max(source_id)`). (Scenario: Duplicate deliveries within one run do not
      duplicate rows)
- [x] 2.10c `run_id_` set to a run in a **different** experiment returns zero rows (regression guard for
      the run branch — the experiment filter must stay conjoined with the parenthesized disjunction).
- [x] 2.11a A legacy NULL-`pipeline_run_id` scan is absent from any `run_id_` query (its
      `pipeline_run_id IS NULL` never equals `run_id_`), confirming run grouping ignores unrun data.
- [x] 2.11 `run_id_` set to a value no source carries (incl. a `pipeline_run_id IS NULL` source) returns
      zero rows, no error. (Scenario: A run id matching no source returns no rows)
- [x] 2.12 Both `source_id_` and `run_id_` non-null → raises. (Scenario: Supplying both is rejected)
- [x] 2.13 Result column names/types/order are byte-identical to the legacy function (assert via
      `pg_get_function_result` or a 1-row call's `cur.description`); rows ordered by
      `accession_name, plant_id` (seed two accessions out of order). (Scenario: Result columns and
      ordering are preserved)
- [x] 2.14 An experiment/trait with no trait rows returns zero rows, no error. (Scenario: An experiment
      with no matching traits returns cleanly)
- [x] 2.15 Legacy NULL-source scan (direct-insert seed) is returned by the default `get_scan_traits`
      path (exercises the `IS NOT DISTINCT FROM` NULL branch end-to-end through the function).
- [x] 2.16 `cyl_scan_trait_names` lists distinct latest names; a name only in a superseded older source
      is excluded. (Scenarios: Lists distinct names present in latest data / A name only in a superseded
      source is excluded)
- [x] 2.16a Seed a latest-source row with `trait_id = NULL` (direct insert); assert `cyl_scan_trait_names`
      contains no `NULL` entry (the `WHERE trait_name IS NOT NULL` guard).
- [x] 2.17 Role reads: `SET LOCAL ROLE` to `bloom_agent`/`bloom_user`/`bloom_admin` can SELECT all three
      views **including the joined `source_name` column** and call `get_scan_traits`. Guard with a
      standalone non-BYPASSRLS assertion mirroring the writeback suite's `test_bloom_roles_are_not_bypassrls`
      (there is no combined set-role+assert helper — use the two idioms: inline `SET LOCAL ROLE` and a
      separate `pg_roles.rolbypassrls` check). No new write policy/grant on the three tables (drift check).
      (Requirement: Read path stays open to read roles with no RLS change)
- [x] 2.18 `test_migration_body_is_idempotent` — re-apply the migration body on already-applied state;
      the two views and the 4-arg function still exist (catches non-idempotent CREATE). (Scenario:
      Forward migration adds the read surface)
- [x] 2.19 `test_rollback_restores_prior_read_surface` — apply the rollback body; assert the two new
      views are gone, exactly **one** `get_scan_traits` remains with `(bigint, text)` identity args, and
      `cyl_scan_trait_names` is back to `SELECT name FROM cyl_traits` (assert it returns a
      registered-but-unmeasured name the latest-only view would have excluded). Also assert recreated
      `cyl_scan_trait_names` is readable by `bloom_agent` post-migration (grant re-issued). (Scenarios:
      Rollback restores the prior read surface without overload ambiguity / Recreated trait-names view
      stays readable)
- [x] 2.20 Confirm every 2.x test above FAILS before implementation — views/function absent raise
      `psycopg.errors.UndefinedTable` / `UndefinedFunction`; the 4-arg call raises `UndefinedFunction`.

## 3. Implementation — migration (GREEN)

- [x] 3.1 Create `supabase/migrations/20260701000000_cyl_trait_read_source_aware.sql` (timestamp > the
      writeback migration 20260630180000), wrapped in `BEGIN; … COMMIT;`, dependency-ordered.
- [x] 3.2 `CREATE OR REPLACE VIEW public.cyl_scan_traits_source WITH (security_invoker = on)` (idempotent
      re-run) selecting the **8 spec columns, no `id`**: `cst.scan_id, cst.trait_id, t.name AS trait_name,
    cst.value, cst.source_id, s.name AS source_name, s.metadata->>'pipeline_run_id' AS pipeline_run_id,
    (cst.source_id IS NOT DISTINCT FROM max(cst.source_id) OVER (PARTITION BY cst.scan_id)) AS is_latest`
      from `cyl_scan_traits cst LEFT JOIN cyl_trait_sources s ON s.id = cst.source_id LEFT JOIN cyl_traits
    t ON t.id = cst.trait_id`. Then literal `GRANT SELECT ON public.cyl_scan_traits_source TO
    bloom_agent, bloom_user, bloom_admin, authenticated;`.
- [x] 3.3 `CREATE OR REPLACE VIEW public.cyl_scan_traits_latest WITH (security_invoker = on) AS SELECT
    scan_id, trait_id, trait_name, source_id, value FROM public.cyl_scan_traits_source WHERE is_latest;` + literal `GRANT SELECT … TO bloom_agent, bloom_user, bloom_admin, authenticated;`.
- [x] 3.4 `DROP VIEW IF EXISTS public.cyl_scan_trait_names; CREATE VIEW public.cyl_scan_trait_names WITH
    (security_invoker = on) AS SELECT DISTINCT trait_name FROM public.cyl_scan_traits_latest WHERE
    trait_name IS NOT NULL ORDER BY trait_name;` + **re-issue** literal `GRANT SELECT ON
    public.cyl_scan_trait_names TO bloom_agent, bloom_user, bloom_admin, authenticated;` (the prior grant
      does not survive DROP). (DROP+CREATE, not OR REPLACE, because the column set changes.)
- [x] 3.5 `DROP FUNCTION IF EXISTS public.get_scan_traits(bigint, text);` then `CREATE FUNCTION
    public.get_scan_traits(experiment_id_ bigint, trait_name_ text, source_id_ bigint DEFAULT NULL,
    run_id_ text DEFAULT NULL) RETURNS TABLE (scan_id bigint, date_scanned text, plant_age_days int,
    wave_number int, plant_id bigint, germ_day int, plant_qr_code text, accession_name text,
    trait_name text, trait_value float) LANGUAGE plpgsql STABLE`. Leading guard
      `IF source_id_ IS NOT NULL AND run_id_ IS NOT NULL THEN RAISE EXCEPTION …`. `RETURN QUERY` selecting
      `src.trait_name`/`src.value` from `cyl_scan_traits_source src` joined `cyl_scans ON cyl_scans.id =
    src.scan_id → … → accessions`; project `cyl_scans.date_scanned` **raw** (coerced `date→text`, not
      `to_char`). WHERE, with the disjunction **wrapped in one parenthesized AND-operand** (else the
      run/pin branches detach from the experiment filter → cross-experiment leak):
      `WHERE cyl_experiments.id = experiment_id_ AND src.trait_name = trait_name_ AND (
       (source_id_ IS NULL AND run_id_ IS NULL AND src.is_latest)
       OR (source_id_ IS NOT NULL AND src.source_id = source_id_)
       OR (run_id_ IS NOT NULL AND src.source_id = (SELECT max(s2.source_id)
             FROM public.cyl_scan_traits_source s2
             WHERE s2.scan_id = src.scan_id AND s2.trait_id = src.trait_id
               AND s2.pipeline_run_id = run_id_)) )`
      `ORDER BY accessions.name, cyl_plants.id` — **table-qualified**, NOT the legacy
      `ORDER BY accession_name, plant_id`: in a `plpgsql` `RETURNS TABLE` function bare `plant_id` is
      ambiguous (OUT var vs `cyl_scans.plant_id`) and raises on every call under
      `variable_conflict = error`. Qualify all select/sort identifiers to their tables. **Do NOT** add
      `REVOKE EXECUTE … FROM PUBLIC` (keep prior PUBLIC-execute posture).

## 4. Rollback + types (GREEN)

- [x] 4.1 Add `supabase/rollbacks/20260701000000_cyl_trait_read_source_aware_rollback.sql`: `DROP VIEW IF
    EXISTS cyl_scan_trait_names; DROP VIEW IF EXISTS cyl_scan_traits_latest; DROP VIEW IF EXISTS
    cyl_scan_traits_source;` (dependents first); `DROP FUNCTION IF EXISTS get_scan_traits(bigint, text,
    bigint, text);` **then** recreate the prior 2-arg `get_scan_traits(bigint, text)` `LANGUAGE SQL`
      verbatim — copy the 20241119232238 body exactly, **including its `AS accession_name`/`AS plant_id`
      SELECT-list aliases** (its `ORDER BY accession_name, plant_id` is a SQL-function output-alias
      reference that fails if the aliases are dropped); recreate `cyl_scan_trait_names AS SELECT name FROM
    cyl_traits` + its grant.
- [x] 4.2 Hand-edit all **five** tracked `database.types.ts` copies (mirroring #371; local regen will
      fail per the known dev-DB gap): `web/lib`, `web/types`, `packages/bloom-js/src/types`,
      `packages/bloom-fs/src/types`, `packages/bloom-nextjs-auth/src/lib`. Add the two new views and the
      4-arg `get_scan_traits` with `source_id_`/`run_id_` **optional** in `Args` so `TraitExplorer.tsx`'s
      2-arg call still typechecks; keep `Returns` columns identical.

## 5. Validate (GREEN)

- [x] 5.1 Run `tests/integration/test_cyl_read_path.py` against the compose stack — all green. CI's
      `compose-health-check` (db push → pytest) is the authoritative signal; local DB may lack roles.
- [x] 5.2 `openspec validate add-cyl-trait-read-source-aware --strict` passes.
- [x] 5.3 `cd web && npx tsc --noEmit` and the Next.js build pass against the edited types (catches a
      stale `get_scan_traits` arg list); `packages/bloom-js` + `bloom-fs` `tsc -p` clean.
- [x] 5.4 Migration lint clean (filename `^[0-9]{14}_[a-z0-9_]+\.sql$`, timestamp > 20260630180000);
      ruff/black/prettier and full pre-merge suite green.

## 6. Docs + follow-up

- [x] 6.1 In `_WIKI/BLOOMMCP/README.md` "Supabase data access" section, **rewrite** the
      `client.table("cyl_scan_traits").select(...)` example to read `cyl_scan_traits_latest` (note
      `cyl_scan_traits_source` for the source/run dimension); cross-reference the spec/migration for the
      latest-selection rule rather than restating it. Cite the section by name (it is unnumbered).
- [x] 6.2 File the broadened sibling issue "repoint source-blind cyl trait readers at the latest-source
      surface" (covering: rebuild `cyl_trait_by_experiment_wave` on `cyl_scan_traits_latest`; repoint +
      fix `cyl_tools.py` `get_scan_traits_tool`; refresh `context_tools.py` `CONTEXT_CYL`). Reference it
      from the PR body so the deferral is durably tracked before the change folder is archived.
