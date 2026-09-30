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

- [x] 0.1 Run `npm ci`, then read `node_modules/next/dist/docs/` (route handlers, async `params`/`searchParams`, `notFound`, client components). Record any divergence from the video route's patterns here. **(done 2026-09-28 in PR 3's worktree: `npm ci` now installs Next 16.3.4, so the stale-16.2.0 note in design.md no longer applies. No divergence from the video route:**
  - **`params`/`searchParams` are promises, typed inline as `Promise<{…}>`, as the video route and every existing page already do. The docs' global `PageProps`/`RouteContext` helpers need `next typegen`, and nothing in `web/` uses them, so PR 3 doesn't either;**
  - **`notFound()` throws `NEXT_HTTP_ERROR_FALLBACK;404` and only renders the 404 when thrown in the render path (a component or an awaited function), never from an un-awaited promise. The drill-down page therefore calls it directly after its awaited lookup;**
  - **client-component props must be serializable, so pages pass snapshot rows, never the Supabase client or callbacks.)**
- [x] 0.2 On the dev stack (`make dev-up && make migrate-local`), record in the PR: **(done 2026-09-25 on a rebuilt dev DB: both run tables published; bloom_user reads all four relations.)**
  - `pg_publication_tables` for `supabase_realtime` includes both run tables;
  - under `SET ROLE bloom_user`, rows are readable from `cyl_pipeline_runs`, `cyl_pipeline_run_scans`, `cyl_scan_latest_source` and `cyl_scans_extended`.
