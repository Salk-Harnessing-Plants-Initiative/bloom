> **Commit grouping** (per review): land section 1+2 as one commit (`feat(db): ...`), section 3+4 as
> one commit (`feat(workflows): ...`), sections 5 as doc commit(s) — matching how this repo's actual
> precedents (bloom PR #469, the D/E write-back RPC change) shipped tests-first-but-same-commit as
> their implementation, not as standalone red-test commits. Do not split 1 from 2 or 3 from 4 into
> separate commits.

## 1. Schema & RLS tests first (red)

- [x] 1.1 Create `tests/integration/test_cyl_pipeline_dispatch.py` (live-compose-DB, rollback-wrapped,
      matching the pattern in `tests/integration/test_cyl_scan_intermediates.py` — a single `pg_conn`
      fixture from `tests/integration/conftest.py`, each test in `try/finally: pg_conn.rollback()`.
      **Not** `tests/integration/test_cyl_video_queue.py` — that file only exists on the unmerged PR
      #469 branch, not in this checkout.). Write, and confirm currently **fail** (tables don't exist
      yet):
  - `test_cyl_pipeline_runs_defaults` — insert with only `target_level`/`target_id`/`params`/
    `requested_by`; assert `status = 'queued'`, all four counts default to `0`, timestamps null
    where expected.
  - `test_cyl_pipeline_runs_scan_ids_target_id_nullable` — insert with `target_level='scan_ids'`,
    `target_id=NULL`; assert success.
  - `test_cyl_pipeline_run_scans_unique_run_scan` — insert two rows with the same `(run_id, scan_id)`;
    assert the second raises a unique-violation.
  - `test_cyl_pipeline_run_scans_minimal_insert` — insert supplying only `run_id`/`scan_id`/
    `status='queued'`; assert `batch_index`/`argo_workflow_name`/`source_id` are all `NULL`.
- [x] 1.2 In the same file, write and confirm **fail** (no roles/policies yet):
  - `test_bloom_user_read_only` — `SET LOCAL ROLE bloom_user`; `SELECT` succeeds, `INSERT`/`UPDATE`
    raise `InsufficientPrivilege`.
  - `test_bloom_workflows_can_insert_but_not_update` — `SET LOCAL ROLE bloom_workflows`; insert a run
    row (succeeds); then attempt to `UPDATE` that row's `status` (must raise `InsufficientPrivilege`
    — proves the Phase-1 least-privilege boundary; `UPDATE` is deliberately not granted yet).
  - `test_anon_denied` — `SET LOCAL ROLE anon`; `SELECT` on either table returns zero rows (RLS
    default-deny).
