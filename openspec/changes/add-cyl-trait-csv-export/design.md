## Context

**#976's reads** (on staging at `57242f36`; M2 = `supabase/migrations/20260930120200_add_cyl_trait_recipe_reads.sql`):

| Function | What it returns |
|---|---|
| `list_trait_recipes(experiment_ids_, scan_ids_)` (M2:108-161) | One row per recipe: `recipe_key`, `recipe_key_version`, `recipe_kind`, `definition`, `n_scans` (`count(DISTINCT scan_id)`), `newest_source_id` (the max) and `is_default`. The default is the highest `newest_source_id`, NULLs last (M2:142). Rows come in that order. |
| `get_trait_recipe_coverage(experiment_ids_, scan_ids_, recipe_key_)` (M2:165-231) | One row per selected scan, with status `included`, `no_traits`, `legacy_only` or `other_recipe`; the first match wins (M2:219-225), so a scan whose only recipe is `unattributed` is `legacy_only`. `source_id` is set only for `included` scans, and is NULL for `unattributed` (M2:213, 226). `available_recipes` is sorted `COLLATE "C"`. A NULL `recipe_key_` evaluates *the selection's* default (M2:198-204). |
| `get_experiment_traits(experiment_id_, source_id_, run_id_, recipe_key_, scan_ids_)` | Long rows, ordered `accessions.name, cyl_plants.id, cyl_scans.id, trait_name` (M2:308, 340). A NULL `recipe_key_` reads the latest source (`src.is_latest`, M2:299), which can mix recipes. With a key, it reads each scan's highest source of that recipe, through the `chosen` CTE over the presence function's per-scan max (M2:313-317, 91-93). `trait_value` is `src.value::float` (M2:287, 329) with no filtering. |

Behaviour common to all three:
- A NULL `scan_ids_` means the whole experiment (M2:297).
- Scans are reached through inner joins: experiments → waves → plants → accessions → scans (M2:53-62).
- They are SECURITY INVOKER, and EXECUTE is granted to `bloom_agent`, `bloom_user`, `bloom_admin` and `authenticated` (M2:357-364).
- They set no statement timeout. The 8 s limit per call is the `authenticator` role's `statement_timeout` (`20260921120000_refresh_cyl_experiment_trait_counts_incremental.sql:4`).

**Values are float4.** `cyl_scan_traits.value` is `REAL` (`20231113203010_create_cyl_scan_traits_table.sql:5`, never altered).
- The RPC widens it to float8.
- On the dev DB, float8 renders in JSON with 15 significant digits (`to_json(0.1::real::float8)` = `0.100000001490116`).
- Non-finite values render as the strings `"NaN"`, `"Infinity"` and `"-Infinity"`.

**The sidecar contract** is `_WIKI/SUPABASE/trait-recipes.md` §"Export sidecar v1" plus `trait-recipes.export.schema.json`, which has `additionalProperties: false` throughout. The page (`:8`) says the rules live in the specs.
- **What it fixes:** three files, and an experiment-grain stem `<experiment-slug>_<recipe_key[:8]>_<yyyymmdd>` (`:114`).
- **What's wrong with that stem:** the slug is undefined, and `[:8]` breaks non-hex keys (`legacy:12345` → `legacy:1`).
- **What it leaves open:** the `scan_ids_sha256` encoding, the `filters` shape, orderings, the CSV layout, and filtered and per-scan stems.
- **Stale notes:** its note for `selection.scan_ids` ("The `scan_ids_` passed, or null") can't hold for a batched exporter. The example sidecar has `scan_ids: null` beside `filters: {"plant_age_days": [7]}`.

This change amends all of these (D4–D6, task 8.1).

**Production** (read-only via bloomctl, 2026-09-30):
- Experiment 1 has 18,471 scans on 3,971 plants, in 26 waves of 449–1,443 scans. Ages are mostly 0, 2, 7, 9 and 13. No plant lacks an accession.
- `cyl_scan_traits_source` reads (4 columns) cost about 22 µs per row: 100 scans take 2.1 s and 300 take 6.4 s.
- Each scan has about 850–980 rows, so experiment 1 is about 17M rows.
- `list_experiment_trait_sources(1)` failed with `57014` at 8.28 s.

