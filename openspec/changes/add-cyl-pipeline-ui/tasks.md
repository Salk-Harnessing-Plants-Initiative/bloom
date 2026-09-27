**Landing plan: six PRs to `staging`.**

| PR | Title | Sections | Depends on |
|---|---|---|---|
| 1 | "Add the run → experiment view the pipeline UI reads" | §0–1 | – |
| 2 | "Open the traits page at a given wave and day" | §2 | – |
| 3 | "Watch pipeline runs live" | §3–8 | PR 1 (view types), PR 2 (links) |
| 4 | "Add the pipeline trigger proxy" | §9 | – (may go in parallel with PR 3) |
| 5 | "Batch the pipeline trigger's id filters so large experiments run" (bloom#901) | §9b | – (may go in parallel) |
| 6 | "Start pipeline runs from the scan, experiment and accession pages" | §10–12 | PR 3, PR 4, PR 5 |

- `lint_migration_isolation.py` (`pr-checks.yml:637-652`, in warning mode and becoming blocking) keeps schema changes in their own PR. So PR 1 carries both the migration and this proposal; it is not a proposal-only PR.
- Each PR is branched from `origin/staging` after the PRs it depends on have merged, never stacked. Before opening PRs 2–6, check that `git diff --name-only origin/staging...HEAD` contains no `supabase/` or `database.types.ts` changes.

**Rules for every task:**
- **Red first.** Tests under **Test first** are written and seen failing first. "Failing" means an assertion failure against a stub that exports the signature and throws `not implemented`; a skip or a module-not-found error doesn't count.
- **Characterization tests.** A test that can't fail first (characterization or regression) is marked `(characterization)` in the task and in the PR.
- **Commits.** Red evidence goes in the commit body or the PR description. A test and its implementation may share a commit, so every pushed head is green.
- **Web tests** are colocated Vitest files. Component tests:
  - declare `// @vitest-environment jsdom`;
  - under fake timers, use `act()` plus `vi.advanceTimersByTimeAsync`, and never `waitFor`;
  - call `vi.useRealTimers()` before `cleanup()`.
- **Helpers** never live in `page.tsx`, `layout.tsx` or `route.ts`; they go in sibling modules.
- **Guarded directories.** No file under them, tests included, writes a guard-forbidden string literally.

## PR 1: schema, proposal, docs

## 0. Preconditions

- [ ] 0.1 Run `npm ci`, then read `node_modules/next/dist/docs/` (route handlers, async `params`/`searchParams`, `notFound`, client components). Record any divergence from the video route's patterns here.
- [x] 0.2 On the dev stack (`make dev-up && make migrate-local`), record in the PR: **(done 2026-09-25 on a rebuilt dev DB: both run tables published; bloom_user reads all four relations.)**
  - `pg_publication_tables` for `supabase_realtime` includes both run tables;
  - under `SET ROLE bloom_user`, rows are readable from `cyl_pipeline_runs`, `cyl_pipeline_run_scans`, `cyl_scan_latest_source` and `cyl_scans_extended`.
- [ ] 0.3 Capture real Realtime payloads with a throwaway Node 22 script in the scratchpad, using the repo-root `@supabase/supabase-js`:
  1. `createClient(<dev supabase url>, <anon key>)`, then `auth.signInWithPassword` as a dev `bloom_user`.
  2. `channel(...).on('postgres_changes', {event: '*', schema: 'public', table}, p => console.log(JSON.stringify(p)))` for each run table.
  3. Drive the rows by SQL. For the TOAST case, set `error_message` to more than 4 KB of incompressible text (`SELECT string_agg(md5(random()::text), '') FROM generate_series(1,200)`) and confirm it is stored out of line (`pg_column_size`). Then UPDATE only `done_count`, and capture that event. Do this for both tables.
  4. Commit the captures in PR 3 as `web/lib/cyl-pipeline/__fixtures__/realtime-*.json`, and record the timestamp format.

## 1. `cyl_pipeline_run_experiments` view + index

- [x] 1.1 **Test first.** Write `tests/integration/test_cyl_pipeline_run_experiments.py`.
  - **Setup:**
    - Seed with the helpers in `test_cyl_read_model_views.py:28-69`, inserting the run and scan rows as the connection superuser.
    - Each test applies `_sql_body(MIGRATION)` (`test_cyl_pipeline_dispatch.py:53`) inside its rolled-back transaction. This works over CI's already-applied schema because the file is idempotent.
    - Assert `MIGRATION is not None`; never skip.
  - **Cases:**
    - (a) A multi-scan run in one experiment gives one row, whose `created_at` equals the run's.
    - (b) A run spanning two experiments gives two rows.
    - (c) A null `plant_id` gives no row; so does a null `wave_id`.
    - (d) A run with no scan rows gives no row.
    - (e) `SET LOCAL ROLE bloom_user` reads the seeded rows.
    - (f) A soft-deleted experiment is hidden from `bloom_user` and visible to `bloom_admin`.
    - (g) Invoker: create `pipeline_probe_<uuid8>` NOLOGIN (following `test_schema_usage_grants.py:85`) with `USAGE` on `public` and `SELECT` on the view only. Inside a `with pg_conn.transaction():` savepoint, querying the view as it raises `InsufficientPrivilege` naming a base table.
    - (h) `reloptions && ARRAY['security_invoker=on','security_invoker=true']`.
    - (i) The privilege matrix over the spec's role list, with lowercase `'public'`.
    - (j) The index exists on `(scan_id)`.
    - (k) The migration text contains `SET LOCAL lock_timeout` and `NOTIFY pgrst, 'reload schema'`, and the rollback text contains `SET LOCAL lock_timeout`.
    - (l) Rollback, then re-apply.
    - (m) `_sql_body()` of both files contains no `\bBEGIN\b` or `\bCOMMIT\b` token.
- [x] 1.2 Write `supabase/migrations/20260924120000_add_cyl_pipeline_run_experiments.sql` per the `/database-migration` skill and design D5.
  - Header comment: why `lock_timeout` (there is no repo precedent), why REVOKE comes first, and why NOTIFY.
  - Layout:
    - `BEGIN;` alone on a line;
    - `SET LOCAL lock_timeout = '5s';`
    - `CREATE INDEX IF NOT EXISTS …;`
    - `CREATE OR REPLACE VIEW … WITH (security_invoker = on) …;`
    - `REVOKE ALL …;`
    - `GRANT SELECT …;`
    - `COMMIT;` alone on a line;
    - `NOTIFY pgrst, 'reload schema';` on the next line.
  - The file must be fully idempotent.
  - Bump the timestamp if another migration lands first.
- [x] 1.3 Write `supabase/rollbacks/20260924120000_add_cyl_pipeline_run_experiments_rollback.sql` in the same layout: `BEGIN;` alone; `SET LOCAL lock_timeout = '5s';`; `DROP VIEW IF EXISTS …;`; `DROP INDEX IF EXISTS …;`; `COMMIT;` alone; `NOTIFY pgrst, 'reload schema';`.
- [x] 1.4 Run: **(done: 23/23 view tests + sibling suites green; lint passed; PostgREST 200 as bloom_user; EXPLAIN 40.5 ms / 69.9 ms on 150k rows. Also fixed `test_cyl_pipeline_dispatch.py::test_rollback_removes_everything` to apply this rollback first.)**
  - `make migrate-local`;
  - `uv run --extra test pytest tests/integration/test_cyl_pipeline_run_experiments.py -v`;
  - `scripts/lint_migrations.sh origin/staging`;
  - `uv run ruff check tests/integration/`.

  Also:
  - Through PostgREST, `GET /rest/v1/cyl_pipeline_run_experiments?limit=1` with a `bloom_user` JWT returns 200, not PGRST205.
  - On about 100k seeded run-scan rows, as `bloom_user`, record `EXPLAIN (ANALYZE, BUFFERS)` for the panel query (`experiment_id = X order by created_at desc limit 10`) and for the list's names query (`run_id in (50 ids)`).
- [x] 1.5 **(characterization)** Write `tests/integration/test_cyl_scan_latest_source_precheck.py`, in SQL on `pg_conn`, rolled back.
  - Seed 40 scans. Insert `cyl_scan_traits` rows so that 38 scans have a real `source_id`, 1 has only NULL `source_id`s, and 1 has no traits. The rows go through the maintaining trigger.
  - Under `SET LOCAL ROLE bloom_user`, a single `select scan_id, max_source_id … where scan_id = any(…)` gives K = 38 (non-null), L = 1 (null) and 1 absent.
- [x] 1.6 Types:
  1. Back up the four generated `database.types.ts` copies: `web/lib/`, `packages/bloom-js/src/types/`, `packages/bloom-fs/src/types/`, `packages/bloom-nextjs-auth/src/lib/`.
  2. Run `make gen-types`, which overwrites them.
  3. Restore the backups and hand-merge **only** the view entry into each copy.
  4. Confirm the diff contains only that entry.

  Leave `web/types/database.types.ts` untouched.
- [x] 1.7 Docs: **(done: `erd-snapshot CHANGED=` ignores view-only changes, so the PR uses `TABLES=`.)**
  - Run `make erd`, confirm the view appears, and commit `_WIKI/SUPABASE/erd.md`.
  - Check whether `make erd-snapshot CHANGED=origin/staging` picks up a view-only change, and note the result in the PR.
  - `_WIKI/SUPABASE/README.md`: replace "future live-status panel" and document the view and the index.
  - `services/workflows/README.md` trigger example:
    - use `"params": {}` and show `"reused_count": 0`;
    - one sentence saying params are recorded but not yet applied (#897);
    - one sentence naming `/api/cyl/pipeline` as the web caller.
- [ ] 1.8 PR body:
  - **Schema changes** section: ER snapshot, plus a constraints-table row for the index with "How it is added" = `IF NOT EXISTS (index)`.
  - Run `make pr-body-check BODY=<file>` and `openspec validate add-cyl-pipeline-ui --strict`.
  - Use "Refs #15".

## PR 2: traits page deep link

## 2. `wave`/`age` search params on the traits page

- [ ] 2.1 **Test first.** Write `web/app/app/traits/[speciesId]/[experimentId]/initial-selection.test.ts` for a pure `resolveSelection(options, requested, current)`:
  - a requested pair that is available is selected;
  - an unavailable requested pair falls back to the defaults (last wave, max age for that wave) and returns a note;
  - `current` is kept on a trait change when still available, otherwise it falls back with a note;
  - an age that exists but not for the chosen wave falls back.
- [ ] 2.2 **Test first.** Write `parse-search-params.test.ts` for `parseWaveAge(searchParams)`:
  - `'1'` and `'14'` give numbers;
  - `'0x1'`, `''`, `'07'`, `'1.5'` and arrays (`?wave=1&wave=2`) are ignored.
- [ ] 2.3 **Test first.** Write `TraitExplorer.test.tsx` (jsdom, real timers):
  - mock `@/lib/supabase/client` `rpc` to resolve rows, and `@/components/scan-trait-boxplot` to a stub;
  - with `initialWave`/`initialAge` set, the selects show them after `await act(...)`;
  - after changing trait, the selection is kept if still valid;
  - an unavailable pair shows the fallback note.

  Also write a traits `page.test.tsx` (expression-page style, mocking `./TraitExplorer` to capture its props): `searchParams` `{wave: '1', age: '14'}` become numeric props.
- [ ] 2.4 Implement:
  - `page.tsx` awaits `searchParams` (typed `Promise<Record<string, string | string[] | undefined>>`) and passes the parsed values;
  - `TraitExplorer` calls `resolveSelection` **inside** its data effect, after the options are computed. A ref marks the URL params as consumed after the first load;
  - key `TraitExplorer` on `${wave}-${age}`.
- [ ] 2.5 Verify with `cd web && npx tsc --noEmit && npm run test:unit && npm run build` and `npx prettier --check`. Then `/pre-merge` and `/review-pr`.

## PR 3: live read-only views

## 3. Pure logic in `web/lib/cyl-pipeline/`

- [ ] 3.0 Create `__fixtures__/supabase-mock.ts`, modelled on `components/expression-differential-analysis.test.tsx:253-295`. It provides:
  - a thenable query builder that records the table, `select`, `eq`, `in`, `or`, `not`, `is`, `lt`, `gte`, `order`, `limit`, `range` and `maybeSingle` calls, and answers via a per-test function;
  - a channel mock whose `.on()` returns itself and whose `.subscribe(cb)` captures `cb`;
  - a `removeChannel` spy;
  - a deferred helper (not `Promise.withResolvers`, because CI runs Node 20);
  - an exported `clientModule` for `vi.mock("@/lib/supabase/client", async () => (await import(".../supabase-mock")).clientModule)`.

  Add 0.3's captures.
- [ ] 3.1 **Test first.** Write `run-display.test.ts`:
  - every scenario in the display requirement, including the clamp and a `failed` run keeping its `error_message`;
  - each stage's tooltip;
  - the raw status is always present;
  - no output mentions `reused_count`;
  - the scan status label map over all five CHECK values plus an unknown value.
- [ ] 3.2 Implement `run-display.ts`, the status unions and the scan label map.
- [ ] 3.3 **Test first.** Write `timestamps.test.ts`:
  - `…10.123456` is after `…10.123400`;
  - no fraction is before `.5`;
  - `+00:00` equals `Z`;
  - an unparsable value sorts as newest;
  - use the captured formats.
- [ ] 3.4 Implement `timestamps.ts`.
- [ ] 3.5 **Test first.** Write `realtime-reducer.test.ts`, using the captures:
  - buffered replay;
  - merge keeps absent fields;
  - counts never decrease;
  - the unknown-run cursor rule (including zero rows loaded);
  - the cursor moves only on snapshot or "load older";
  - "load older" de-duplicates;
  - ties are broken by `id`;
  - other-run events are ignored;
  - `countsFromScanRows`.
- [ ] 3.6 Implement `realtime-reducer.ts`.
- [ ] 3.7 **Test first.** Write `resync-scheduler.test.ts` (fake timers):
  - the first `SUBSCRIBED` refetches at t = 0;
  - `CLOSED` then `SUBSCRIBED` at t = 500 ms gives exactly one more refetch, at t = 2000 ms;
  - a burst of four transitions in the window still gives exactly one.
- [ ] 3.8 Implement `resync-scheduler.ts`.
- [ ] 3.9 **Test first.** Write `run-links.test.ts`:
  - one scan gives one "Scan images" link and one traits link;
  - the joint `(wave, age)` pair example from the spec;
  - the tie-break;
  - a null age omits `age`;
  - at most 3 pairs per experiment;
  - two experiments;
  - missing metadata.
- [ ] 3.10 Implement `run-links.ts`.
- [ ] 3.11 **Test first.** Write `failure-hints.test.ts`:
  - `likelyCause` rules via a shared `stageInProblems()`;
  - `isNoOpCandidate(row, hasResults)` is true only when `error_message === BACKSTOP_MESSAGE` and results exist.

  Export `BACKSTOP_MESSAGE` from a module that has a test pinning it to the poller's text. Read `services/workflows/status_poller.py` in the test and assert the string is present.
- [ ] 3.12 Implement `stage-in.ts` and `failure-hints.ts`.

## 4. Read queries

- [ ] 4.1 **Test first.** Write `queries.test.ts`, asserting calls **per table**:
  - `fetchRuns({cursor?, mineOnly?})`: `created_at`/`id` desc; the keyset `.or()` built from the raw string with values double-quoted; `eq('requested_by')` when `mineOnly`; limit 50.
  - `fetchRun(id)` uses `maybeSingle`.
  - `fetchRunScans(runId)`: pages of 1000 ordered by `scan_id` until an empty page.
  - `fetchScanMeta(ids)`: chunks of ≤ 200, including `qr_code`, `wave_number`, `plant_age_days`, species and accession ids.
  - `fetchLatestSources(ids)`: chunks of ≤ 200, returning `scan_id` and `max_source_id`.
  - `fetchRunExperiments(runIds)` and `fetchExperimentRunIds(expId)`: the view, `created_at` desc, limit 10.
  - `isRunInExperiment`.
  - Every error surfaces typed.
- [ ] 4.2 Implement `queries.ts`.

## 5. `LiveIndicator` and nav

- [ ] 5.1 **Test first.** Write `web/components/recent-phenotypes-by-cyl-scanner/LiveIndicator.test.tsx`:
  - **(characterization)** with no prop it renders as today, and the plate-scanner consumer is unchanged;
  - `connecting` and `offline` render distinct `aria-live` text;
  - `offline` renders `onRefresh`.
- [ ] 5.2 Implement the optional props.
- [ ] 5.3 **Test first.** Write `web/components/nav-sections.test.ts`:
  - a "Pipeline runs" entry at `/app/cyl-pipeline-runs`;
  - every other entry equals today's list, with the array inlined as the expected value.

  `/app/pipelines` has no nav entry and stays absent.
- [ ] 5.4 Move `navSections` into `web/components/nav-sections.ts`, import it in `layout.tsx`, and add the entry.

## 6. Runs list `/app/cyl-pipeline-runs`

- [ ] 6.1 **Test first.** Write `RunsListLive.test.tsx`:
  - INSERT and UPDATE subscriptions on a per-instance topic;
  - the resync scheduler wired to channel status (first `SUBSCRIBED`, the trailing case);
  - a deferred snapshot plus an UPDATE during the fetch shows the newer value;
  - an UPDATE issues no query;
  - an INSERT goes on top with no names and no query, and its names are fetched once on its first non-`queued` event;
  - an older unloaded run is ignored, and "load older" returns it in order;
  - the "Only mine" filter;
  - the indicator states and refresh;
  - under StrictMode, two topics are created, the first is removed, and one remains;
  - unmount removes the channel;
  - with the baseline taken after the initial resync, `advanceTimersByTimeAsync(300_000)` adds no `from()` calls per table, and there is no `/workflows/runs` fetch;
  - requester, target and experiment links render, and a run whose only experiment is soft-deleted renders with no experiment link;
  - the failed-count link;
  - the empty and error states.
- [ ] 6.2 **Test first.** Write `page.test.tsx` (expression style): the snapshot renders, and a failure shows the error.
- [ ] 6.3 Implement `page.tsx`, `RunsListLive.tsx` and `RunRow.tsx`.

## 7. Drill-down `/app/cyl-pipeline-runs/[runId]`

- [ ] 7.1 **Test first.** Write `[runId]/page.test.tsx`:
  - each invalid id in the spec scenario calls `notFound()` (mocked to throw) via `parseId`;
  - an unknown id (`maybeSingle` → null) calls `notFound()`;
  - a lookup error renders an error;
  - a valid id renders.
- [ ] 7.2 **Test first.** Write `RunDetailLive.test.tsx`, mocking `./RunScansTable` with a stub that shows the row count and the links:
  - channel filters;
  - a scan UPDATE to `written` changes the label and the header count;
  - other-run events are ignored;
  - elapsed time and "last scan update" (`vi.setSystemTime`);
  - the header params line for `{}` and for non-empty params;
  - 5000 rows take 6 `cyl_pipeline_run_scans` calls;
  - the empty state;
  - a failed row gets its likely cause, and the #900 note only for backstop text with results. A row that turns failed live triggers exactly one metadata/latest-source lookup;
  - "current in trait views";
  - the timing note;
  - the experiment links;
  - the sync cases from 6.1 for both channels.
- [ ] 7.3 **Test first.** Write `RunScansTable.test.tsx` (real timers, `disableVirtualization`):
  - 5000 rows show `/1–100 of 5000/`, with page size 100;
  - the status filter;
  - every column in the spec's list;
  - the status labels.
- [ ] 7.4 Implement `[runId]/page.tsx`, `RunDetailLive.tsx` and `RunScansTable.tsx`.

## 8. Experiment panel, guard, wrap-up

- [ ] 8.1 **Test first.** Write `web/components/cyl-pipeline/ExperimentRunsPanel.test.tsx`:
  - the 10 most recent, with display states;
  - held-only events;
  - it stays at 10 on insert;
  - the race (INSERT with empty membership isn't cached; a `submitted` UPDATE re-queries and lists the run);
  - a foreign `running` run gives one query, then it is cached;
  - "Runs unavailable";
  - the links;
  - the 6.1 sync cases.
- [ ] 8.2 **Test first.** Write an experiment page render test asserting the panel receives `experimentId`.
- [ ] 8.3 Implement `ExperimentRunsPanel.tsx` and mount it.
- [ ] 8.4 Write `web/lib/cyl-pipeline/no-provenance-joins.test.ts`, using plain `node:fs`. The resolver takes an injected `{webRoot, readFile, exists}`.
  - **Self-checks (red first)**, run in memory:
    - a fixture graph `a → @/lib/b → ./c`, with `c` containing `get_scan_traits`, is detected;
    - each forbidden pattern matches its fixture;
    - `cyl_pipeline_run_scans_run_id_fkey` does not match.
  - **(characterization)** Scan of the real tree:
    - the three directories are each non-empty;
    - parse import specifiers with `/\bfrom\s*["']([^"']+)["']|\bimport\s*\(\s*["']([^"']+)["']\s*\)|\bimport\s+["']([^"']+)["']/g`;
    - resolve `@/` and relative paths (`.ts`, `.tsx`, `/index.*`), following `web/lib/` transitively;
    - exclude exactly the two generated type files and the guard itself;
    - assert that none of the files contains any spec-forbidden pattern.
- [ ] 8.5 Add a "Pipeline runs" section to `web/README.md`.
- [ ] 8.6 Live check on the dev stack.
  - **Setup:**
    1. Confirm `WORKFLOWS_K8S_TOKEN` is empty in `.env.dev`.
    2. Run `docker compose -f docker-compose.dev.yml stop cyl-pipeline-worker cyl-status-poller`.
    3. Confirm that `WORKFLOWS_SUPABASE_EMAIL`/`_PASSWORD` are set.
    4. Get a `bloom_user` token.
  - **Steps:**
    1. Send one `curl http://localhost:5100/pipeline` with `{"target_level":"scan","target_id":<id>,"params":{}}`.
    2. Confirm the run appears live.
    3. Drive progress by SQL: set the scan rows to `written` with `updated_at = now()`, then set the run's `done_count`.
    4. Confirm the drill-down, the header and the list update live.
    5. Seed more than 50 runs and confirm "load older" neither skips nor duplicates.
  - **Cleanup:**
    1. `SELECT pgmq.purge_queue('cyl_pipeline_dispatch')`.
    2. Restart both services.
- [ ] 8.7 Verify:
  - `openspec validate add-cyl-pipeline-ui --strict`;
  - `cd web && npx tsc --noEmit && npm run test:unit && npm run build`;
  - `npx prettier --check <changed files>`;
  - `python3 scripts/lint_migration_isolation.py origin/staging` reports no migration change.

  Then run `/pre-merge` and `/review-pr`. PR body: "Refs #15".
- [ ] 8.8 **After the staging deploy of PR 3:** as a second signed-in member, decode the token (`role: bloom_user`) and confirm live updates arrive when a run row changes. That proves Realtime-as-`bloom_user` before any UI trigger ships. Record the result on the PR.

## PR 4: trigger proxy

## 9. Proxy

- [ ] 9.1 **Test first.** Write `trigger-request.test.ts`:
  - every validation rule in the spec, including `target_id: null` with `scan_ids`, and both `MAX_TRIGGER_SCAN_IDS` bounds (5000 and 5001);
  - the rebuilt body carries `params: {}`.
- [ ] 9.2 Implement `trigger-request.ts`, exporting `MAX_TRIGGER_SCAN_IDS = 5000`, with a comment tying it to `services/workflows/pipeline.py`'s `MAX_SCAN_IDS`. Add a test that reads `pipeline.py` and asserts the two values are equal.
- [ ] 9.3 **Test first.** Write `web/app/api/cyl/pipeline/route.test.ts`, modelled on the video route test. One test per scenario in the three proxy requirements, plus:
  - **Ordering:** check each of 415, 403 and 401 with `new Request(url, {method: 'POST', headers, body: new ReadableStream({pull: pullSpy}, {highWaterMark: 0}), duplex: 'half'} as RequestInit)`. Assert `pullSpy` was not called, `req.bodyUsed === false`, and `fetch` was not called.
  - **Origin:**
    - `localhost:3000` against `Host: localhost:3001` gives 403;
    - the comparison is case-insensitive;
    - the first `x-forwarded-host` value wins;
    - the Request URL host differs from `Host`.
  - **Defaults and limits:**
    - `WORKFLOWS_URL` unset gives `http://workflows:5100/pipeline`;
    - a 257 KB streamed body with no `Content-Length` gives 413, and a `Content-Length` over the cap gives 413 without reading the body.
  - **Upstream failures:**
    - a `DOMException("t","TimeoutError")` rejection gives 504, with `init.signal instanceof AbortSignal`;
    - `TypeError("fetch failed", {cause: {code: "ECONNREFUSED"}})` gives 502;
    - a 404 string detail is truncated in the response;
    - a non-integer `Retry-After` is dropped;
    - a malformed 2xx gives 502.
  - **Logging:** logs contain no `Authorization`.
- [ ] 9.4 Implement `route.ts`, with a comment that the Origin check depends on Caddy having no `trusted_proxies`. Update the `caddy/Caddyfile:123` comment, and confirm `tests/unit/test_caddy_cyl_video_route.py` passes.
- [ ] 9.5 Verify as in 8.7. The PR body notes that the route is reachable by same-origin signed-in POSTs once deployed, which adds nothing beyond `/workflows/pipeline`. Then `/pre-merge` and `/review-pr`.

## PR 5: trigger batching (bloom#901)

## 9b. Batch the trigger's id filters; skip the preview for `{}`

- [ ] 9b.1 **Test first.** Write `services/workflows/tests/test_postgrest_batches.py` for `id_batches(ids, budget=4000)`:
  - an empty list gives no batches;
  - the rendered length of each batch, counting separators, is ≤ budget;
  - order is preserved and ids are neither lost nor duplicated;
  - a single id longer than the budget gets its own batch;
  - 19-digit ids batch correctly;
  - `ID_FILTER_BUDGET_CHARS` equals the value in `bloomcli/src/bloomctl/_postgrest.py`. The test reads that file, so the two can't drift.
- [ ] 9b.2 Implement `services/workflows/postgrest_batches.py`, ported from bloomctl's `id_batches`, with a header citing bloom#674's measurement.
- [ ] 9b.3 **Test first.** Extend `services/workflows/tests/test_pipeline.py`. Its `_FakeClient` must record each `.in_()` id list, so it may need extending.
  - A 3000-id `scan_ids` request issues more than one `cyl_scans_extended` filter call, each within budget, and proceeds as if all were found.
  - A missing id in the last batch still gives 404 naming it, with no rows written.
  - With non-empty params and 2500 enumerated scans, every `cyl_scan_traits`/`cyl_trait_sources` call is within budget, and `reused_count` matches the unbatched expectation.
  - A request with `params: {}` issues **no** `cyl_scan_traits` or `cyl_trait_sources` call, and `reused_count = 0`, even when the scans have sources.
  - **Regression:** update `test_dedup_preview_issues_one_batched_query_not_a_per_scan_loop` to non-empty params (`{"age": 14}`, a matching `_hash_of`). Its "3 and 30 scans give the same query count" assertion still holds, since both fit in one batch.
  - Every other existing dedup and enumeration test passes unchanged.
- [ ] 9b.4 Implement it in `services/workflows/pipeline.py`: batch the three filters, merge the results, and short-circuit `_dedup_preview` when `params == {}`.
- [ ] 9b.5 Update the trigger section of `services/workflows/README.md`: id filters are batched, and the preview is skipped for `{}`.
- [ ] 9b.6 Verify:
  - `cd services/workflows && uv run --frozen --extra test pytest`;
  - `uv run --extra test pytest tests/integration/test_cyl_pipeline_dispatch.py`;
  - `uv run ruff check` and `uv run black --check` on the changed files;
  - `openspec validate add-cyl-pipeline-ui --strict`.

  Then:
  - on the dev stack, with 8.6's setup (worker and poller stopped, K8s token empty), `curl http://localhost:5100/pipeline` an experiment-level target of at least 2,000 seeded scans. It must return 200 with the right `scan_count`, and the run and scan rows must be written;
  - purge the queue and restart the services;
  - record the result, then `/pre-merge` and `/review-pr`. The PR body says "Refs #901"; it doesn't close it, because the non-empty-params row-volume half stays open.

## PR 6: trigger UI

## 10. Dialog logic and queries

- [ ] 10.1 **Test first.** Write `params-summary.test.ts`:
  - the spec scenario, exactly;
  - flagged scans are excluded from the groups;
  - it uses `stageInProblems()`;
  - the output's key set equals exactly `['groups', 'stageInCount']`.
- [ ] 10.2 Implement `params-summary.ts`.
- [ ] 10.3 **Test first.** Extend `queries.test.ts`:
  - `fetchTargetScans(target)` uses the trigger's filters, pages of 1000 ordered by `scan_id`, and `scan_ids` chunks of ≤ 200; 2,500 scans give N = 2500.
  - `fetchConcurrentRuns(experimentIds)`:
    1. read runs with `status=not.in.(complete,failed)` and `created_at` within 7 days, limit 20;
    2. filter to incomplete counts in the client;
    3. check the view for `run_id=in.(…)` (chunked) and `experiment_id=in.(…)`;
    4. return at most 10, plus a count of the rest.
- [ ] 10.4 Implement them.
- [ ] 10.5 **Test first.** Write `accession-scan-ids.test.ts`: it returns every `plant.cyl_scans[].id`, de-duplicated and without mutating its input. Two same-day scans plus one with `cyl_images: []` give 3 ids.
- [ ] 10.6 Implement `web/components/cyl-pipeline/accession-scan-ids.ts`.

## 11. Dialog, selection, entry points

- [ ] 11.1a **Test first.** Write `RunPipelineDialog.test.tsx`, part 1 (content):
  - confirm is disabled until the queries settle;
  - headline;
  - blockers: N = 0, a missing selection id, N > `MAX_TRIGGER_SCAN_IDS` for a `scan_ids` target. An 8,000-scan experiment has no size blocker;
  - stage-in warning;
  - concurrent runs, with their display state and link, and "and M more";
  - pre-check line and details, in the exact copy, with the L clause omitted when L = 0;
  - the all-results notice replaces the pre-check line;
  - params groups collapsed beyond 3, with the caption;
  - the ≥ 500 acknowledgement;
  - banned phrases are absent.
- [ ] 11.1b **Test first.** Part 2 (submit):
  - two synchronous clicks give one `POST`, to exactly `/api/cyl/pipeline`;
  - the success state, with the timing and reload note and the mismatch note;
  - 429, 502/504, 401, and 404/422 behaviour;
  - a query failure.
- [ ] 11.2 Implement `RunPipelineDialog.tsx` and `RunPipelineButton.tsx`.
- [ ] 11.3 **Test first.** Write `ScanSelection.test.tsx`:
  - counts by scan id;
  - `closest('a') === null`, and clicking doesn't navigate;
  - "Select all shown";
  - the bar is hidden at 0 and carries the per-page note;
  - "Run selected (3)" submits exactly those ids through the mocked `fetch`;
  - it is disabled over the limit.
- [ ] 11.4 Implement the selection components.
- [ ] 11.5 **Test first.** Page render tests:
  - the scan page shows the button only when the scan exists;
  - the experiment page shows "Run experiment", plus "Run wave" only when there is more than one wave;
  - the accession page shows "Run this accession" with exactly `accessionScanIds(plants)` (computed before the in-place sort), disabled over the limit, plus the checkboxes.
- [ ] 11.6 Wire the three pages.
- [ ] 11.7 **Test first.** Extend `RunDetailLive.test.tsx`:
  - "Re-run failed" is gated, submits exactly the failed ids, and warns when a row has the #900 note;
  - "Re-run scans without a result (M)" appears only when U > 0 on `complete`/`failed` runs;
  - the settled case offers only "Re-run failed";
  - both are disabled over the limit.

  Extend `ExperimentRunsPanel.test.tsx` with the optimistic insert.
- [ ] 11.8 Implement.

## 12. Live verification on staging (before PR 6 merges)

**Environment:** the web app runs locally against staging, with `WORKFLOWS_URL=https://staging.bloom.salk.edu:8443/workflows` and staging `NEXT_PUBLIC_SUPABASE_URL`.

**Before starting:**
- Confirm the live `insert_cyl_result_envelope` contract pin matches the deployed traits image's `contract_version` (bloom#895).
- Create controlled scans with `bloomctl cyl create-test-scan --good`, and `--poison` for a deterministic stage-in failure.
- For a null-age scan, have a `bloom_admin` set one test scan's `plant_age_days` to NULL, and revert it afterwards.
- Write down a manual cancel procedure (delete the Argo workflows, purge pgmq) before any multi-scan run.

- [ ] 12.1 Run one scan from the scan page, and record the run id. Confirm:
  - the list and drill-down update without a reload and reach a finished state (intermediate states may be skipped within one sweep);
  - there is exactly 1 scan row, with counts 1/1;
  - there are no timer-driven reads and no `/workflows/runs` calls.
- [ ] 12.2 Trigger a `scan_ids` run of at least 3 scans via "Run this accession" or the grid. Confirm:
  - it appears on the experiment panel;
  - it appears live for a second member, whose decoded `role` is `bloom_user`;
  - the same for `bloom_writer`/`bloom_admin` accounts, if they exist.
- [ ] 12.3 Trigger a run including the `--poison` scan and the null-age scan (via the scan page or "Run this accession"; null-age scans don't render in the grid). Confirm:
  - the dialog warned about the null-age scan;
  - the actual per-scan `error_message` values, recorded;
  - "Re-run failed" submits exactly the failed ids.

  Also confirm a staging run with `status='complete' and failed_count>0` renders by rule 3 or rule 5, according to its counts.
- [ ] 12.3b Run a scan whose only source came from `bloomctl cyl ingest-result`. Record its status. If it is `failed` with the backstop text, confirm the #900 note, and add the evidence to bloom#900 (confirm with the user before posting).
- [ ] 12.4 Turn the network adapter off for 30 s during an active run, then back on. Confirm:
  - `CHANNEL_ERROR`/`CLOSED` then `SUBSCRIBED` in the console;
  - the indicator shows offline, then live;
  - the counts resync.
- [ ] 12.5 If an empty wave exists, confirm the dialog shows "No scans to run". Note that any zero-scan run triggered via `curl` leaves a permanent "No scans matched" row.
- [ ] 12.6 Observe whether a large reconciliation burst disconnects other Realtime widgets, and record it.
- [ ] 12.7 Leave a drill-down open past the JWT lifetime. Confirm it recovers or shows offline with refresh.
- [ ] 12.8 For the runs in 12.1–12.3, confirm `done_count`/`failed_count` equal the per-status tallies. Record this as evidence for `fix-cyl-pipeline-run-scan-status` 8.1–8.4, and tick those only in that change, only if they match.
- [ ] 12.9 Open a run's traits link and confirm it lands on the run's wave and day, with the run's scans visible.
- [ ] 12.10 Trigger one experiment-level run of the largest practical staging experiment (at least 1,500 scans if one exists). Use the manual cancel procedure afterwards if it isn't wanted to finish. Confirm:
  - it is accepted as **one** run;
  - `scan_count` is right;
  - the drill-down loads every row.

  Record the trigger latency, since it makes 25-scan enqueue RPCs sequentially.
- [ ] 12.11 Record run ids, screenshots and mismatches in the PR. Mismatches are fixed or filed, not waived. Then verify as in 8.7, and run `/pre-merge` and `/review-pr`.

## 13. After merge

- [ ] 13.1 Draft a bloom#15 comment: §10 v1 has shipped; phases 3–4, #865, #897, #898, #899 and #900 remain. Post only with explicit user go-ahead.
- [ ] 13.2 Draft updates to sleap-roots-pipeline's roadmap row (line 308) and to design §10's text (overrides, "N will run", blob links, requester names), coordinated with PR #88.
- [ ] 13.3 Once §12 has been repeated on the deployed staging build, run `/openspec:archive add-cyl-pipeline-ui`.