- [x] 1.3 Write and confirm **fail** (publication entries don't exist yet):
  - `test_realtime_publication_includes_both_tables` — query `pg_publication_tables` for
    `supabase_realtime`; assert both `cyl_pipeline_runs` and `cyl_pipeline_run_scans` are present.
  - `test_realtime_publication_add_table_is_idempotent` — re-apply the migration's
    `ALTER PUBLICATION ... ADD TABLE` blocks a second time inside the open transaction; assert no
    `duplicate_object` error.
- [x] 1.4 Write and confirm **fail** (grant doesn't exist yet):
  - `test_bloom_workflows_can_check_all_sources_for_a_scan` — `SET LOCAL ROLE bloom_workflows`;
    select `scan_id, source_id` from `cyl_scan_traits` joined to `id, metadata` on
    `cyl_trait_sources`, for a scan seeded with 2+ sources (an earlier one with `params={age:14}`,
    a later one with `params={age:21}`); assert the query returns **all** of that scan's sources, not
    only the highest-`source_id` one — proving the grant supports an existence check across sources,
    not a latest-only filter.
  - **Implementation-time addition** (discovered while writing `pipeline.py`'s enumeration logic —
    `cyl_scans_extended`'s inner joins can't distinguish "wave/experiment exists with zero scans"
    from "doesn't exist at all", so a direct existence check against `cyl_waves`/`cyl_experiments` is
    required; `bloom_workflows` had no grant on either): `test_bloom_workflows_can_check_wave_existence`,
    `test_bloom_workflows_can_check_experiment_existence` — `SET LOCAL ROLE bloom_workflows`; assert
    `SELECT id` succeeds for an existing id and returns no row for a nonexistent one, on both tables.
- [x] 1.5 Write and confirm **fail** (queue/function don't exist yet):
  - `test_enqueue_creates_pgmq_message` — `SET LOCAL ROLE bloom_workflows`; call
    `enqueue_cyl_pipeline_batch(run_id, batch_index, scan_ids)`; assert it returns a `msg_id` and
    `pgmq.read('cyl_pipeline_dispatch', ...)` returns a message with the same `run_id`/`batch_index`/
    `scan_ids`.
  - `test_enqueue_execute_denied_to_anon_authenticated_public` — `has_function_privilege` for `anon`,
    `authenticated`, and `PUBLIC` against `enqueue_cyl_pipeline_batch`'s signature all report `false`;
    the same check for `bloom_workflows` reports `true`.
- [x] 1.6 Rollback fidelity (currently unexercised in the original draft — added per review). **Note:**
      `test_cyl_scan_intermediates.py`'s existing `test_rollback_script_drops_the_table` only applies
      a *rollback* body inside an open transaction (against a migration already applied for real by
      `make migrate-local` outside the test) — no file in this repo applies a forward *migration* body
      inside an open transaction. This is a new technique to write, not a copy-paste of an existing
      one (DDL is transactional in Postgres, so it should work; just don't assume a template exists):
  - `test_migration_body_is_idempotent` — apply the migration SQL body a second time in an open
    transaction; confirm no error.
  - `test_rollback_removes_everything` — apply the migration body, then the rollback body, both in
    one open transaction; assert `cyl_pipeline_runs`, `cyl_pipeline_run_scans`,
    `enqueue_cyl_pipeline_batch`, and the `cyl_pipeline_dispatch` pgmq queue all no longer exist; then
    let the fixture's teardown roll back.

**Result:** all 17 tests (13 originally planned + 2 wave/experiment-existence + the 2 rollback tests
this section grew to include) fail as expected — `UndefinedTable`/`UndefinedFunction`/
`InsufficientPrivilege` — before section 2, confirming red.

## 2. Migration (green)

- [x] 2.1 Re-confirm the latest migration timestamp on `origin/staging` immediately before writing
      the migration (do not reuse a timestamp scoped earlier in this proposal's lifetime without
      re-checking — `staging` may have moved). Re-verified at implementation time: `staging` had
      moved (a `bloommcp_usage` migration landed at `20260730000000`); branch fast-forwarded to the
      new tip; `20260730120000` confirmed still after the latest (`20260730000000`).
- [x] 2.2 Add `supabase/migrations/20260730120000_create_cyl_pipeline_runs.sql`: both tables, RLS
      enabled + the policies (`admin_all_*`, `agent_read_*`, `user_read_*`, plus `bloom_workflows`'s
      `SELECT`/`INSERT` grants+policies — **no `UPDATE`**, and no `user_update_*` policy, matching
      `20260710000000_bloom_user_read_only_cleanup.sql`'s repo-wide convention), the
      `ALTER PUBLICATION supabase_realtime ADD TABLE ...` idempotent blocks for both tables,
      `pgmq.create('cyl_pipeline_dispatch')` (existence-guarded), the `enqueue_cyl_pipeline_batch`
      `SECURITY DEFINER` function with the explicit `REVOKE ... FROM PUBLIC, anon, authenticated` /
      `GRANT ... TO bloom_workflows`, and the new **column-scoped** grants for the dedup-preview join:
      `GRANT SELECT (scan_id, source_id) ON cyl_scan_traits TO bloom_workflows` and
      `GRANT SELECT (id, metadata) ON cyl_trait_sources TO bloom_workflows`. **Implementation-time
      addition:** also a new `workflows_read_*` policy + `GRANT SELECT (id)` on `cyl_waves` and on
      `cyl_experiments` (the latter's policy scoped `USING (deleted_at IS NULL)`, matching that
      table's existing read-policy convention) — needed for the enumeration route to distinguish an
      existing-but-empty wave/experiment from a nonexistent one.
- [x] 2.3 Add the companion `supabase/rollbacks/20260730120000_create_cyl_pipeline_runs_rollback.sql`
      (drops both tables, the function, the queue, removes the publication entries, revokes the
      column-scoped grants — including the `cyl_waves`/`cyl_experiments` additions above).
- [x] 2.4 `make migrate-local`; re-run all of section 1's tests — confirm every one now passes,
      including 1.6's actual apply-then-rollback-then-verify test. (`make migrate-local` needs the
      `supabase` CLI, unavailable in this environment — applied the migration directly via `psql`
      against the running local dev stack instead; all 17 tests pass.)
- [x] 2.5 `make gen-types`; commit the four regenerated `database.types.ts` files
      (`packages/bloom-fs`, `packages/bloom-js`, `packages/bloom-nextjs-auth`, `web/lib`) alongside
      the migration in the same commit. (Generated directly via
      `npx supabase gen types typescript --db-url ...`, since `make gen-types`'s own Makefile
      defaults don't pick up this stack's non-default host port/password without an override.
      **Caught at pre-merge time:** the first generation used `2>&1`, merging the Supabase CLI's
      stderr diagnostics — "Connecting to ..." and an update-available notice — into the same file
      as the real TypeScript output, corrupting all four copies with a stray first/last line. This
      would have broken the `build-and-audit` CI job. Fixed by regenerating with stderr routed
      separately; `web`'s `tsc --noEmit` and full `next build` both confirmed clean afterward — see
      §6.4.)
- [x] 2.6 Run the migration linter (`tests/integration/test_lint_migrations.py`, or
      `scripts/lint_migrations.sh` directly) against the new migration file before moving on.
      (The pytest wrapper fails in this environment on a pre-existing WSL/CRLF quirk, same one noted
      in `update-cyl-writeback-workflows-grant`'s own task 4.3 — confirmed by running
      `scripts/lint_migrations.sh origin/staging` directly: passed, "checked 1 new file(s)".)

## 3. Route/module tests first (red)

> **Note on 3.1's placement relative to the commit-grouping note above:** task 3.1 below (the
> `sleap-roots-contracts` dependency) touches `services/workflows/pyproject.toml`/`uv.lock` — a
> workflows-service file, not a database file — so it correctly belongs in the section-3+4
> `feat(workflows): ...` commit, not the section-1+2 `feat(db): ...` commit (an earlier draft placed
> it in section 2; moved here on review). It must still be done **before** the rest of section 3,
> since `test_pipeline.py`'s dedup fixtures need `compute_param_hash` to seed expected `param_hash`
> values, not just `pipeline.py`'s implementation in section 4.

- [x] 3.1 Add `sleap-roots-contracts>=0.1.0a5` to `services/workflows/pyproject.toml` (matching
      `bloomcli/pyproject.toml`'s plain open-floor pin — no git dependency, no heavy transitive deps;
      confirmed it doesn't conflict with this service's existing `numpy>=2.0.0`/`pillow>=12.0.0`
      floors) and run `uv lock` in `services/workflows/` to regenerate `uv.lock`. **Without this task,
      CI's `--frozen` lock checks (`pr-checks.yml`'s workflows-tests and pip-audit steps) will fail
      the first time `pipeline.py` imports `compute_param_hash`.** Do this before 3.2 — the test file
      below imports it too. (`uv lock` resolved cleanly: added `sleap-roots-contracts==0.1.0a5` +
      transitive `pyyaml==6.0.3`; `pydantic` already present transitively via `supabase`. `pip-audit`
      on the full tree: clean, no known vulnerabilities.)
- [x] 3.2 Create `services/workflows/tests/test_pipeline.py`, matching `test_video.py`'s hand-rolled
      fake-client convention (a `_Query`/`_Client` fake implementing exactly the subset of the
      supabase-py fluent API `pipeline.py` calls, plus a fake for
      `.rpc("enqueue_cyl_pipeline_batch", ...)`). Write, and confirm currently **fail** (module
      doesn't exist yet):
  - Request validation: `test_rejects_scan_ids_target_with_empty_list`,
    `test_rejects_experiment_target_with_null_id`, `test_rejects_scan_ids_present_with_non_scan_ids_level`,
    `test_rejects_non_integer_target_id` (parametrize over string/float sub-cases),
    `test_rejects_non_positive_target_id` (separate from the type-check test — a value-range case,
    not a type-check case; parametrize over both `0` and negative values, since the requirement says
    "positive integer," which excludes zero too), `test_rejects_malformed_json_body`,
    `test_accepts_scan_ids_with_populated_list`.
  - Enumeration (positive path for all four target_levels, not just wave):
    `test_enumerate_scan_resolves_single_scan`, `test_enumerate_wave_resolves_via_cyl_scans_extended`,
    `test_enumerate_experiment_resolves_via_cyl_scans_extended`,
    `test_enumerate_scan_ids_resolves_exact_given_list`, `test_enumerate_unknown_target_404`,
    `test_enumerate_unknown_scan_id_in_scan_ids_404`,
    `test_enumerate_existing_wave_with_zero_scans_succeeds_with_scan_count_zero`,
    `test_enumerate_existing_experiment_with_zero_scans_succeeds_with_scan_count_zero` (the spec's
    zero-scan scenario explicitly covers both wave and experiment — both need their own test).
  - Dedup preview (informational only — **renamed from the original "skip" framing**; checks ALL of
    a scan's sources by comparing `compute_param_hash(request_params)` against each source's
    already-stored `metadata->'params'->>'param_hash'` — NOT by re-hashing the stored `metadata`
    column, which would almost never match since it also carries per-run code shas/digests; see
    design.md for both this and why `is_latest` is the wrong rule here):
    `test_dedup_preview_counts_matching_prior_source_but_still_enqueues_it`,
    `test_dedup_preview_finds_older_matching_source_when_newest_source_has_different_params`,
    `test_dedup_preview_counts_scan_once_even_with_two_matching_sources`,
    `test_dedup_preview_excludes_scan_with_no_prior_source_from_reused_count`,
    `test_dedup_preview_excludes_scan_whose_sources_all_have_differing_params`,
    `test_all_scans_matching_prior_source_still_all_enqueued_not_short_circuited`,
    `test_dedup_preview_issues_one_batched_query_not_a_per_scan_loop` — run the same dedup-preview
    code path twice with different enumerated-scan-count fixtures (3 scans, then 30 scans; both
    fixtures seed at least one scan with a prior source, so both queries actually run); assert the
    fake client's query-method call count is exactly 2 in both runs.
  - Row writing: `test_writes_run_and_scan_rows_before_enqueue`,
    `test_zero_scan_target_writes_run_row_with_scan_count_zero_and_completes`.
  - Batching: `test_chunks_all_scans_into_batch_size_groups_and_enqueues_each`,
    `test_exact_multiple_of_batch_size_produces_no_empty_trailing_batch`.
  - Response: `test_success_response_shape`.
- [x] 3.3 Auth/rate-limit — two separate tests (split from a single bundled test per review, so each
      spec scenario has its own named assertion). **Implementation note:** these use FastAPI's
      `TestClient` + `app.dependency_overrides` (not the hand-rolled-fake convention) — calling
      `main`'s route function directly bypasses `Depends()` resolution entirely, so it's the only way
      to prove the route's auth wiring without re-testing `auth.py`'s own already-covered logic:
  - `test_pipeline_route_401_without_auth` — override `require_supabase_user` to raise 401; assert
    401, assert `pipeline.trigger_pipeline` never called.
  - `test_pipeline_route_429_before_any_work` — override `require_supabase_user` to succeed, monkeypatch
    `main.enforce_rate_limit` to raise 429 with `Retry-After`; assert 429, assert the header, assert
    `pipeline.trigger_pipeline` never called.

**Result:** all 31 tests fail at collection (`ModuleNotFoundError: No module named 'pipeline'`) before
section 4, confirming red.

## 4. Route/module implementation (green)

- [x] 4.1 Add `services/workflows/pipeline.py`: `trigger_pipeline(body: dict, user_id: str) -> dict`
      (mirroring `video.py`'s `generate_experiment_scan_video` entry-point shape) implementing
      validation → enumerate → dedup preview (informational, never gates enqueue; one batched query
      via `compute_param_hash` from `sleap-roots-contracts`, comparing against each source's stored
      `param_hash`, not re-hashing `metadata`) → insert rows → batch → enqueue → response, per the
      spec's requirements in order.
- [x] 4.2 Add `@app.post("/pipeline")` (**not** `/workflows/pipeline` — Caddy's
      `handle_path /workflows/*` already strips that prefix before proxying to this service; every
      existing route here is registered the same prefix-free way) in `services/workflows/main.py`:
      `Depends(require_supabase_user)` for auth, `enforce_rate_limit(user_id)` called explicitly,
      delegates to `pipeline.trigger_pipeline`.
- [x] 4.3 Re-run all of section 3's tests (3.2, 3.3) — confirm every one now passes. (31/31 green; full
      `services/workflows/tests/` suite also re-run: 68/68 passed, no regressions to
      `test_auth.py`/`test_video.py`/etc.)

**Additional live verification (beyond the task list):** rebuilt and restarted the `workflows` Docker
container against the local dev stack; confirmed it boots cleanly with the new dependency (no import
errors), `/health` responds, and `/pipeline` correctly returns `401` for both a missing and an invalid
bearer token via real HTTP requests — proving the FastAPI dependency wiring works end-to-end, not
just in the unit tests. The full authenticated happy path (`app_client()` signing in as the
`bloom_workflows` app user) could not be exercised live — `WORKFLOWS_SUPABASE_EMAIL`/`_PASSWORD`
aren't configured in this local `.env.dev` (a pre-existing local-dev-setup gap, not something this
proposal introduces or needs to fix).

## 5. Documentation

- [x] 5.1 Update `_WIKI/SUPABASE/README.md`:
  - **Do NOT extend** the existing sanctioned-`EXECUTE`-roles sentence for `insert_cyl_result_envelope`
    (`bloom_writer, service_role, bloom_admin, bloom_workflows`) — `enqueue_cyl_pipeline_batch`'s
    `EXECUTE` is granted to `bloom_workflows` **only**, a different (narrower) role list, so folding
    it into that sentence would misstate that the other three roles can call it too. Add a **separate**
    sentence/clause stating `enqueue_cyl_pipeline_batch`'s own single-role grant.
  - Add a new `### pgmq queues` subsection (none currently exists in this file) documenting
    `cyl_pipeline_dispatch` and the triple-revoke (`PUBLIC, anon, authenticated`) pattern, so future
    queues have a written convention to follow instead of only a code precedent.
  - Note the new column-scoped `bloom_workflows` grants on `cyl_scan_traits`/`cyl_trait_sources`
    (plus the `cyl_waves`/`cyl_experiments` existence-check grants added during implementation, §2.2).
- [x] 5.2 Update `services/workflows/README.md`:
  - Added the new route to the endpoint inventory table (path `/pipeline`, proxied externally as
    `/workflows/pipeline`) and a new "### Pipeline trigger" subsection (curl example), matching the
    existing "### Video generation" subsection's format.
  - Added bullets to the "Layer 2 — service identity" grant list (the "app user needs only" closing
    sentence was inaccurate once this landed): `SELECT`/`INSERT` on
    `cyl_pipeline_runs`/`cyl_pipeline_run_scans`, `SELECT (scan_id, source_id)` on `cyl_scan_traits`,
    `SELECT (id, metadata)` on `cyl_trait_sources`, `SELECT (id)`-only on `cyl_waves`/`cyl_experiments`,
    and `EXECUTE` on `enqueue_cyl_pipeline_batch`. Split the closing sentence to correctly attribute
    the original three items to `_create_workflows_role.sql` and the rest to
    `_create_cyl_pipeline_runs.sql`.
  - Added a paragraph to "On-demand vs queued generation" noting this route is a **third**, pgmq-based
    dispatch path — distinct from both the synchronous video route and the `video_jobs`/`pg_notify`
    queued-video mechanism.
  - Fixed the two now-stale rate-limit descriptions ("Auth model — Layer 1" sentence and the
    `WORKFLOWS_RATE_LIMIT`/`WORKFLOWS_RATE_WINDOW_SECONDS` Configuration-table rows), since `/pipeline`
    shares the existing limiter rather than getting its own.

## 6. Validation

- [x] 6.1 `openspec validate add-cyl-pipeline-trigger --strict` passes.
- [x] 6.2 `uv run --extra test pytest tests/integration/test_cyl_pipeline_dispatch.py -v --tb=short`
      passes in full (17/17). (No CI wiring needed — `pr-checks.yml`'s `compose-health-check` job
      already runs `pytest tests/integration/ -v --tb=short` over the whole directory.)
- [x] 6.3 `uv run --extra test pytest services/workflows/tests/ -v --tb=short` passes in full (68/68,
      no regressions to existing `test_auth.py`/`test_video.py`/etc. — also whole-directory, no CI
      wiring needed).
- [x] 6.4 Pre-merge checks relevant to this change (full `/pre-merge` needs an open PR for its
      GitHub-status steps, and its Python-audit loop doesn't currently include `services/workflows` —
      a pre-existing gap in that checklist doc, not introduced here): `openspec validate --strict`
      (passes); `pip-audit` on `services/workflows`'s full dependency tree including the new
      `sleap-roots-contracts` (clean, no known vulnerabilities); `ruff check` + `black --check` on
      every new/changed Python file (clean after auto-fixing import ordering + one unused-variable
      lint finding); Docker image rebuild + live boot/route smoke test (see §4). Matching the
      `build-and-audit` CI job exactly: built `packages/bloom-js`/`packages/bloom-fs` (`npx tsc -p
      tsconfig.json`, both clean), then `web`'s `npx tsc --noEmit` (clean of `database.types.ts`
      errors after the §2.5 fix — the only remaining errors are two pre-existing, unrelated `NODE_ENV`
      read-only issues in test files this change never touched) and a full `next build` (succeeds).
      Full
      `tests/integration/` suite run for a repo-wide regression check: 146 failed / 319 passed / 9
      skipped / 8 errors — **zero of the 146 failures reference `test_cyl_pipeline_dispatch.py`**
      (confirmed by grep). Every failure is pre-existing/environmental: HTTP-endpoint tests needing
      the full `docker-compose.prod.yml` stack (`make prod-up`, not running here — matching the exact
      precedent in `update-cyl-writeback-workflows-grant`'s own task 4.3), the same WSL/CRLF
      `test_lint_migrations.py` quirk noted in §2.6, and
      `test_migrations.py::test_all_migrations_recorded` /
      `test_local_dev_bootstrap.py::test_all_migrations_applied` — these check
      `supabase_migrations.schema_migrations`, populated only by the real `supabase db push` (CLI
      unavailable here; this migration and the concurrently-merged `bloommcp_usage` one were both
      applied via direct `psql` instead, so neither is tracked in that table locally — resolves
      automatically once CI/a real `supabase db push` applies them).

## 7. PR #570 review fixes (red → green)

A 5-lens adversarial PR review (Code Quality, Testing, Scientific Rigor, Security, Behavioural
Correctness) found one BLOCKING bug (independently traced by 3 of 5 reviewers) and several IMPORTANT
gaps, all sharing a root cause: untrusted request fields reaching downstream calls with no
bounds/finite-value checking, plus two grant/ordering gaps found by direct code inspection. Full
review: PR #570's review comment. Fixed here via the same TDD discipline as the rest of this change.

- [x] 7.1 Add failing tests first (red), then implement (green):
  - **Duplicate `scan_ids` (BLOCKING):** `test_scan_ids_with_duplicates_are_deduped` (unit) — body
    `scan_ids=[5,5,7]`; assert `scan_count=2`, exactly 2 `cyl_pipeline_run_scans` rows, no crash.
    Confirmed failing (crashed on the fake client's insert, or — once the fake was made to enforce
    uniqueness — on the real `UNIQUE(run_id, scan_id)` constraint) before the fix.
  - **Non-finite `params` (IMPORTANT):** `test_rejects_params_with_nan`, `test_rejects_params_with_infinity`
    (unit) — assert `422`, assert `app_client`/enumeration never called (validation happens before any
    Supabase call, matching the existing pattern for every other field).
  - **Oversized `params` (IMPORTANT):** `test_rejects_params_exceeding_max_bytes` (unit) — a `params`
    dict serializing past `MAX_PARAMS_BYTES`; assert `422`.
  - **`scan_ids` length cap (IMPORTANT):** `test_rejects_scan_ids_exceeding_max_length` (unit) — a
    `scan_ids` list longer than `MAX_SCAN_IDS`; assert `422` without any enumeration query.
  - **Wave/experiment ordering (IMPORTANT):** `test_enumerate_wave_orders_by_scan_id`,
    `test_enumerate_experiment_orders_by_scan_id` (unit) — seed scan rows in a scrambled order; assert
    the fake client recorded an `.order("scan_id")` call and the resulting `batch_index` assignment
    matches ascending `scan_id` order, not seed order. (Required extending the hand-rolled fake
    `_Query` with an `.order()` method that actually sorts, matching the real supabase-py contract —
    the fake previously ignored ordering entirely.)
  - **Column-scoped `INSERT` grants (IMPORTANT):** `test_bloom_workflows_can_insert_only_populated_columns`
    (integration) — `SET LOCAL ROLE bloom_workflows`; insert with only the columns `pipeline.py`
    populates (succeeds); insert additionally touching `argo_workflow_name` (must raise
    `InsufficientPrivilege`).
- [x] 7.2 Implement the fixes in `pipeline.py`:
  - `_validate_request` now computes `compute_param_hash` once up front (catching
    `NonCanonicalizableError`/`TypeError`/`RecursionError` → `422`) and checks `MAX_PARAMS_BYTES`
    before attempting the hash; the resulting hash is threaded through to `_dedup_preview` (which no
    longer recomputes it) rather than discarded.
  - `scan_ids` deduplicated order-preserving (`list(dict.fromkeys(scan_ids))`) and length-capped at
    `MAX_SCAN_IDS` in `_validate_request`.
  - `.order("scan_id")` added to `_enumerate`'s wave/experiment queries.
- [x] 7.3 Fix `supabase/migrations/20260730120000_create_cyl_pipeline_runs.sql` (edited in place — not
      yet applied to any shared environment, so this is safe per the forward-only-migration
      convention): explicit `REVOKE INSERT` before the new column-scoped `GRANT INSERT (...)` on both
      new tables (idempotent re-apply safety), matching `cyl_scan_videos`' precedent. Updated the
      companion rollback to match.
- [x] 7.4 Re-run the full suite: all pre-existing tests still green, all new tests green. `openspec
      validate --strict` passes. `ruff`/`black` clean.