**Web and infrastructure:**
- **Auth.** `web/middleware.ts:76-88` lets every `/api` path through. `getSession()` (`web/lib/supabase/server.ts:59-70`) decodes the cookie without verifying it. The existing cyl routes are safe only because they forward the token to a service that verifies it. `auth.getUser(jwt)` verifies a token against GoTrue.
- **Sessions.** The browser client (`createBrowserClient`) stores its session in cookies, so a browser `refreshSession()` updates what the server reads. `JWT_EXPIRY` is 3600 (`.env.staging.defaults:93`, `.env.prod.defaults:100`).
- **The `bloom-web` service** (`docker-compose.prod.yml`) is one `next start` process (`web/Dockerfile.bloom-web.prod` runs `npm run start` on `node:20-alpine`). It has no `mem_limit` and no `restart:` policy. Next 16's process handlers only log unhandled rejections.
- **PostgREST** sets no `PGRST_DB_POOL` (default 10 connections). It may keep running a statement after its client disconnects, up to the 8 s limit.
- **Proxies.** Caddy proxies `/api/cyl/*` with no timeouts (`caddy/Caddyfile:129-131`). Kong's default 60 s applies per request (`volumes/api/kong.yml:115-133` sets none).
- **Browsers** drop a response that sends no bytes for too long; Firefox's `network.http.response.timeout` is 300 s.
- **Library versions:** supabase-js 2.106.2 (the `.abortSignal()` builder method and the `accessToken` client option), next 16.3.4, and fflate 0.8.3 (current at review).

## Goals / Non-Goals

**Goals**
- One recipe per file, never mixed, with every selected scan accounted for.
- The same CSV, excluded CSV and sidecar bytes for the same data (with `generated_at` fixed), whatever the batch size or the order calls complete in.
- Experiment 1 exports without breaking the 8 s per-call limit or starving other PostgREST users.
- Values that round-trip exactly to what Bloom stores.
- A CSV that sleap-roots-analyze and bloommcp read correctly, with the loader arguments documented.

**Non-goals.** See the proposal's "Out of scope".

## Decisions

### D1. An in-process export job

The CSV header needs every trait name, so nothing can be sent until every read finishes. For experiment 1 that means minutes of silence, which a single request would lose to browser idle limits. So an export runs as a job inside bloom-web:

| Route | Response |
|---|---|
| `POST /api/cyl/trait-export/jobs` (query-string parameters, empty body) | `202 {job_id}` |
| `GET /api/cyl/trait-export/jobs/[jobId]` | `{status, phase, done, total, detail?, filename?}`, from a fixed field whitelist. `status` is `running`, `ready`, `failed` or `cancelled`. |
| `GET /api/cyl/trait-export/jobs/[jobId]/download` | The zip when `ready`, streamed from the stored chunks with `Content-Length`. `409` with a `detail` when the job is `running`, `failed` or `cancelled`. |
| `DELETE /api/cyl/trait-export/jobs/[jobId]` | `204`. Cancels a running job, or drops a finished one and its zip. |

- **Job ids** come from `crypto.randomUUID()`. A `[jobId]` that isn't a UUID, is unknown or expired, belongs to another user, or was lost to a restart returns `404`. The UUID shape is checked before any lookup.
- **State.** The job registry, slot counters and PostgREST semaphore live on `globalThis[Symbol.for("bloom.cylTraitExport")]`, so every route module (and a dev reload) shares one instance. Next uses the same pattern in `process-error-handlers`.
- **Retention.** A finished job (any terminal status) is kept for `RETAIN_SECONDS` = 600 s.
  - Lookups treat `now − finished_at > RETAIN_SECONDS` as absent, so expiry is exact. An `unref`'d sweep only frees memory.
  - A ready zip can be downloaded any number of times until it expires, its owner deletes it, or its owner's next job is accepted.
  - Each user holds at most one finished job: when their `POST` returns `202`, their previous finished job is dropped. A refused `POST` drops nothing.
