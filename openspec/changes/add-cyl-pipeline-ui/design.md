## Context

Claims are checked against `origin/staging` @ `7f3ff94a` (2026-09-24). Paths are repo-relative. Normative rules live in the spec; this file holds the evidence and rationale.

**Why v1 is buildable now.**

- **The counts are written.** bloom PR #774 merged on 2026-09-15.
  - The poller sets `done_count` from `written`/`reused` rows and `failed_count` from `failed` rows (`status_poller.py:212-213,272-273`).
  - It writes them through `update_cyl_pipeline_run_status` (`:450`) on every sweep that concludes a status.
  - It skips the write when the rollup is None, or when `complete` is withheld (`:343-353`, bloom#706). That run's row and its Realtime events then freeze.
  - `fix-cyl-pipeline-run-scan-status` tasks 8.1–8.4 (live accuracy) are still open. Task 12.8 here produces that evidence.
- **srp#71 affects `cyl_trait_sources`, not progress.**
  - Progress rows are the *requested* scans, inserted at trigger time (`pipeline.py:311-322`).
  - Extra source rows are correct results. Write-back dedups on `idempotency_key` (`20260917140000:128`), whose inputs are images, params, models and code but no run id (contracts `compute_idempotency_key`). An unrequested row therefore appears only when one of those inputs changed.
  - sleap-roots-pipeline PR #88 (open) cites run `hpdpf` ingesting 0/12.
  - No cleanup is needed.

**Why three §10 items are cut down.**

- **Params are inert (#897).**
  - `k8s_client.build_workflow_body` takes no params.
  - The vendored workflow takes only `scan-ids`.
  - `bloomctl download_for_predict` resolves each scan's params itself, via `resolve_params(scan, overrides={"mode": "cylinder"})` (`download_for_predict.py:95-97`).
- **No skip prediction (#898).**
  - The trigger has no dry-run and enqueues every scan.
  - `reused_count` comes from `_dedup_preview` (`pipeline.py:297`), which runs before the inserts at `:300,322`. It compares one hash of the raw request params against stored resolved hashes, so it is 0 for `params: {}` (#584, #897).
  - The real skip happens per stage on the cluster, against the stage's own outputs, keyed by the idempotency key (sleap-roots-pipeline `openspec/specs/per-batch-pipeline/spec.md:59,492`).
- **`status` misleads (#857/#706/#710).**
  - A run can be `complete` with failures (#857).
  - `partial` is not terminal: the rollup still updates it and re-stamps `completed_at` (`20260912111000:60-66`).
  - Runs freeze in `running` (#706) or `queued` (#710, dead dispatch client).

**Trigger** (`main.py:183-204`, `pipeline.py:71-334`; `POST /workflows/pipeline` via `Caddyfile:174-176`).

- Body rules are at `pipeline.py:71-122`. Wave and experiment targets are unbounded (`:142-188`).
- Upstream behaviour:
  - `Retry-After` is always the 60 s window (`auth.py:95-96`).
  - 500/503 bodies leak internals (`auth.py:47-51,69-72`).
- The trigger is synchronous and **not idempotent**. It inserts the run, then the scan rows (a separate request), then one enqueue RPC per 25-scan batch (`:300-328`), with no transaction. A run can therefore exist even though the caller saw an error.
- **URL-length hazard, at every target level (bloom#901).** The `scan_ids` existence check (`:191-198`) and the dedup preview each put all ids into one unpaged `.in_()`: the preview's scan-id query (`:212-219`) and its source-id query (`:226-233`, possibly many times N). The gateway returns `414` above roughly 1,300 small ids (measured in bloom#674; bloomctl batches since, `bloomcli/src/bloomctl/_postgrest.py`). So a plain experiment run over roughly 1,300 scans fails today. The preview also fetches every trait row of the requested scans (about 1,035 per scan), which is a volume problem under the 8 s timeout. D9 fixes the URL half, and removes the preview's cost entirely for `params: {}`.

**Rate limit.** 5 requests per 60 s per user, per process; prod runs one worker (`docker-compose.prod.yml:256`). The limit is charged after auth and before `pipeline.py`'s body validation (`main.py:195`). It is shared across `/pipeline`, `GET /runs/{id}`, scan-video and plate-video (`main.py:93,154,195,219`).

**Tables** (`20260730120000`; never altered since).

- `cyl_pipeline_runs` has no `updated_at`, and `requested_by` has no FK.
- `cyl_pipeline_run_scans`:
  - `updated_at` is set to `now()` by every writer (`20260817120000:115,196`, `20260912110000:337`, `20260917140000:157,186,299`).
  - There is no `scan_id` index.
  - Rows are inserted once. Today they move only `queued` → `written`/`failed`, and every writer guards `status != 'failed'`, so failed and written rows are final. `predicted` and `reused` are allowed by the CHECK but unused (`reused` is reserved, `20260912110000:18`).
- **Access:**
  - `bloom_workflows` inserts through column grants (`:75-88,127-138`); definer RPCs update; `bloom_admin` has `FOR ALL`.
  - `bloom_user` has `SELECT USING (true)` on both tables.
- **Realtime:**
  - Both tables are published (`:143-155`), with replica identity DEFAULT. Realtime is v2.34.47.
  - Its `apply_rls` builds `record` from wal2json columns. An unchanged TOASTed column is therefore **absent** from UPDATE payloads (checked on dev by a round-3 reviewer; task 0.3 captures real payloads). realtime-js keeps absent keys absent.
- **Statement timeout:** PostgREST reads run under `authenticator`'s `statement_timeout = 8s`. That is an image default, observed in `20260921120000:4`.

**Web.**

- **Stack:**
  - Next 16.3.4 App Router (docs under repo-root `node_modules/next/dist/docs/`; the local install is stale at 16.2.0, see task 0.1) and React 19.
  - MUI x-data-grid is a webpack `externals` entry (`next.config.js:52-57`), so its components are `"use client"`.
  - Next's build type-check rejects extra named exports from `page.tsx`/`layout.tsx`/`route.ts`.
- **Reusable pieces:**
  - The JWT-forwarding proxy pattern is `app/api/cyl/experiments/[experimentId]/scans/[scanId]/video/route.ts`.
  - `parseId` (`web/lib/route-params.ts`) rejects non-digit, zero and unsafe ids. It accepts leading zeros.
- **Realtime today:**
  - Two consumers exist: the cyl and plate recent-phenotype widgets. Both are INSERT-cue-only, with no status handling.
  - `LiveIndicator` has no props and is "purely decorative".
- **`TraitExplorer`:** it sets wave to the last and age to the max **inside its data effect**, which re-runs on every trait change (`TraitExplorer.tsx:46-86`). It reads no URL params.
- **Auth and routing:**
  - The auth cookie is `SameSite=lax` and not httpOnly.
  - No route checks `Origin`.
  - Middleware lets `/api/*` through without auth. Because middleware matches the route, Next buffers request bodies up to 10 MB before the handler runs.
- **Visibility for `bloom_user`:**
  - `phenotypers` returns 0 rows: its policies are `TO authenticated` (`20230711213312:18-21`), and `bloom_user` is not a member of `authenticated`. `bloom_writer` is (`20260622180000:23`).
  - `cyl_experiments` hides soft-deleted rows from `bloom_user`/`bloom_agent` (`20260414002000:95`). This is cosmetic, not a boundary: the runs and scans of such an experiment stay readable.
- **Dev stack:**
  - It runs `cyl-pipeline-worker` and `cyl-status-poller` (`docker-compose.dev.yml:128-155,183-205`), and has no Caddy (workflows is at `localhost:5100`).
  - With `WORKFLOWS_K8S_*` set, a dev trigger submits real GPU work.
  - With it unset, claims are dead-lettered to `failed` after about 5 × 60 s (`dispatch_worker.py:90-101`; `20260817120000:166-171`).
- **Deploy gate:** bloom#895 (open) gates deploying any contract-a9 image. If one reaches the cluster first, every write-back fails.

## Goals / Non-Goals

**Goals:**

- A member can start a run for a scan, a wave, an experiment, an accession or a selection, and sees truthfully what will be sent.
- Every member sees every run update live.
- A member can drill into per-scan outcomes, re-run failed scans, and reach the existing trait views at the run's wave and age.

**Non-Goals:** see the proposal's Out of scope. v1 also offers no recovery action for runs frozen by #706/#710, because a "stuck" threshold is out of scope.

## Decisions

### D1. Trigger through a hardened Next route handler

- **CSRF.** A lax cookie rides same-site cross-origin posts, so any `*.salk.edu` page could otherwise start GPU work. Two checks close this:
  - **Media type.** A no-cors form post can't send `application/json`, so it gets 415. A CORS fetch triggers a preflight. Next's automatic OPTIONS reply carries only `Allow`, and Caddy adds no `Access-Control-Allow-*`, so the browser blocks it.
  - **Origin vs host.** `Origin` is compared with the first `x-forwarded-host` value, or `host`, matching Next's own server-action check. It is never compared with `request.url`, because bloom-web runs `next start -H 0.0.0.0`.
    - Caddy 2.11 has no `trusted_proxies`, so it overwrites client `X-Forwarded-Host`, and only Caddy can reach bloom-web.
    - The route comment records that this depends on there being no `trusted_proxies`. That matters if cloudflared (bloom#616) ever fronts bloom-web.
- **Rebuilt body.** `params` is always `{}`: it is the true "no overrides" value while #897 stands. Nothing from the client is spread into the forwarded body.
- **Timeout.** 120 s, under undici's 300 s. A 5000-scan trigger makes 200 sequential enqueue RPCs.
- **Body cap.** Next buffers up to 10 MB first, so the handler's 256 KB cap bounds validation, not memory. That is acceptable.
- **Why a proxy.** The browser client already holds the token for its reads, so the proxy isn't needed to hide it. It gives one place for CSRF, error normalisation and the timeout. It lives under `/api/cyl/*` because Caddy routes `/api/cyl/*`, `/api/gravi/*` and two exact paths to bloom-web, and every other `/api/*` to Kong (`Caddyfile:111-138`).
- **Duplicates.** There is no upstream idempotency key, and the dialog's in-flight guard stops double clicks only. So 502/504 copy says "may have started" (spec: "Confirm dialog submits once…"). This is conservative: an enumeration 414 fails before any insert, yet still shows that copy.
- **`MAX_TRIGGER_SCAN_IDS`** equals the trigger's `MAX_SCAN_IDS` (5000) and applies only to `scan_ids` targets. Wave and experiment runs have no size cap once D9 lands; only the ≥ 500 acknowledgement applies.

### D2. Reads via Supabase + Realtime; no polling

Polling `GET /runs/{id}` would share the 5/60 s limiter with the trigger and with video. The poller writes progressing runs about every 15 s.

- **Resync on every `SUBSCRIBED`, including the first.** This closes the gap between the server-rendered snapshot and the subscription.
  - It is leading **plus** trailing: another transition inside the window still gets exactly one refetch when the window ends. A leading-only throttle could drop a real reconnect.
  - Events during a fetch are buffered and replayed after it. Realtime delivers a channel's changes in commit order, so snapshot plus replay converges.
- **No `updated_at` guard.** `now()` is the transaction *start* time, so an older-starting transaction that commits later would be dropped. `Date.parse` on microsecond timestamps is also implementation-defined.
- **Merge, not replace.** Absent TOASTed columns keep their held values.
- **Counts only grow.** Monotonic `max(held, new)` is therefore safe; a resync corrects any admin edit.
- **List cursor.** The raw `(created_at, id)` of the oldest loaded row, changed only by a snapshot or "load older".
  - It exists because `partial` and still-`running` runs get an UPDATE every sweep. Inserting those into the window would make "load older" skip the runs in between.
  - Timestamps are compared by a microsecond-exact parser.
- **Unique topics per mount.** Needed because StrictMode double-mounts on the singleton browser client.
- **Auxiliary lookups.** Scan metadata and experiment names are fetched once per row, and only when needed:
  - experiment names for a live-inserted run, on its first non-`queued` event (the scan rows exist by then, as below);
  - likely-cause and "current in trait views" data for a row that turns `failed` live.
- **Panel membership race.** The trigger commits the run row before its scan rows. A negative membership answer is cached only when it came from a non-`queued` event: settle, and therefore `submitted`, happens only after the scan rows are inserted.
- **Drill-down header.** Its counts come from the held scan rows, so the header is live per scan event. The list's run row can trail it by one sweep.

### D3. Counts first

- Once `D + F = N` the run is finished, whatever its `status`, because failed and written rows are final.
- Before that point:
  - `complete` means "the cluster says it's over, but some scans have no outcome";
  - `partial` means "a batch failed at dispatch; the rest may still be running".
- A run-level `error_message`, typically a dispatch failure, is shown whenever `status = 'failed'`, whichever rule applies.
- `completed_at` is ignored, because dispatch and every `partial` sweep stamp it.
- `reused_count` is never shown.
- This matches the status-polling rule that no consumer may read `complete` as `failed_count = 0` (`cyl-pipeline-status-polling/spec.md:382-383`).

### D4. Dialog content

- **Enumeration.** It uses `_enumerate`'s filters (`pipeline.py:125-203`), so N matches the trigger's count. `cyl_scans_extended` is owner-rights, and `bloom_user` has `USING (true)` on the base tables. Paging and chunking follow from `.in_()` URL length and the 8 s timeout. K and L come from one `select scan_id, max_source_id` per 200-id chunk.
- **Resolved params.** They are for display only. They mirror `resolve_params` today (whose alias map is empty) and are **throwaway**: once a server-side preview exists (#898), it replaces them. #897 must not extend them client-side.
- **K and L.**
  - K counts `max_source_id IS NOT NULL`. That includes manually ingested sources, the #900 case.
  - L counts NULL rows: legacy source-less traits, or (rarely, admin-only) traits all deleted. Hence "typically" in the copy. A successful run replaces L's traits in trait views.
- **"May skip".** Skipping is decided per stage on the cluster, against its own outputs.
- **Concurrent runs.** The query reads recent runs whose status is not `complete`/`failed` (created within 7 days, limit 20). It filters for incomplete counts in the client, because PostgREST can't compare two columns, then checks membership through the view. Each entry shows its counts-first label and age, never "in progress", because #706/#710 runs never settle.
- **Large runs** (N ≥ 500) need an acknowledgement, because runs can't be cancelled from Bloom.
- **Layout.** The dialog shows, in this order:
  1. headline;
  2. blockers;
  3. stage-in warning;
  4. concurrent runs;
  5. the one-line pre-check (details in a disclosure), or the all-results notice;
  6. collapsible params with caption;
  7. acknowledgement;
  8. confirm.

### D5. `cyl_pipeline_run_experiments` view

"Runs touching experiment X" needs the per-scan rows: `target_id` has no FK, and `scan_ids` runs have no target.

```sql
CREATE OR REPLACE VIEW public.cyl_pipeline_run_experiments WITH (security_invoker = on) AS
SELECT DISTINCT rs.run_id, w.experiment_id, r.created_at
FROM public.cyl_pipeline_run_scans rs
JOIN public.cyl_pipeline_runs r ON r.id = rs.run_id
JOIN public.cyl_scans s ON s.id = rs.scan_id
JOIN public.cyl_plants p ON p.id = s.plant_id
JOIN public.cyl_waves w ON w.id = p.wave_id
JOIN public.cyl_experiments e ON e.id = w.experiment_id;
```

- **Pushdown.** Filters on `experiment_id` and `run_id` push down under `security_invoker` as `bloom_user`, because every RLS qual here is `true` or a plain column test. The cost scales with the queried experiment's run-scan rows, not the table: about 100k rows, sub-second. Task 1.4 records `EXPLAIN ANALYZE` on seeded data.
- **Index follow-up.** `cyl_pipeline_runs` has no `(created_at desc, id desc)` index, so the list is a top-N sort. That is fine today; add the index when the table grows.
- **Why `lock_timeout = '5s'`.** The index's SHARE lock, and the rollback's ACCESS EXCLUSIVE, fail fast rather than queueing writers. `CONCURRENTLY` is impossible inside `db push`'s transaction.
- **Why REVOKE-first.** Default privileges grant writes on new relations, and which ones fire depends on the migrating role. On dev, `supabase_admin`'s defaults grant `arwdDxt` to anon/authenticated/service_role.
- **Why NOTIFY.** `NOTIFY pgrst` follows `20260916120000:41-49`, because deploy.yml never restarts `rest`. It runs after `COMMIT`, so it lands in `db push`'s next implicit transaction.
- **Idempotent.** The file must be fully re-runnable: CI applies migrations before pytest, and the tests re-apply the body.

- **Membership is live.** It follows *current* plant and wave assignments, so correcting a plant's `wave_id` or a wave's `experiment_id` moves that run's rows. Runs don't snapshot their experiments; nothing in v1 needs the historical assignment.
- **Soft-delete hiding is a UI filter, not an access boundary.** `bloom_user`/`bloom_agent` can still reach a deleted experiment's id through the base tables, and `bloom_writer` sees those runs through its own `cyl_experiments` policy.
- **Runs that never appear on a panel.** A zero-scan run has no scan rows, so it appears on no experiment panel, only in the global list. The same goes for a run whose scan-row insert failed after its run row was committed (the trigger isn't transactional).

Rejected alternatives:
- target-only matching, which misses every `scan_ids` run;
- a client-side join, which is an unbounded transfer.

### D6. Run → traits only through requested scans

- **Nothing links a `cyl_trait_sources` row to its run.** `provenance.pipeline_run_id` is NULL on every pipeline-written source (bloom#864; producer fix talmolab/sleap-roots#268).
  - `get_scan_traits(run_id_)` therefore returns nothing. It filters `s2.pipeline_run_id = run_id_` (`20260701000000:131`), and that value is `metadata ->> 'pipeline_run_id'` (`20260817130000:203-217`).
  - A source's `name` is that id when present, otherwise `sleap-roots:<idempotency_key>` (`20260917140000:125`). So today every name is the latter.
- **Even once populated,** write-back can deliver correct envelopes for unrequested scans (sleap-roots-pipeline#71). A run-keyed listing would present them as that run's output.
- **So links derive from `cyl_pipeline_run_scans` only.** The spec's guard bans the run-id matching surfaces in the UI code. It deliberately does **not** ban `cyl_trait_sources` or `param_hash`, which a later provenance or export view needs (D8).
- **Traits link.** It deep-links the existing traits page to the run's most common `(wave, age)` pair. The two values must come as a pair, because independent modes can name a combination no scan has. The URL seeds only the first load, because `TraitExplorer` resets wave and age on every trait change. When a pair has no data for the chosen trait, a visible note explains the fallback.
  - The page shows the whole experiment's scans at that wave and age, latest result per scan, and the link label says so.
  - The per-scan link goes to the scan's images page. No page shows one scan's trait values, so the drill-down shows `source_id` and whether it is current in trait views instead.

### D7. Selection, re-run, and failure hints

- **"Run this accession".** It uses every `plant.cyl_scans` id, via a pure helper called before the page's in-place sort. The grid renders only the first frame-1 scan per day.
- **Checkboxes** sit outside the `PlantScan` link, so selecting never navigates. The selection is per page.
- **Re-run gating.**
  - "Re-run failed" waits for settled header counts.
  - "Re-run scans without a result" is shown only when U > 0 on a `complete`/`failed` run, so it never duplicates "Re-run failed".
- **No-op false failure (bloom#900).** A re-run over a scan whose only source was ingested outside any run is reported `failed`, because the #875 fallback matches only sources that a run-scan row already carries (`20260917140000:163-191`).
  - The note is shown only when the row's `error_message` equals the poller's backstop text (`status_poller.py:238-241`, shared as a constant) **and** the scan currently has pipeline results.
  - It says re-running won't change this.
- **Requester names** are out of scope: `phenotypers` is invisible to `bloom_user` and holds scanner operators.

### D9. Batch the trigger's id filters; skip the preview for `{}` (bloom#901)

Large experiments must run as **one** run, from the UI and the API alike. The alternative, having the UI split a large target into several `scan_ids` runs, costs one rate-limit slot per chunk (5 per 60 s), scatters progress across runs, can strand half an experiment when a submission fails midway, and leaves API callers broken.

- **Batching.** Every `in.(…)` filter in the trigger is split by rendered length under a 4000-character budget, the same budget and algorithm as bloomctl's `id_batches` (bloom#674), and the results are merged. The helper is copied into `services/workflows/` rather than imported: the service doesn't depend on bloomctl, and a shared package would be heavier than the 20 lines it saves. A test reads bloomctl's module to pin the two budgets equal, so they can't drift apart silently.
- **Skip for `{}`.** Stored `param_hash` values are computed over resolved `{species, mode, age}`, so `compute_param_hash({})` can never match one, and the preview is guaranteed to return 0. Skipping both preview queries gives the identical result at no cost. Every UI request sends `{}` until #897 lands, so the UI never pays the preview's trait-row volume.
- **Spec impact.** The existing `cyl-pipeline-trigger` requirement says the preview is "a single batched query" whose query count doesn't grow with scan count. Batching by length changes that, so the requirement is MODIFIED in full. No active change touches that capability. The one existing unit test that runs the preview with `params: {}` (`test_dedup_preview_issues_one_batched_query_not_a_per_scan_loop`) moves to non-empty params.
- **Left open on #901.** For non-empty `params`, the preview still fetches every trait row. Fixing that needs a view or RPC, which is a migration, which the isolation lint would put in its own PR, and it only affects API callers today. It stays tracked there.

### D8. Forward compatibility with a per-param-set trait download (#865)

The end goal is downloading a scan set's traits as computed with a chosen parameter set, as a reproducible file. This is recorded on #865.

- **The unit of export is a scan set plus a source-selection rule,** not a run. Re-runs split one logical set across runs. A run is one convenient way to pick a scan set.
- **The reproducible identity is per source.** It is `cyl_trait_sources.metadata -> 'params'`/`param_hash` plus `predict_models`, reached through `cyl_pipeline_run_scans.source_id` or the latest source.
  - A run's `params` holds only the *requested overrides*, `{}` in v1.
  - Even after #897, the effective params are resolved per scan.
- **Guard scope.** The guard covers only the run-id matching surfaces, so a drill-down may later show the resolved params and model versions, or offer "Download traits", without amending it.
- **What v1 does now:**
  - The drill-down shows each row's `source_id` and whether it is current in trait views. Those are the handles a pinned export needs.
  - A header line shows the run's requested params: "from each scan's metadata (no overrides)" for `{}`.

## Risks / Trade-offs

- **Realtime as `bloom_user`.**
  - By config it works: Kong checks only the anon key, and Realtime's superuser DB role assumes the JWT role. No repo test proves it.
  - PR 3's post-deploy check and task 12.2 prove it, with a decoded role.
  - A 5000-row reconciliation burst may trip tenant limits; the coalesced resync absorbs it. Task 12.6 observes the effect on other widgets.
- **Large requests.** D9 fixes the URL limit, and removes the preview's trait-row volume for `{}`. Enumeration of a very large experiment (`cyl_scans_extended` by `experiment_id`, unpaged) and the 200 sequential enqueue RPCs of a 5000-scan run are unmeasured. Task 12.10 runs a large experiment on staging.
- **Contract gate.** bloom#895 must be satisfied before the §12 live runs, or every write-back fails. That is a precondition of §12.
- **Frozen runs.** #706/#710 runs get no UPDATEs. Elapsed time and "last scan update" make that visible.
- **Stale upstream text.** Design §10 and the roadmap still describe overrides and "N will run". Task 13.2 drafts the updates.

## Migration Plan

- Six PRs, merged in the order given in tasks.md. The hard dependencies are PR 3 on PR 1's view types, and PR 6 on PR 5's batching (large runs).
- The view and index are additive. The rollback drops both under a lock timeout.