- [x] 0.3 Capture real Realtime payloads with a throwaway Node 22 script in the scratchpad, using the repo-root `@supabase/supabase-js`:
  1. `createClient(<dev supabase url>, <anon key>)`, then `auth.signInWithPassword` as a dev `bloom_user`.
  2. `channel(...).on('postgres_changes', {event: '*', schema: 'public', table}, p => console.log(JSON.stringify(p)))` for each run table.
  3. Drive the rows by SQL. For the TOAST case, set `error_message` to more than 4 KB of incompressible text (`SELECT string_agg(md5(random()::text), '') FROM generate_series(1,200)`) and confirm it is stored out of line (`pg_column_size`). Then UPDATE only `done_count`, and capture that event. Do this for both tables.
  4. Commit the captures in PR 3 as `web/lib/cyl-pipeline/__fixtures__/realtime-*.json`, and record the timestamp format.

  **(done 2026-09-28: `realtime-runs.json` and `realtime-run-scans.json`, plus `postgrest-timestamps.json`. Findings:**
  - **Kong can't reach the dev tenant.** Every socket through `localhost:8000/realtime/v1/` fails with `TenantNotFound: realtime`. Realtime takes the tenant from the first label of the upstream Host, and Kong's upstream has been `realtime:4000` since 86decda6 (2026-06-11), while the only seeded tenant is `realtime-dev`. Staging runs the same `kong.yml`, image and compose settings, and its `_realtime.tenants` also holds only `realtime-dev`. This is outside PR 3's scope; the captures were therefore taken by connecting to `realtime:4000` from inside `bloom-web` with `Host: realtime-dev.supabase-realtime`. The payload shapes are Realtime's own, so bypassing Kong doesn't change them. That container runs Node 20, not 22, with the same `@supabase/realtime-js` 2.106.2 as the repo root.
  - **The token role is `bloom_user`** (decoded), and events arrive for it on both tables.
  - **TOAST.** `error_message` set to 6400 bytes was stored out of line (`pg_column_size` 6400, uncompressed; both toast relations non-empty). A later UPDATE of only `done_count` (runs), or only `attempts` (run scans), **omits the `error_message` key**, rather than sending `null`. Merging is required.
  - **Shape.** `eventType`, `new`, `old`, `commit_timestamp`, `errors: null`. UPDATE and DELETE `old` carry only `{id}` (replica identity DEFAULT). DELETE's `new` is `{}`.
  - **Timestamp format.** Realtime and PostgREST both render `timestamptz` as `YYYY-MM-DDTHH:MM:SS[.f{1,6}]+00:00`, with trailing zeros trimmed: `.123400` arrives as `.1234`, `.5` as `.5`, and a whole second has no fraction.
  - **Delivery.** A timed probe (50 inserts, 150 ms apart) delivered all 50, including ones committed before the `Subscribed to PostgreSQL` system message, so `SUBSCRIBED` is a sound resync trigger. In the first capture, one run INSERT (about 1 s after `SUBSCRIBED`) was never delivered, and it didn't reproduce in two later sessions. A capture through Kong on the freshly created temporary tenant (8.6 setup) lost one more the same way, which traced both to a cold start: each was a tenant's first subscription after Realtime started (see 8.6). A drop is not recovered by a resync, which only follows a reconnect; a reload or Refresh corrects it.)**

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
- [x] 1.8 PR body: **(done: #902, merged 2026-09-25 as 683f8e1e.)**
  - **Schema changes** section: ER snapshot, plus a constraints-table row for the index with "How it is added" = `IF NOT EXISTS (index)`.
  - Run `make pr-body-check BODY=<file>` and `openspec validate add-cyl-pipeline-ui --strict`.
  - Use "Refs #15".

## PR 2: traits page deep link

## 2. `wave`/`age` search params on the traits page

- [x] 2.1 **Test first.** Write `web/app/app/traits/[speciesId]/[experimentId]/initial-selection.test.ts` for a pure `resolveSelection(rows, preferred)`. The preference, whether requested or hand-picked, is one argument:
  - a preferred pair that is available is selected;
  - an unavailable preferred pair falls back to the default (last wave, oldest age within that wave) and returns a note;
  - an age that exists but not for the chosen wave falls back;
  - a trait with no rows returns a note when a pair was preferred.
- [x] 2.2 **Test first.** Write `parse-search-params.test.ts` for `parseWaveAge(searchParams)`:
  - `'1'` and `'14'` give numbers;
  - `'0x1'`, `''`, `'07'`, `'1.5'` and arrays (`?wave=1&wave=2`) are ignored.
- [x] 2.3 **Test first.** Write `TraitExplorer.test.tsx` (jsdom, real timers):
  - mock `@/lib/supabase/client` `rpc` to resolve rows, and `@/components/scan-trait-boxplot` to a stub;
  - with `initialWave`/`initialAge` set, the selects show them after `await act(...)`;
  - after changing trait, the selection is kept if still valid;
  - an unavailable pair shows the fallback note.

  Also write a traits `page.test.tsx` (expression-page style, mocking `./TraitExplorer` to capture its props): `searchParams` `{wave: '1', age: '14'}` become numeric props.
- [x] 2.4 Implement: **(done. Defaults stay the page's existing ones, last wave and oldest age overall, so no-parameter visits are unchanged; the red run was 25/25 helper tests and 6 component/page tests failing on assertions against stubs.)**
  - `page.tsx` awaits `searchParams` (typed `Promise<Record<string, string | string[] | undefined>>`) and passes the parsed values;
  - `TraitExplorer` calls `resolveSelection` **inside** its data effect, after the options are computed. A ref marks the URL params as consumed after the first load;
  - key `TraitExplorer` on `${wave}-${age}`.
- [x] 2.5 Verify with `cd web && npx tsc --noEmit && npm run test:unit && npm run build` and `npx prettier --check`. Then `/pre-merge` and `/review-pr`.

  **(PR 2, #912, merged 2026-09-28 as `3d67e468`; ticked in PR 6. Its description records `tsc --noEmit` and `npm run build` passing, `npm run test:unit` 1071 tests in 85 files, Prettier not applied to `web/` (as in 8.7's note), and the review-fix commit `e03105b8`.)**

## PR 3: live read-only views

## 3. Pure logic in `web/lib/cyl-pipeline/`

- [x] 3.0 Create `__fixtures__/supabase-mock.ts`, modelled on `components/expression-differential-analysis.test.tsx:253-295`. It provides:
  - a thenable query builder that records the table, `select`, `eq`, `in`, `or`, `not`, `is`, `lt`, `gte`, `order`, `limit`, `range` and `maybeSingle` calls, and answers via a per-test function;
  - a channel mock whose `.on()` returns itself and whose `.subscribe(cb)` captures `cb`;
  - a `removeChannel` spy;
  - a deferred helper (not `Promise.withResolvers`, because CI runs Node 20);
  - an exported `clientModule` for `vi.mock("@/lib/supabase/client", async () => (await import(".../supabase-mock")).clientModule)`.

  Add 0.3's captures.
- [x] 3.1 **Test first.** Write `run-display.test.ts`:
  - every scenario in the display requirement, including the clamp and a `failed` run keeping its `error_message`;
  - each stage's tooltip;
  - the raw status is always present;
  - no output mentions `reused_count`;
  - the scan status label map over all five CHECK values plus an unknown value.
- [x] 3.2 Implement `run-display.ts`, the status unions and the scan label map.
- [x] 3.3 **Test first.** Write `timestamps.test.ts`:
  - `…10.123456` is after `…10.123400`;
  - no fraction is before `.5`;
  - `+00:00` equals `Z`;
  - an unparsable value sorts as newest;
  - use the captured formats.
- [x] 3.4 Implement `timestamps.ts`.
- [x] 3.5 **Test first.** Write `realtime-reducer.test.ts`, using the captures:
  - buffered replay;
  - merge keeps absent fields;
  - counts never decrease;
  - the unknown-run cursor rule (including zero rows loaded);
  - the cursor moves only on snapshot or "load older";
  - "load older" de-duplicates;
  - ties are broken by `id`;
  - other-run events are ignored;
  - `countsFromScanRows`.
- [x] 3.6 Implement `realtime-reducer.ts`.
- [x] 3.7 **Test first.** Write `resync-scheduler.test.ts` (fake timers):
  - the first `SUBSCRIBED` refetches at t = 0;
  - `CLOSED` then `SUBSCRIBED` at t = 500 ms gives exactly one more refetch, at t = 2000 ms;
  - a burst of four transitions in the window still gives exactly one.
- [x] 3.8 Implement `resync-scheduler.ts`.
- [x] 3.9 **Test first.** Write `run-links.test.ts`:
  - one scan gives one "Scan images" link and one traits link;
  - the joint `(wave, age)` pair example from the spec;
  - the tie-break;
  - a null age omits `age`;
  - at most 3 pairs per experiment;
  - two experiments;
  - missing metadata.
- [x] 3.10 Implement `run-links.ts`.
- [x] 3.11 **Test first.** Write `failure-hints.test.ts`:
  - `likelyCause` rules via a shared `stageInProblems()`;
  - `isNoOpCandidate(row, hasResults)` is true only when `error_message === BACKSTOP_MESSAGE` and results exist.

  Export `BACKSTOP_MESSAGE` from a module that has a test pinning it to the poller's text. Read `services/workflows/status_poller.py` in the test and assert the string is present.
- [x] 3.12 Implement `stage-in.ts` and `failure-hints.ts`.

## 4. Read queries

- [x] 4.1 **Test first.** Write `queries.test.ts`, asserting calls **per table**:
  - `fetchRuns({cursor?, mineOnly?})`: `created_at`/`id` desc; the keyset `.or()` built from the raw string with values double-quoted; `eq('requested_by')` when `mineOnly`; limit 50.
  - `fetchRun(id)` uses `maybeSingle`.
  - `fetchRunScans(runId)`: pages of 1000 ordered by `scan_id` until an empty page.
  - `fetchScanMeta(ids)`: chunks of ≤ 200, including `qr_code`, `wave_number`, `plant_age_days`, species and accession ids.
  - `fetchLatestSources(ids)`: chunks of ≤ 200, returning `scan_id` and `max_source_id`.
  - `fetchRunExperiments(runIds)` and `fetchExperimentRunIds(expId)`: the view, `created_at` desc, limit 10.
  - `isRunInExperiment`.
  - Every error surfaces typed.
- [x] 4.2 Implement `queries.ts`.

## 5. `LiveIndicator` and nav

- [x] 5.1 **Test first.** Write `web/components/recent-phenotypes-by-cyl-scanner/LiveIndicator.test.tsx`:
  - **(characterization)** with no prop it renders as today, and the plate-scanner consumer is unchanged;
  - `connecting` and `offline` render distinct `aria-live` text;
  - `offline` renders `onRefresh`.
- [x] 5.2 Implement the optional props.
- [x] 5.3 **Test first.** Write `web/components/nav-sections.test.ts`:
  - a "Cylinder Pipeline Runs" entry at `/app/cyl-pipeline-runs` (first built as "Pipeline runs"; renamed 2026-09-29 so it says which pipeline, since RNA-seq runs are coming too);
  - every other entry equals today's list, with the array inlined as the expected value.

  `/app/pipelines` has no nav entry and stays absent.
- [x] 5.4 Move `navSections` into `web/components/nav-sections.ts`, import it in `layout.tsx`, and add the entry.

## 6. Runs list `/app/cyl-pipeline-runs`

- [x] 6.1 **Test first.** Write `RunsListLive.test.tsx`:
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
- [x] 6.2 **Test first.** Write `page.test.tsx` (expression style): the snapshot renders, and a failure shows the error.
- [x] 6.3 Implement `page.tsx`, `RunsListLive.tsx` and `RunRow.tsx`.

## 7. Drill-down `/app/cyl-pipeline-runs/[runId]`

- [x] 7.1 **Test first.** Write `[runId]/page.test.tsx`:
  - each invalid id in the spec scenario calls `notFound()` (mocked to throw) via `parseId`;
  - an unknown id (`maybeSingle` → null) calls `notFound()`;
  - a lookup error renders an error;
  - a valid id renders.
- [x] 7.2 **Test first.** Write `RunDetailLive.test.tsx`, mocking `./RunScansTable` with a stub that shows the row count and the links:
  - channel filters;
  - a scan UPDATE to `written` changes the label and the header count;
  - other-run events are ignored;
  - elapsed time and "last scan update" (`vi.setSystemTime`);
  - the header params line for `{}` and for non-empty params;
  - 5000 rows take 6 `cyl_pipeline_run_scans` calls;
  - the empty state;
  - a failed row gets its likely cause, and the #900 note only for a no-result text with results (write-back's or the poller's backstop; PR 6 added write-back's). A row that turns failed live triggers exactly one metadata/latest-source lookup;
  - "current in trait views";
  - the timing note;
  - the experiment links;
  - the sync cases from 6.1 for both channels.
- [x] 7.3 **Test first.** Write `RunScansTable.test.tsx` (real timers, `disableVirtualization`):
  - 5000 rows show `/1–100 of 5000/`, with page size 100;
  - the status filter;
  - every column in the spec's list;
  - the status labels.
- [x] 7.4 Implement `[runId]/page.tsx`, `RunDetailLive.tsx` and `RunScansTable.tsx`.

  **Notes on §3–8 as built (2026-09-28):**
  - **One channel per view.** The drill-down puts its three bindings (run UPDATE `id=eq.`, scan INSERT and UPDATE `run_id=eq.`) on one channel, so one status stream drives one resync. 7.2's "both channels" sync cases are covered as both tables' events on that channel.
  - **Client-loaded snapshots wait for `SUBSCRIBED`.** The drill-down's scan rows and the experiment panel's runs load on the first `SUBSCRIBED`, not at mount. A mount fetch would be redundant, since the first `SUBSCRIBED` must refetch anyway, and it would double a 5000-row read. If the channel fails before its first `SUBSCRIBED`, the view says live updates are unavailable (not "Loading…") and offers Refresh, which loads it. The list and the drill-down header are server-rendered.
  - **The drill-down header** uses the run row's own counts until the scan rows first load, then the held-row tallies.
  - **Synced side state.** Everything a snapshot reads lives in the synced view: the drill-down's scan metadata, latest sources and experiments; the list's pages. So a superseded fetch can't leave stale pieces behind, and buffered events replay onto all of it. `update()` calls made during a fetch are buffered like events. A "Load older" page is dropped if a snapshot started after it, because appending it to a newer window would move the cursor past rows neither holds (found in PR 3's review).
  - **"Current in trait views"** is "unknown" when the latest-source read failed, and for a row whose `source_id` changed since that read, until the next snapshot. It isn't inferred: an empty envelope marks a row written with a `source_id` and inserts no traits, so the scan's latest source doesn't move.
  - **Round 2 of the review** (after bloom#939 was filed):
    - rows turning failed live are looked up in one batch per 500 ms burst, reading only metadata the view lacks, and a batch is dropped if a newer snapshot started or the row's source changed meanwhile;
    - an unreadable experiments view means no traits links, and says so;
    - the panel never re-adds a run it already holds from an older membership answer;
    - "Load older" checks the store, not the last render, for a fetch in flight;
    - "Only mine" hides other members' rows at once;
    - Refresh, Retry and "Only mine" open the 2 s resync window like any refetch;
    - a failed names lookup isn't retried (at most one per run).
  - **Degraded reads.** A failed metadata or latest-source read keeps the rows and says scan details are unavailable. Only a failed run-scan read fails the snapshot.
  - **Experiment names** in the drill-down come only from `cyl_pipeline_run_experiments`. `cyl_scans_extended` runs with its owner's rights, so its names would include soft-deleted experiments. Traits links are limited to the experiments that view lists.
  - **Panel membership.** A confirmed member is added from its Realtime payload when that payload is a whole row; otherwise it is read by id (`fetchRunsByIds`, added to `queries.ts`).
  - **Shared pieces:** `use-live-sync.ts` (the per-view sync loop), `components/cyl-pipeline/RunState.tsx` (the label, with the failed count linked), `elapsed.ts`, `run-text.ts`, `scan-meta.ts` and `use-now.ts`.

## 8. Experiment panel, guard, wrap-up

- [x] 8.1 **Test first.** Write `web/components/cyl-pipeline/ExperimentRunsPanel.test.tsx`:
  - the 10 most recent, with display states;
  - held-only events;
  - it stays at 10 on insert;
  - the race (INSERT with empty membership isn't cached; a `submitted` UPDATE re-queries and lists the run);
  - a foreign `running` run gives one query, then it is cached;
  - "Runs unavailable";
  - the links;
  - the 6.1 sync cases.
- [x] 8.2 **Test first.** Write an experiment page render test asserting the panel receives `experimentId`.
- [x] 8.3 Implement `ExperimentRunsPanel.tsx` and mount it.
- [x] 8.4 Write `web/lib/cyl-pipeline/no-provenance-joins.test.ts`, using plain `node:fs`. The resolver takes an injected `{webRoot, readFile, exists}`.
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
- [x] 8.5 Add a "Cylinder pipeline runs" section to `web/README.md`.
- [x] 8.6 Live check on the dev stack.
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

  **(done 2026-09-29, 23/23 checks, with these deviations:**
  - **Where it ran.** `next dev -p 3001` from PR 3's worktree against the running dev Supabase, with env on the command line only, because the Docker stack serves the main checkout. Edge was driven by a throwaway Playwright script as the dev `bloom_user`.
  - **Realtime through Kong** needed a temporary `realtime` tenant: a copy of `realtime-dev` in `_realtime.tenants` and `_realtime.extensions`, deleted afterwards, with Realtime restarted. Without it every socket fails `TenantNotFound: realtime` (see 0.3); that defect needs its own fix.
  - **The trigger curl returns 500** on the dev stack ("missing WORKFLOWS_SUPABASE_EMAIL, WORKFLOWS_SUPABASE_PASSWORD"), before any write. The run was created by SQL instead, in the trigger's order: run row `queued`, then its scan row, then `submitted`. `WORKFLOWS_K8S_TOKEN` was empty, and the worker and poller were stopped throughout.
  - **Bug found and fixed.** The first pass showed channels reaching `SUBSCRIBED` with no events delivered. On a full page load the views' `phx_join` carried no user token (anon), so RLS dropped every run event. `useLiveSync` now awaits `realtime.setAuth()` before subscribing, tested red first. The first fix (commit "join Realtime as the signed-in user") passed `session.access_token` explicitly; PR #938's review replaced that with the no-argument call, which reads the token through supabase-js's `accessToken` callback instead of pinning one (commit "buffer live-view updates, and let Realtime keep the token fresh").
  - **Re-run after the review fixes.** The same script passed 23/23 again on 2026-09-29, on the code of commits 165fceb9–f6dd1da7, with the same temporary tenant (removed afterwards). The join probe showed `phx_join` carrying `bloom_user`.
  - **Verified live, with no reload:**
    - a new run appears on top of the list, attributed to "you";
    - its experiment name appears after `submitted`;
    - the drill-down's scan row turns "Result recorded" and its header "Finished · 1 succeeded" when the scan row is updated, while the list's row waits for the run row's rollup and then follows;
    - the experiment panel lists the run, and adds a second run once its scan rows land;
    - with 57 runs, the list shows 50, and "Load older" gives all 57 in exact `(created_at, id)` order with no duplicates;
    - 60 quiet seconds made no REST requests, and no `/workflows/runs` request was ever made.
  - **Cold-start drop.** The one-event losses in 0.3 came only on a tenant's first subscription after Realtime started: twice, each time on a cold tenant. None were lost in the warm sessions (three capture runs, a 50-insert probe, and the 8.6 browser runs).
    - A later event for the same row repairs the view: UPDATE payloads carry every column but an unchanged TOASTed `error_message`, the list admits unknown runs, and every view merges held ones.
    - A dropped *final* event has no later event: a scan row's last transition, or the run's last rollup. It stays missing until a reconnect, a reload or Refresh.
    - Staging restarts Realtime on every deploy, so cold starts recur.
  - **Cleanup.** Queue purged (it was empty), services restarted, no run rows left.)**
- [x] 8.7 Verify:
  - `openspec validate add-cyl-pipeline-ui --strict`;
  - `cd web && npx tsc --noEmit && npm run test:unit && npm run build`;
  - `npx prettier --check <changed files>`;
  - `python3 scripts/lint_migration_isolation.py origin/staging` reports no migration change.

  Then run `/pre-merge` and `/review-pr`. PR body: "Refs #15".

  **(PR 3, 2026-09-28: `openspec validate --strict` valid; `tsc --noEmit` clean; `npm run test:unit` 1348/1348 after both PR review rounds and the nav rename; `npm run build` passes with CI's env (`NEXT_PUBLIC_SUPABASE_*` placeholders, as in `pr-checks.yml`; without them the existing `/test` page fails to prerender); migration-isolation "no migration change"; no `supabase/` or `database.types.ts` in the diff. `prettier --check` flags every changed file, and equally untouched merged ones such as `TraitExplorer.tsx` and `navigation.tsx`: Prettier isn't applied to `web/` and CI doesn't run it, so the files follow the surrounding code instead.)**
- [x] 8.8 **After the staging deploy of PR 3, and after bloom#939 (Realtime tenant through Kong) is fixed there:** as a second signed-in member, decode the token (`role: bloom_user`) and confirm live updates arrive when a run row changes. That proves Realtime-as-`bloom_user` before any UI trigger ships. Record the result on the PR.

  **(PR 6, 2026-09-30, staging deploy `b0d455bf`: run 18 was started by `bloom-staging-ops` (a `bloom_writer`) while the author watched the runs list as a plain member. It appeared at the top live, as "another member · cb4de37b", and changed to "Finished · 1 succeeded" without a reload. The author's account has no role flags, and the live `custom_access_token_hook` gives such accounts `bloom_user`. The #939 anon-join-after-hard-reload remainder didn't affect the views in this session: the indicator stayed Live across page loads and across a 90 s cable disconnect.)**

## PR 4: trigger proxy

## 9. Proxy

- [x] 9.1 **Test first.** Write `trigger-request.test.ts`:
  - every validation rule in the spec, including `target_id: null` with `scan_ids`, and both `MAX_TRIGGER_SCAN_IDS` bounds (5000 and 5001);
  - the rebuilt body carries `params: {}`.
- [x] 9.2 Implement `trigger-request.ts`, exporting `MAX_TRIGGER_SCAN_IDS = 5000`, with a comment tying it to `services/workflows/pipeline.py`'s `MAX_SCAN_IDS`. Add a test that reads `pipeline.py` and asserts the two values are equal. **(characterization: that test and the `= 5000` test pass against the stub, which already carries the constant.)**
- [x] 9.3 **Test first.** Write `web/app/api/cyl/pipeline/route.test.ts`, modelled on the video route test. One test per scenario in the three proxy requirements, plus:
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
- [x] 9.4 Implement `route.ts`, with a comment that the Origin check depends on Caddy having no `trusted_proxies`. Update the `caddy/Caddyfile:123` comment, and confirm `tests/unit/test_caddy_cyl_video_route.py` passes. **(characterization: its new route-exists test.)**
- [x] 9.5 Verify as in 8.7. The PR body notes that the route is reachable by same-origin signed-in POSTs once deployed, which adds nothing beyond `/workflows/pipeline`. Then `/pre-merge` and `/review-pr`.

  **(PR 4, #952, 2026-09-29: `openspec validate --strict` valid; `tsc --noEmit` clean; `npm run test:unit` all green, with 72 route and validation tests after two `/review-pr` rounds (5 reviewers, then 2); `npm run build` passes with CI's placeholder env; `test_caddy_cyl_video_route.py` 5 passed; migration-isolation "no migration change"; no `supabase/` or `database.types.ts` in the diff. `/pre-merge`: `npm audit --audit-level=critical` clean, no dependency changes; Docker image builds and compose integration tests left to CI.)**

## PR 5: trigger batching (bloom#901)

## 9b. Batch the trigger's id filters; skip the preview for `{}`

- [x] 9b.1 **Test first.** Write `services/workflows/tests/test_postgrest_batches.py` for `id_batches(ids, budget=4000)`:
  - an empty list gives no batches;
  - the rendered length of each batch, counting separators, is ≤ budget;
  - order is preserved and ids are neither lost nor duplicated;
  - a single id longer than the budget gets its own batch;
  - 19-digit ids batch correctly;
  - `ID_FILTER_BUDGET_CHARS` equals the value in `bloomcli/src/bloomctl/_postgrest.py`. The test reads that file, so the two can't drift. (characterization)
- [x] 9b.2 Implement `services/workflows/postgrest_batches.py`, ported from bloomctl's `id_batches`, with a header citing the 414 measurement. (That measurement is in the PR author's self-review comment on PR #650, https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/650#issuecomment-5269400672, not bloom#674 as this line first said.)
- [x] 9b.3 **Test first.** Extend `services/workflows/tests/test_pipeline.py`. Its `_FakeClient` must record each `.in_()` id list, so it may need extending.
  - A 3000-id `scan_ids` request issues more than one `cyl_scans_extended` filter call, each within budget, and proceeds as if all were found.
  - A missing id in the last batch still gives 404 naming it, with no rows written.
  - With non-empty params and 2500 enumerated scans, every `cyl_scan_traits`/`cyl_trait_sources` call is within budget, and `reused_count` matches the unbatched expectation.
  - A request with `params: {}` issues **no** `cyl_scan_traits` or `cyl_trait_sources` call, and `reused_count = 0`, even when the scans have sources.
  - **Regression:** update `test_dedup_preview_issues_one_batched_query_not_a_per_scan_loop` to non-empty params (`{"age": 14}`, a matching `_hash_of`). Its "3 and 30 scans give the same query count" assertion still holds, since both fit in one batch.
  - Every other existing dedup and enumeration test passes unchanged.
- [x] 9b.4 Implement it in `services/workflows/pipeline.py`: batch the three filters, merge the results, and short-circuit `_dedup_preview` when `params == {}`.
- [x] 9b.5 Update the trigger section of `services/workflows/README.md`: id filters are batched, and the preview is skipped for `{}`.
- [x] 9b.6 Verify:
  - `cd services/workflows && uv run --frozen --extra test pytest`;
  - `uv run --extra test pytest tests/integration/test_cyl_pipeline_dispatch.py`;
  - `uv run ruff check` and `uv run black --check` on the changed files;
  - `openspec validate add-cyl-pipeline-ui --strict`.

  Then:
  - on the dev stack, with 8.6's setup (worker and poller stopped, K8s token empty), `curl http://localhost:5100/pipeline` an experiment-level target of at least 2,000 seeded scans. It must return 200 with the right `scan_count`, and the run and scan rows must be written;
  - purge the queue and restart the services;
  - record the result, then `/pre-merge` and `/review-pr`. The PR body says "Refs #901"; it doesn't close it, because the non-empty-params row-volume half stays open.

  **Done 2026-09-29.** Unit suite 842 passed, 1 skipped (after rebasing onto `438c2d73`; 847 after the review tests); `test_cyl_pipeline_dispatch.py` 41 passed; ruff 0.9.9, black 26.3.1 and ruff-format clean; strict validate passes.

  Dev stack: worker and poller stopped, `WORKFLOWS_K8S_TOKEN` empty in all three containers. The dev workflows service has no app user, so a throwaway `is_workflows` user was created (README "Provisioning") and deleted afterwards. The main checkout serves the stack, so two one-off containers ran instead of `localhost:5100`: this branch on `:5101`, and staging's `pipeline.py` on `:5102` as a control. The seeded experiment had 2,100 scans (ids 570–2669, 10,069 rendered characters).

  | Request                             | Code    | Result                                                     |
  | ----------------------------------- | ------- | ---------------------------------------------------------- |
  | experiment, `params: {}`            | staging | 500: `APIError 414 'URI too long'`; no rows written        |
  | experiment, `params: {}`            | branch  | 200, `scan_count` 2100, `reused_count` 0                   |
  | `scan_ids`, all 2,100, `params: {}` | branch  | 200, `scan_count` 2100                                     |
  | experiment, `params: {"age": 14}`   | branch  | 200, `scan_count` 2100, `reused_count` 0 (batched preview) |

  Each branch run wrote 2,100 `queued` scan rows in 84 batches; the queue held 252 = 3 × 84 messages. Cleanup purged the queue, deleted the runs, seeded rows and user, and restarted both services.

  The `{"age": 14}` row above proves little: those scans had no trait rows, so the preview stopped after `cyl_scan_traits`, and no stored hash is over partial params. After review, a second run (worktree commit `e8c874f0`) seeded 2,100 scans (four-digit ids; the exact range wasn't recorded), 2 sources each (4,200, with ids 1376–5575) and 3 trait rows per source. Every third scan's older source carries the `resolve_params` hash of soybean/cylinder/age 14, so the expected `reused_count` is 700.

  | `params`                                      | Result                  | Kong requests (`cyl_scan_traits` / `cyl_trait_sources`) |
  | --------------------------------------------- | ----------------------- | ------------------------------------------------------- |
  | `{species: soybean, mode: cylinder, age: 14}` | 200, `reused_count` 700 | 3 / 6, no 414, longest request URI 5,666 bytes          |
  | `{species: soybean, mode: cylinder, age: 99}` | 200, `reused_count` 0   | 3 / 6, no 414                                           |
  | `{}`                                          | 200, `reused_count` 0   | 0 / 0                                                   |

  During this second run `cyl-pipeline-worker` and `cyl-status-poller` were running, not stopped as 8.6 asks. Their logs show only "workflows service not configured" retries, since they have no app-user credentials, so they claimed nothing. The 252 queue messages and all rows stayed `queued` until cleanup, which removed the same kinds of rows as the first run.

## PR 6: trigger UI

## 10. Dialog logic and queries

- [x] 10.1 **Test first.** Write `params-summary.test.ts`:
  - the spec scenario, exactly;
  - flagged scans are excluded from the groups;
  - it uses `stageInProblems()`;
  - the output's key set equals exactly `['groups', 'stageInCount']`.
- [x] 10.2 Implement `params-summary.ts`.
- [x] 10.3 **Test first.** Extend `queries.test.ts`:
  - `fetchTargetScans(target)` uses the trigger's filters, pages of 1000 ordered by `scan_id`, and `scan_ids` chunks of ≤ 200; 2,500 scans give N = 2500.
  - `fetchConcurrentRuns(experimentIds)`:
    1. ask the view for `experiment_id=in.(…)` (chunked) with `created_at` within 7 days;
    2. read those runs (`id=in.(…)`, chunked) with `status=not.in.(complete,failed)`;
    3. filter to incomplete counts in the client;
    4. return at most 10, plus the true count of the rest.

    (PR 6 review: the first draft read the 20 newest unfinished runs lab-wide before checking membership, so frozen runs elsewhere could hide this experiment's. It now matches the spec, with no candidate cap.)
- [x] 10.4 Implement them.
- [x] 10.5 **Test first.** Write `accession-scan-ids.test.ts`: it returns every `plant.cyl_scans[].id`, de-duplicated and without mutating its input. Two same-day scans plus one with `cyl_images: []` give 3 ids.
- [x] 10.6 Implement `web/components/cyl-pipeline/accession-scan-ids.ts`.

## 11. Dialog, selection, entry points

- [x] 11.1a **Test first.** Write `RunPipelineDialog.test.tsx`, part 1 (content):
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
- [x] 11.1b **Test first.** Part 2 (submit):
  - two synchronous clicks give one `POST`, to exactly `/api/cyl/pipeline`;
  - the success state, with the timing and reload note and the mismatch note;
  - 429, 502/504, 401, and 404/422 behaviour;
  - a query failure.
- [x] 11.2 Implement `RunPipelineDialog.tsx` and `RunPipelineButton.tsx`.
- [x] 11.3 **Test first.** Write `ScanSelection.test.tsx`:
  - counts by scan id;
  - `closest('a') === null`, and clicking doesn't navigate;
  - "Select all shown";
  - the bar is hidden at 0 and carries the per-page note;
  - "Run selected (3)" submits exactly those ids through the mocked `fetch`;
  - it is disabled over the limit.
- [x] 11.4 Implement the selection components.
- [x] 11.5 **Test first.** Page render tests:
  - the scan page shows the button only when the scan exists;
  - the experiment page shows "Run experiment", plus "Run wave" only when there is more than one wave;
  - the accession page shows "Run this accession" with exactly `accessionScanIds(plants)` (computed before the in-place sort), disabled over the limit, plus the checkboxes.
- [x] 11.6 Wire the three pages.
- [x] 11.7 **Test first.** Extend `RunDetailLive.test.tsx`:
  - "Re-run failed" is gated, submits exactly the failed ids, and warns when a row has the #900 note;
  - "Re-run scans without a result (M)" appears only when U > 0 on `complete`/`failed` runs;
  - the settled case offers only "Re-run failed";
  - both are disabled over the limit.

  Extend `ExperimentRunsPanel.test.tsx` with the optimistic insert.
- [x] 11.8 Implement.
- [x] 11.9 **Test first** (added in PR 6's review, bloom#863). A server-side switch, `CYL_PIPELINE_TRIGGER_ENABLED`, which is on only for exactly `true`:
  - off hides every run action and selection on the scan, experiment, accession and drill-down pages;
  - off makes the proxy answer 503 before reading the session or body;
  - staging defaults set it `true` and prod `false`, and prod compose passes it to bloom-web (`tests/unit/test_env_defaults.py`).

## 12. Live verification on staging (before PR 6 merges)

**Environment:** the web app runs locally against staging, with `WORKFLOWS_URL=https://staging.bloom.salk.edu:8443/workflows` and staging `NEXT_PUBLIC_SUPABASE_URL`.

**Before starting:**
- Confirm the live `insert_cyl_result_envelope` contract pin matches the deployed traits image's `contract_version` (bloom#895).
- Create controlled scans with `bloomctl cyl create-test-scan --good`, and `--poison` for a deterministic stage-in failure.
- For a null-age scan, have a `bloom_admin` set one test scan's `plant_age_days` to NULL, and revert it afterwards.
- Write down a manual cancel procedure (delete the Argo workflows, purge pgmq) before any multi-scan run.

- [x] 12.1 Run one scan from the scan page, and record the run id. Confirm:
  - the list and drill-down update without a reload and reach a finished state (intermediate states may be skipped within one sweep);
  - there is exactly 1 scan row, with counts 1/1;
  - there are no timer-driven reads and no `/workflows/runs` calls.
- [x] 12.2 Trigger a `scan_ids` run of at least 3 scans via "Run this accession" or the grid. Confirm:
  - it appears on the experiment panel;
  - it appears live for a second member, whose decoded `role` is `bloom_user`;
  - the same for `bloom_writer`/`bloom_admin` accounts, if they exist.
- [x] 12.3 Trigger a run including the `--poison` scan and the null-age scan (via the scan page or "Run this accession"; null-age scans don't render in the grid). Confirm:
  - the dialog warned about the null-age scan;
  - the actual per-scan `error_message` values, recorded;
  - "Re-run failed" submits exactly the failed ids.

  Also confirm a staging run with `status='complete' and failed_count>0` renders by rule 3 or rule 5, according to its counts.
- [x] 12.3b Run a scan whose only source came from `bloomctl cyl ingest-result`. Record its status. If it is `failed` with a no-result text (write-back's, or the poller's backstop), confirm the #900 note, and add the evidence to bloom#900 (confirm with the user before posting).
- [x] 12.4 Turn the network adapter off for 30 s during an active run, then back on. Confirm:
  - `CHANNEL_ERROR`/`CLOSED` then `SUBSCRIBED` in the console;
  - the indicator shows offline, then live;
  - the counts resync.
- [x] 12.5 If an empty wave exists, confirm the dialog shows "No scans to run". Note that any zero-scan run triggered via `curl` leaves a permanent "No scans matched" row.
- [ ] 12.6 Observe whether a large reconciliation burst disconnects other Realtime widgets, and record it.
- [x] 12.7 Leave a drill-down open past the JWT lifetime. Confirm it recovers or shows offline with refresh.
- [ ] 12.8 For the runs in 12.1–12.3, confirm `done_count`/`failed_count` equal the per-status tallies. Record this as evidence for `fix-cyl-pipeline-run-scan-status` 8.1–8.4, and tick those only in that change, only if they match.
- [x] 12.9 Open a run's traits link and confirm it lands on the run's wave and day, with the run's scans visible.
- [x] 12.10 Trigger one experiment-level run of the largest practical staging experiment (at least 1,500 scans if one exists). Use the manual cancel procedure afterwards if it isn't wanted to finish. Confirm:
  - it is accepted as **one** run;
  - `scan_count` is right;
  - the drill-down loads every row.

  Record the trigger latency, since it makes 25-scan enqueue RPCs sequentially.
- [ ] 12.11 Record run ids, screenshots and mismatches in the PR. Mismatches are fixed or filed, not waived. Then verify as in 8.7, and run `/pre-merge` and `/review-pr`.

  **(PR 6, 2026-09-30, local web app against staging `b0d455bf`; details and screenshots are in PR #965's body.)**
  - **Preconditions:**
    - contract pin a9 on the RPC and on the live trait-extractor template;
    - test scans TEST-E2E-019 and 020, created with `create-test-scan --good`; 014 and 007 are the poison scans;
    - 020's age was set to NULL by a `bloom_writer` before staging, then reverted (no staging account is a `bloom_admin`);
    - the cancel procedure was written before the multi-scan runs.
  - **12.1:** run 15, one row, updated live to "Finished · 1 succeeded". No polling: no browser data requests in 6½ idle minutes, and no `GET /runs/…` at the workflows service.
  - **12.2:** run 16 was listed live on the experiment panel. Run 18, started by another member, appeared live for a `bloom_user` (see 8.8).
  - **12.3:** run 16 (11 scans) showed the stage-in warning for the null-age scan. 015–019 were written; 010–013, 014 and 020 failed, all with write-back's "no result produced for this scan by write-back". The #900 note shows only where the scan has results, and "Re-run failed scans (6)" lists exactly those. Complete runs with failures render by rule 3.
  - **12.3b:** covered by the hand-submitted-source variant (runs 11 and 16; the other session's comment on #900). This found that the note had to match write-back's text as well (`05abc061`).
  - **12.4:** during run 17, a 90 s cable disconnect took the indicator Live → offline → Live, followed by a full snapshot refetch.
  - **12.5:** not applicable. Staging has no wave with zero scans.
  - **12.9:** run 11's link opened wave 9999 · day 2 with no fallback note.
  - **12.10:** run 17, Missouri_Soy_Repetition, 1,515 scans: one run, `scan_count` 1515, all rows loaded, about 3 s trigger latency. It was cancelled because staging's image bytes are missing (every frame 404s). About 165 failed downloader pods from retries fed into srp#98.
  - **12.7:** run 18's drill-down, opened at about 19:17Z, still showed Live about 80 minutes later (20:38Z), past the one-hour JWT lifetime. Staging's Kong log shows the client's token refreshes (20:03–20:30Z) and Realtime rejoins (20:31, 20:36Z). Accepted by the author on the indicator. Caveat: no event was received on that tab after expiry, and the log can't separate that tab from other browser activity.
  - **Still open:** 12.6 (no large burst is possible on staging), and 12.8 (to be ticked in `fix-cyl-pipeline-run-scan-status`: runs 15, 16 and 18 counts equal their tallies).

## 13. After merge

- [ ] 13.1 Draft a bloom#15 comment: §10 v1 has shipped; phases 3–4, #865, #897, #898, #899 and #900 remain. Post only with explicit user go-ahead.
- [ ] 13.2 Draft updates to sleap-roots-pipeline's roadmap row (line 308) and to design §10's text (overrides, "N will run", blob links, requester names), coordinated with PR #88.
- [ ] 13.3 Once §12 has been repeated on the deployed staging build, run `/openspec:archive add-cyl-pipeline-ui`.