- **Memory budget.** Held bytes are the sizes of unexpired ready zips plus `RUNNING_JOB_RESERVE_BYTES` = 256 MB for each running job. A start is refused with `429` "server busy" if held bytes (minus the caller's own finished job) plus one reserve would exceed `MAX_HELD_BYTES` = 768 MB.
- **Lost job ids.** A per-user `429` carries the owner's running `job_id`. If a `202` was lost, or the tab closed, the dialog can then resume or cancel that job instead of waiting out its deadline.
- **Restarts** lose all jobs. The dialog reports a `404` during polling as "the export was interrupted".
- **Why not a durable job.** It would need a table, pgmq, a worker, a bucket, and `bloom_workflows` grants on the RPCs and every table they read. That stays a follow-up.
- **The single-process assumption** is stated in `web/README.md`. A second replica needs the durable job (task 12.3 drafts a compose note).

### D2. Reads, batching and integrity

**Limits** (constants in `web/lib/cyl-trait-export/limits.ts`):

| Constant | Value | Meaning |
|---|---|---|
| `BATCH_SCANS` | 100 (task 7.3) | Scans per coverage or trait call |
| `LISTING_BATCH_SCANS` | 20,000 (task 7.4) | Scans per `list_trait_recipes` call. A listing is one HTTP request under Kong's 60 s, so it is listed in as few calls as possible; 20,000 covers the largest experiment (experiment 1, 18,471 scans) |
| `PG_CONCURRENCY` | 3 | Size of one FIFO semaphore that every PostgREST call from the feature passes through (`rpc` and `from`, across jobs and listings) |
| `MAX_RUNNING_JOBS` | 2 | Running jobs, all users |
| `MAX_JOBS_PER_USER` | 1 | Running jobs per user |

**How the semaphore behaves:**
- It leaves 7 of PostgREST's 10 connections for everyone else.
- Each job and each listing keeps at most `PG_CONCURRENCY` calls outstanding (in flight or queued), so they interleave and one job can't queue ahead of everything.
- An acquire can be aborted, which removes the waiter from the queue.
- An aborted call keeps its slot until 9 s after it was issued, because PostgREST may still be running its statement.

**Steps:**

1. **Selection.**
   - For a scan, resolve its `experiment_id` from `cyl_scans_extended`, then check that the experiment is visible (D7).
   - Read `cyl_scans_extended` for the experiment (with `wave_number = W` and/or `plant_age_days = A` when given, or for the one scan). Page by keyset: `scan_id > last`, ordered, 1,000 rows per page. Paging stops only on an empty page, so a server row cap can't end it early.
   - One `count: "exact", head: true` read of the same query must then equal `|S|`, and `S` must be strictly ascending with no repeats. Otherwise the result is "the selection changed during the export; retry".
   - The view inner-joins `species`, so "a whole experiment" means the experiment's scans that have a species.
   - `genotype` comes from `accessions` (`id, name`), read in chunks of accession ids.
2. **Batches.**
   - `S` is cut into `ceil(|S| / BATCH_SCANS)` consecutive chunks numbered from 1 for coverage and traits, and into `ceil(|S| / LISTING_BATCH_SCANS)` chunks for the recipe listing. That numbering is "batch i of n" in every `detail`.
   - Every RPC call passes `experiment_ids_ = [e]` (or `experiment_id_ = e`) and a non-empty `scan_ids_` array, because a NULL `scan_ids_` would read the whole experiment.
   - Nothing is retried.
3. **Recipes.** Call `list_trait_recipes` per listing chunk (one chunk for every current selection) and merge per key:
   - `n_scans` is summed;
   - `newest_source_id` is the max;
   - `recipe_key_version`, `recipe_kind` and `definition` come from the chunk holding the max (for `unattributed`, whose id is NULL, from any chunk);
   - rows are ordered by `newest_source_id DESC NULLS LAST`, and the first row is the default.

   Why the merge equals one call over `S`: presence depends only on the scan (M2:36-106), chunks are disjoint, a source row fixes its version and definition, and only `unattributed` has a NULL id. Task 1.2a checks this against the real functions for chunk sizes 1, 2 and `|S|`. A chunk whose `n_scans` exceeds its size is an error.
4. **Coverage.** Call `get_trait_recipe_coverage([e], chunk, K)` per chunk, **always with an explicit `K`**.
5. **Traits.** Call `get_experiment_traits(e, recipe_key_ := K, scan_ids_ := <the chunk's included scans>)` with `select=scan_id,trait_name,source_id,trait_value` and `count: "exact"`.
   - The rows returned must equal the count. A shortfall means a truncated response.
   - A chunk with no included scans makes no call.
6. **Integrity checks.** Each failure is a typed error that fails the job:
   - (a) every id in `S` has exactly one coverage row, and no coverage row falls outside its chunk;
   - (b) the `included` count equals the merged `n_scans` for `K`;
   - (c) every `included` scan returns at least one trait row;
   - (d) each row's `source_id` IS NOT DISTINCT FROM its scan's coverage `source_id`;
   - (e) no `(scan_id, trait_name)` pair repeats;
   - (f) every trait row's scan is one of the `scan_ids_` passed to that call;
   - (g) no trait name equals one of the 24 fixed column names;
   - (h) every `trait_value` is a number, `null`, `"NaN"`, `"Infinity"` or `"-Infinity"`;
   - (i) the sidecar builder asserts that `included.n_scans + excluded.length == |S|`, and that together they cover exactly `S`;
   - (j) every non-NULL `accession_id` in `S` returns exactly one `accessions` row;
   - (k) every included source id returns exactly one `cyl_trait_sources` row.

   Checks (b) and (d) mean the data changed during the export, so their `detail` says "the selection changed during the export; retry". They catch a new source of `K` arriving on a scan that coverage marked `included`. A source arriving on an excluded scan after its coverage read is not caught; the file still agrees with the coverage it records.
7. **`observed`.** Read `cyl_trait_sources.metadata` for the distinct included source ids, in chunks. `bloom_user` can read it (`20260506000001_bloom_role_rls_policies.sql:172`).

**Completion order.** Calls can complete in any order, so every structure built from them is sorted explicitly before output: the pivot, the trait union, the source ids and `observed`. Nothing relies on arrival order or on M2's `ORDER BY`.

**Memory and the event loop.**
- The pivot keeps, per scan, a `Uint32Array` of trait indexes, a `Float64Array` of values and a `Uint8Array` marking NULL, all off the V8 heap. That is about 17M × 13 B ≈ 220 MB for experiment 1.
- The CSV is generated in fresh `TextEncoder` slices of about 50,000 cells and pushed into fflate's synchronous `ZipDeflate`, with `await new Promise(setImmediate)` between slices.
- That avoids worker threads and unmeasurable queues, keeps each slice's CPU time to tens of milliseconds, and keeps the fflate code bundler-safe.
- The code uses only Node 20 APIs.

**Robustness.**
- **Captured inputs.** The job's inputs are captured before the `202`: the verified user id, the access token, the token's `exp`, and the parameters. The job never calls `cookies()`, `getSession()` or `createServerSupabaseClient`.
- **Client.** The job's Supabase client uses the `accessToken` option with the captured token; it has no auth session. The token and client are dropped when the job ends.
- **Cleanup.** The job body is wrapped in `try/catch/finally`. `finally` sets a terminal status and releases the job slot. Any unexpected error becomes `failed` with a generic `detail`.
- **Deadline.** An independent timer fires at the deadline (D7) and settles the job even if its promise never does: it sets `failed` with "the export took too long", releases the slot, and aborts.
- **Abort.** Every PostgREST call gets `.abortSignal(signal)`. Work before the `202` also aborts on `request.signal`.
- **No `after()`.** Next waits for pending `after()` work on SIGTERM, so jobs don't use it.

### D3. Choosing the recipe

- **The listing route.** `GET /api/cyl/trait-export/recipes` runs D2 steps 1–3 and returns `n_selected` plus the merged rows.
  - An empty selection of a visible experiment returns `200` with `n_selected: 0` and no rows.
  - Each user has at most one listing in flight; a newer listing aborts the older one. A listing also aborts on `request.signal`.
- **Choosing.** The job request carries an explicit `recipe` and `chosen` (`default` or `user`). The job re-lists, and records `recipe.chosen_by = "default"` only when `chosen` is `default` **and** `K` is still the merged default; otherwise it records `user`.
- **A `K` absent from the merged listing** fails the job with "this recipe is not in the selection", and no coverage call is made. The dialog prevents this; it can only arise from a direct request or a race.

### D4. File conventions

**Where the conventions live.** After task 8.1, `trait-recipes.md` §"Export sidecar v1" states these conventions in full for every exporter, and the `cyl-trait-export` spec binds the web to them. D4–D6 record why.

**Interchangeable.** Every exporter follows D4–D6, so two exporters' files for the same data are **interchangeable**. That means:
- the same columns and order;
- the same cell values (a finite value is the stored float4);
- the same row and list orders;
- the same `scan_ids_sha256`.

They may differ only in the sidecar's JSON formatting (whitespace and key order).

**CSV columns, in order:**
1. The 22 metadata columns of bloomctl `cyl download`'s `scans.csv` (`_COLUMNS`, `bloomcli/src/bloomctl/cyl/download.py:104-127`; `CSV_COLUMNS` at 129), from the same sources:
   - the `cyl_scans_extended` fields;
   - `genotype` = `accessions.name`;
   - `scan_path` = `images/Wave<wave_number or 0>/Day<plant_age_days>_<date_scanned>/<qr_code>`, each part through bloomctl's `safe_component` (`bloomcli/src/bloomctl/_download.py:74-89`) with Python `str()` semantics, so a NULL age renders `None`.

   `scan_path` names bloomctl's image layout, so the file joins a `cyl download` directory. It is not a path inside the zip.
2. `recipe_key` (in full), then `source_id`.
3. The trait names present in the included rows, sorted by Unicode code point. JavaScript compares code points explicitly; its default sort uses UTF-16 code units, which differ above U+FFFF.

**A per-scan file** has its own scan's trait columns. It agrees with the experiment file by column name, not by header (user decision, 2026-09-30).

**Rows:** one per included scan, in ascending numeric `scan_id`.

**Cells:**
- **Finite values.** Every value is a stored float4 (Context). A finite value is written as **the shortest decimal that parses back to the same float4** (user decision, 2026-09-30): `0.1`, never `0.10000000149011612`.
  - The web takes `f = Math.fround(v)` of the parsed JSON number, finds the smallest `p` in 1…9 with `Math.fround(Number(f.toPrecision(p))) === f`, and writes `String(Number(f.toPrecision(p)))`. The `String(Number(…))` step turns `9e+1` into `90`. `-0` is written `0`.
  - Fifteen digits from PostgREST is well above float4's 9, so this is exact however many digits are sent.
  - Python's `numpy.float32` repr gives the same value.
- **Non-finite values** are written `NaN`, `Infinity` and `-Infinity`.
- **Empty cells:** a NULL value, a trait the scan lacks, a metadata NULL, and `source_id` under `unattributed`.

**Format:**
- RFC 4180 quoting, applied only to cells containing `,`, `"`, CR or LF.
- CRLF after every line, including the last, as Python's `csv` writes.
- UTF-8 with no BOM.
- No comment lines. A `#` line either raises in pandas or, if it contains a comma, silently becomes the header.

**No spreadsheet escaping.** A cell starting `=`, `+`, `-` or `@` is written as-is (Risks).

**`recipe_key` is written in full.** A 64-hex key is all digits with probability about (10/16)^64, and `legacy:N` and `unattributed` never parse as numbers. A shortened key could parse, and pandas would count it as a numeric trait. sleap-roots-analyze drops `source_id` by its `_id` rule (`data_cleanup.py:146-154`).

### D5. The sidecar and the excluded CSV

**Generator fields.**
- `generated_at` is `Date#toISOString()`: UTC, with milliseconds and `Z`.
- `generated_by` is `{tool: "bloom-web", version: <package.json version>[+<BLOOM_WEB_BUILD_SHA>]}` (D9).

**`selection`:**
- `experiment_ids` is `[e]`.
- `scan_ids` is `null` for an unfiltered experiment. Otherwise it is the full ascending `S`: the filtered scans, or `[s]` for a single scan.
- `filters` is `{}`, or holds `wave_number`, `plant_age_days` or `scan_id` with integer values (including 0). Other exporters may add keys named for their own selectors, with integer or integer-array values; the schema allows any object.
- `scan_ids_sha256` is the lowercase hex sha256 of the UTF-8 bytes of the ascending decimal ids joined by `,` (`3,7,12`).

**`included`:**
- `n_scans` is the count of `included` rows.
- `source_ids` is the distinct non-NULL coverage `source_id`s, ascending. It is `[]` for `unattributed`, and `[5]` for `legacy:5` however many scans that source covers.

**`excluded`** is exactly the non-`included` coverage rows, in ascending `scan_id`, with `reason` equal to their status and `available_recipes` as M2 returns it.

**`other_recipes_in_selection`** is `{recipe_key, n_scans}` of each merged row except `K`, in listing order.

**`recipe.observed`** is present only when `recipe_kind` is `pipeline`, with all six arrays, possibly empty.
- Each array holds distinct values sorted by code point.
- A source missing a key contributes nothing.
- `inference_configs` objects are deduplicated and sorted by their canonical JSON (keys sorted by code point recursively, no whitespace).

**Serialization (the web's formatting choice):**
- the free-form objects `definition` and each `inference_configs` item have their keys sorted recursively;
- the sidecar's own keys follow schema order;
- 2-space indent, and a trailing LF.

**`<stem>.excluded.csv`:**
- Its header is the literal `scan_id,plant_qr_code,reason,available_recipes`.
- `available_recipes` is joined by `;`. No key form contains `;`, and an empty list is an empty cell.
- It follows D4's format.
- It is header-only when nothing is excluded. The web always writes it.

**The example sidecar** gets scalar `filters`, the full `scan_ids`, and a real `scan_ids_sha256` computed from them (task 8.1).

### D6. The stem

The stem is `<slug>[_wave<W>][_day<A>][_scan<id>]_<keyseg>_<yyyymmdd>`. An exporter whose filters have no segment here may omit them; the sidecar records them.

- **`<slug>`:** replace each run of characters outside `[A-Za-z0-9-]` in the experiment name with `-`, lowercase, cut to 60 characters, then trim `-` from both ends. Use `experiment-<id>` if nothing remains.
- **`<keyseg>`:**

  | Key | `<keyseg>` |
  |---|---|
  | 64-hex | first 8 characters |
  | `legacy:N` | `legacy-N`, never truncated |
  | `unattributed` | `unattributed` |
- **`<yyyymmdd>`:** the UTC date of `generated_at`, from the same clock reading.

The zip is `<stem>.zip`. The stem is checked against `^[a-z0-9_-]+$` before it goes into `Content-Disposition`.

### D7. Auth, validation, limits and errors

**Identity.** A route reads the cookie session, captures its `access_token` once, and verifies that exact string with `auth.getUser(token)` against GoTrue. It then decodes the same string for `exp` and asserts `sub === user.id`.
- GoTrue rejecting the token returns `401`.
- A GoTrue error or outage returns `503` "sign-in service unavailable".
- `getClaims` is not used, because with asymmetric keys it verifies locally.

**Job start (`POST`) checks, in order.** No refusal before step 6 makes a PostgREST call.
1. **Same origin.** If `Sec-Fetch-Site` is present, it must be `same-origin`, or `403`.
   - Browsers omit `Origin` on same-origin GETs, so one header check serves every route here. Page JavaScript can't set it.
   - Browsers too old to send it are unguarded, which is accepted.
2. **Session.** A verified session (above), or `401`/`503`.
3. **Time left.** The verified token's `exp` must leave at least `MIN_SESSION_SECONDS` = 1,800 s, or `401` with `detail` "session expires too soon". The dialog refreshes the session and retries once.
4. **Parameters**, or `422`:
   - exactly one of `experiment` and `scan`, each a positive integer;
   - `wave` and `age` are non-negative integers, allowed only with `experiment`;
   - `recipe` matches `^([0-9a-f]{64}|legacy:[0-9]+|unattributed)$`;
   - `chosen` is `default` or `user`;
   - every integer is written `0` or `[1-9][0-9]*`, at most 15 digits;
   - no parameter is repeated.
5. **Limits.** `MAX_JOBS_PER_USER`, `MAX_RUNNING_JOBS` and `MAX_HELD_BYTES` (D1), or `429`. A per-user `429` carries the owner's running `job_id`. The slot is taken here, and released however the job ends, including a refusal at step 6.
6. **Visibility and selection.** The experiment must be visible to the user in `cyl_experiments` through RLS (for a scan, the scan's experiment), and `S` must be non-empty, or `404`. `bloom_user` can't see `deleted_at` rows (`20260506000001:82`), while `cyl_scans_extended` bypasses RLS.
   - D2 step 1 runs here, before the `202`.
   - A count mismatch or repeated id returns `409` with "the selection changed during the export; retry".
   - A PostgREST error returns `502` with a fixed `detail`.

**The other routes:**
- **Listing:** checks 1, 2, 4 (without the `recipe` and `chosen` parameters) and 6, with no session floor and no job limits. An empty selection returns `200 {n_selected: 0, rows: []}`. Otherwise it gives the same `409` and `502`, including for a failed listing batch.
- **Status, download and delete:** checks 1 and 2, the UUID check, and ownership against the verified user (`404`).
- **`HEAD`:** every route exports it returning `405`; otherwise Next runs `GET` for a `HEAD` (`app-route/helpers/auto-implement-methods`).

**Deadline.** `min(EXPORT_MAX_SECONDS = 1500, token seconds left − 60)`. With the 1,800 s floor the first term always wins; the second is kept as a guard.

**`detail`.** Every `detail` is a fixed message, and never contains PostgREST's `message`, `details` or `hint`; those go to the server log only.
- An RPC error names its code and "batch i of n", for example "a trait read timed out (57014) in batch 12 of 185; try a wave or age filter".
- An integrity failure names the check and, where it applies, the scan.
- These codes have their own messages instead:
  - `PGRST202`: "export not available yet";
  - `PGRST301`/`PGRST303`: "session expired; sign in again".

**Polling.** The dialog polls every 2 s for the first minute, then every 5 s. Each poll costs two GoTrue `/user` calls, one in the middleware and one in the route. With at most 2 running jobs that is about 1 per second, which is acceptable.

### D8. The dialog

- **Buttons.** `TraitExportButton` opens `TraitExportDialog`, an MUI Dialog.
  - On the traits page the button is disabled until `TraitExplorer` has loaded its waves and ages. `waveNumber` and `plantAge` start at `0` and are set from the loaded selection (`TraitExplorer.tsx:55-56, 99-101`).
  - The filters are prefilled from that state only when the loaded lists contain the value; otherwise they use "All". `0` is a real value.
- **Listing.**
  - The dialog refreshes the session and fetches the listing when it opens, and on each filter change (debounced 500 ms). A stale response is discarded.
  - Each recipe shows its `<keyseg>`, kind and "N of M scans". The default is preselected and labelled.
  - `n_selected: 0` shows "No scans match this wave and age". An empty recipe list shows "No trait results for this selection". Both disable Download.
- **Download:**
  1. `refreshSession()`; if it fails, stop with a sign-in message.
  2. `POST` the job. On a per-user `429`, offer to resume or cancel the returned job.
  3. Poll, showing "Reading batch d of n".
  4. On `ready`, fetch, `blob()`, and save by object URL.
- **Errors:**
  - A `detail` is shown with Retry.
  - A non-JSON body or a rejected `blob()` shows a generic message and saves nothing.
  - A `404` while polling says "the export was interrupted (the server restarted); please retry".
- **Closing** sends `DELETE` and stops polling. No state is updated after close.
- **Help.** "What's in this file?" links to the "Using a trait export" section of `trait-recipes.md` on GitHub's `main` branch, which has it only after promotion to main (noted in PR B).

### D9. `generated_by.version`

The web image has no build identity: `web/package.json` is `1.0.0`, and the compose build passes no SHA. v1 writes `1.0.0`, plus `+<BLOOM_WEB_BUILD_SHA>` when that is set. Data reproducibility rests on the recipe key and source ids.

### D10. Fixtures and verification

**Location.** `web/lib/cyl-trait-export/__fixtures__/`, which Vitest excludes (`web/vitest.config.ts:75`).
- The directory is marked `-text` by a line appended **after** the `*.csv`/`*.json text eol=lf` lines in `.gitattributes`. That line is committed before any fixture is staged.
- The directory is excluded from prettier and the whitespace hooks.

**`scan-metadata-parity.json`:**
- Anonymised rows shaped like a staging `cyl_scans_extended` select.
- The expected string cells come once from bloomctl's `write_scans_csv`, and the provenance is recorded.
- A row with `\0` is marked synthetic, because a PostgreSQL `text` value can't contain it.

**`golden/input.json`,** in a fixture id space, holds:
- the experiment row, each scan's `cyl_scans_extended` row, and each genotype;
- source rows: `id`, `name`, `scan_id`, `recipe_key_version` and `metadata`, with each `recipe_key` computed by `cyl_trait_recipe_key_v1` on a migrated DB (recorded);
- for each of `K`, `K2`, `legacy:9` and `unattributed`, the single-call coverage and trait rows;
- `chunk_listings[size]` for sizes 1, 2 and `|S|`, each a list of `{scan_ids, rows}` in chunk order;
- `selection_listings` for `wave=2` and `age=0`.

Every value is float4-representable.

**The fake client** holds no M2 logic. It:
- serves a listing only when the call's `scan_ids_` exactly equals a recorded `scan_ids`, and throws otherwise;
- serves coverage and trait rows by filtering the single-call rows to the chunk;
- can resolve calls in reverse or seeded random order, and can shuffle rows.

**Golden outputs** (`golden/{K,legacy-9,unattributed}.{csv,export.json,excluded.csv}`) are hand-written from D4–D6 before any implementation, and never regenerated from the code. A root unit test checks every golden sidecar against the schema: required keys, and no undeclared properties.

**The integration test** (task 1.2a) seeds `input.json` verbatim: QR codes, waves, ages, dates and plants, including one plant with two scans. It runs **before** the goldens are written. Under an order-preserving map from fixture ids to seeded ids (covering `legacy:<id>` strings, `definition` ids and `available_recipes`), it asserts:
- the computed keys;
- the single-call coverage and trait rows, compared as PostgreSQL's own `json_agg` rendering;
- every recorded chunk and selection listing;
- that per-chunk merges equal single calls.

It runs the RPCs as `bloom_user` (`SET LOCAL ROLE`), as `test_cyl_trait_recipes_read.py:496-510` does. It runs in CI's required compose job, and it is the check that ties the web fixtures to M2.

**Verification, not committed tests** (tasks 1.4, 10.1). A scratchpad script in bloommcp's environment validates the sidecars with `jsonschema`, loads the CSVs with sleap-roots-analyze's `load_trait_data`, and runs bloommcp's `qc_clean`. It records the versions and the results. `csv_content` is capped at 5 MiB (`bloommcp/src/bloom_mcp/tools/_inline_input.py:31`), so the `qc_clean` step uses a filtered export.

### D11. Where code lives

- New code lives under `web/lib/cyl-trait-export/`, `web/components/cyl-trait-export/` and `web/app/api/cyl/trait-export/`. It is outside the `cyl-pipeline-ui` guard directories, and imports nothing from them.
- The scan-page button is `ScanTraitExportButton.tsx` beside `page.tsx`, so the page gains one line and the conflict with #965 stays small.

### D12. Forward compatibility with a predictions download

This is not built here.
- **Where predictions live.** Predictions are stored per `(source_id, scan_id, kind, root_type)` in `cyl_scan_intermediates` (`supabase/migrations/20260625120000_create_cyl_scan_intermediates.sql:32-45`, `kind` = `predictions_slp`).
- **How they join an export.** Every pipeline-recipe row carries `(scan_id, source_id)`, so the predictions behind an export are the intermediates rows with those pairs: same models, same code. Legacy and unattributed recipes have none.
- **What a later download may do.** It adds its own files (a `predictions/` folder and a `<stem>.predictions.json` manifest), or it is a bloomctl command that reads an export. It never changes sidecar v1.

## Risks / Trade-offs

- **Measured costs (staging, 2026-10-01, after #992).** `get_experiment_traits` p95 is 1.51 s at 50 scans, 2.28 s at 100 and 4.30 s at 200, so `BATCH_SCANS` is 100 (task 7.3). Experiment 1's job is about 121 s at p50 (163 s at p95), within `EXPORT_MAX_SECONDS`.
  - **The listing.** At `BATCH_SCANS`, experiment 1's listing is 185 calls: about 65 s with one job running and 123 s with two, over Kong's 60 s. One `list_trait_recipes` call over all 18,471 scans took 1.26 s (largest age, 3,819 scans: 0.45 s), so listings use `LISTING_BATCH_SCANS` (task 7.4) and stay in the route rather than moving into the job.
  - If a later experiment's job would exceed `EXPORT_MAX_SECONDS`, work stops and the durable job goes back to the user.
- **A restart loses jobs.** `bloom-web` has no `restart:` policy, so an out-of-memory crash leaves the site down. Task 12.3 drafts an infra issue: `restart: unless-stopped`, a `mem_limit`, and a single-replica comment.
- **Memory** is bounded by `MAX_HELD_BYTES` (running reserves plus held zips) and at most 2 running jobs. Peak RSS under `next start` is recorded in task 10.2.
- **sleap-roots-analyze's `get_trait_columns`** drops any column whose name contains `index`, `date`, `time`, `day_`, `scan_` and similar (`data_cleanup.py:115-141`), so `curve_index` is dropped. Task 1.4 records this, the wiki (task 8.2) tells users to pass their trait columns explicitly, and task 12.3 drafts an upstream issue.
- **Spreadsheets and pandas:**
  - A cell starting `=`, `+`, `-` or `@` can run as a formula in Excel.
  - Without a BOM, Excel may mis-decode non-ASCII names.
  - pandas reads a genotype named `NA` or `None` as NaN unless `keep_default_na=False` is passed.

  All three are accepted for fidelity, and documented in task 8.2.
- **Precision.** Values are float4, so comparisons with float8 sources (such as #865's reference file) need float32 rounding (task 10.1).
- **The run stamp is best-effort** (#976 D3). The export never uses it.

## Open Questions

1. Should the deploy pass a git SHA into `bloom-web` (`BLOOM_WEB_BUILD_SHA`) so the sidecar names the build? That is a compose or deploy change, outside this one.
2. Should `TraitExplorer` move off `get_scan_traits` onto recipe reads? That would be a separate change.
