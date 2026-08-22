# cyl-pipeline-trigger Specification

## Purpose
TBD - created by archiving change add-cyl-pipeline-trigger. Update Purpose after archive.
## Requirements
### Requirement: `POST /workflows/pipeline` requires an authenticated caller and enforces the rate limit

The route SHALL require a valid Supabase session via the existing `require_supabase_user` dependency
(unchanged behavior — 401 on missing/invalid/expired token) and SHALL call the existing
`enforce_rate_limit(user_id)` before doing any other work, matching the manual-call convention
already used by the video route (not a FastAPI dependency). The route is externally reachable as
`POST /workflows/pipeline` through Caddy's prefix-stripping proxy, but MUST be registered internally
as `@app.post("/pipeline")` in `services/workflows/main.py` — matching every other route in this
service, none of which carry the `/workflows` prefix internally.

#### Scenario: Missing or invalid token is rejected

- **WHEN** `POST /workflows/pipeline` is called without an `Authorization` header, or with a token
  that Supabase's `/auth/v1/user` rejects
- **THEN** the route responds `401` and performs no enumeration, dedup, or write

#### Scenario: Rate limit is enforced before any work happens

- **WHEN** a caller has already exhausted their rate-limit window
- **THEN** the route responds `429` with a `Retry-After` header before running any enumeration query

### Requirement: Request validation for `target_level`/`target_id`/`scan_ids`

The route SHALL accept a JSON body of `{target_level, target_id, scan_ids, params}` where
`target_level` MUST be one of `"scan"|"wave"|"experiment"|"scan_ids"`. When `target_level` is
`"scan"`, `"wave"`, or `"experiment"`, `target_id` MUST be a positive integer and `scan_ids` MUST be
absent or `null`. When `target_level` is `"scan_ids"`, `target_id` MUST be `null` and `scan_ids` MUST
be a non-empty array of positive integers, capped at `MAX_SCAN_IDS` (5000) entries — an array longer
than that SHALL be rejected with `422` before any enumeration or database work. Duplicate entries in
`scan_ids` SHALL be deduplicated (order-preserving on first occurrence), not rejected. `params`' values
SHALL be validated for hashability before any other work: the route SHALL compute
`compute_param_hash(params)` once, up front, and SHALL reject with `422` if that raises
(`NonCanonicalizableError` for a non-finite float such as NaN/Infinity, `TypeError` for a
non-JSON-serializable value, or `RecursionError` for pathologically deep nesting) or if `params`'
serialized size exceeds `MAX_PARAMS_BYTES` (10,000 bytes). Any other combination, malformed JSON, or
wrong-typed field SHALL be rejected with `422`.

#### Scenario: scan_ids target with a populated list is accepted

- **WHEN** the body is `{"target_level": "scan_ids", "target_id": null, "scan_ids": [1, 2, 3],
  "params": {}}`
- **THEN** the route accepts the request and proceeds to enumeration

#### Scenario: scan_ids target with an empty list is rejected

- **WHEN** the body is `{"target_level": "scan_ids", "target_id": null, "scan_ids": [], "params":
  {}}`
- **THEN** the route responds `422` without writing any rows

#### Scenario: experiment target with a null target_id is rejected

- **WHEN** the body is `{"target_level": "experiment", "target_id": null, "params": {}}`
- **THEN** the route responds `422`

#### Scenario: scan_ids present alongside a non-scan_ids target_level is rejected

- **WHEN** the body is `{"target_level": "experiment", "target_id": 5, "scan_ids": [1, 2],
  "params": {}}`
- **THEN** the route responds `422`

#### Scenario: Wrong-typed target_id is rejected

- **WHEN** `target_level = "experiment"` and `target_id` is a string or a float
- **THEN** the route responds `422`

#### Scenario: Non-positive target_id is rejected

- **WHEN** `target_level = "experiment"` and `target_id` is `0` or a negative integer (not just
  negative — `0` is also excluded by "positive integer")
- **THEN** the route responds `422`

#### Scenario: Malformed JSON body is rejected

- **WHEN** the request body is not valid JSON at all
- **THEN** the route responds `422` without writing any rows

#### Scenario: Duplicate scan_ids are deduplicated, not rejected

- **WHEN** the body is `{"target_level": "scan_ids", "target_id": null, "scan_ids": [5, 5, 7],
  "params": {}}`
- **THEN** the route accepts the request and proceeds with exactly `[5, 7]` (order-preserving,
  duplicates removed) — `scan_count` is `2`, not `3`

#### Scenario: A scan_ids array longer than MAX_SCAN_IDS is rejected

- **WHEN** `target_level = "scan_ids"` and `scan_ids` contains more than 5000 entries
- **THEN** the route responds `422` without running any enumeration query or writing any rows

#### Scenario: A non-finite value in params is rejected

- **WHEN** `params` contains a NaN or Infinity float value (e.g. `{"age": NaN}` — accepted by the
  underlying JSON parser as a non-standard extension even though it is not valid per the JSON spec)
- **THEN** the route responds `422` without calling `app_client()` or running any enumeration query

#### Scenario: An oversized params object is rejected

- **WHEN** `params`' serialized JSON size exceeds `MAX_PARAMS_BYTES` (10,000 bytes)
- **THEN** the route responds `422` without calling `app_client()` or running any enumeration query

### Requirement: Enumeration resolves each target_level to a flat scan list via cyl_scans_extended

For `target_level = "scan"`, the route SHALL resolve to the single given scan (existence-checked).
For `"wave"`, it SHALL resolve to every scan whose `cyl_scans_extended.wave_id` matches `target_id`,
**ordered by `scan_id` ascending** so that two triggers of the same wave assign scans to the same
`batch_index` groups. For `"experiment"`, it SHALL resolve to every scan whose
`cyl_scans_extended.experiment_id` matches `target_id`, likewise ordered by `scan_id` ascending. For
`"scan_ids"`, it SHALL resolve to exactly the given (deduplicated) list in the caller's own order,
existence-checked against `cyl_scans_extended` — this branch does not depend on query row order at
all. A `target_id` (or any `scan_ids` entry) that does not exist SHALL cause a `404` and no rows
written. A `wave` or `experiment` `target_id` that exists but currently has zero scans is **not** an
error: the route SHALL proceed with an empty enumerated set (see the "zero scans" scenario under
Row-writing below).

#### Scenario: Scan target resolves the single given scan

- **WHEN** `target_level = "scan"` and `target_id` matches an existing scan
- **THEN** enumeration resolves to exactly that one scan id

#### Scenario: Wave target enumerates every scan in that wave

- **WHEN** `target_level = "wave"` and `target_id` matches a wave containing 12 scans across its
  experiment's plants
- **THEN** enumeration resolves to exactly those 12 scan ids

#### Scenario: Experiment target enumerates every scan in that experiment

- **WHEN** `target_level = "experiment"` and `target_id` matches an experiment containing 30 scans
  across its waves
- **THEN** enumeration resolves to exactly those 30 scan ids

#### Scenario: scan_ids target resolves exactly the given list

- **WHEN** `target_level = "scan_ids"` and `scan_ids = [4, 9, 15]`, all existing
- **THEN** enumeration resolves to exactly `[4, 9, 15]`

#### Scenario: An existing wave/experiment with zero scans is not an error

- **WHEN** `target_level = "wave"` (or `"experiment"`) and `target_id` matches a wave/experiment that
  currently has zero scans
- **THEN** the route responds `200` with `scan_count = 0`
- **AND** no `cyl_pipeline_run_scans` rows are written

#### Scenario: Unknown target_id is rejected

- **WHEN** `target_level = "experiment"` and `target_id` does not match any row in
  `cyl_scans_extended`
- **THEN** the route responds `404` and writes no `cyl_pipeline_runs`/`cyl_pipeline_run_scans` rows

#### Scenario: An unknown scan id inside scan_ids is rejected

- **WHEN** `target_level = "scan_ids"` and one entry in `scan_ids` does not exist in
  `cyl_scans_extended`
- **THEN** the route responds `404` and writes no rows for the request

#### Scenario: Wave/experiment enumeration order is stable across repeated triggers

- **WHEN** the same wave (or experiment) is triggered twice, and its underlying scan rows are not
  necessarily returned in the same order by the database on both occasions
- **THEN** both triggers assign the same scans to the same `batch_index` groups, because enumeration
  is explicitly ordered by `scan_id` ascending rather than relying on unordered query row order

### Requirement: Dedup preview is informational only — it never withholds a scan from enqueue

The route SHALL compute `compute_param_hash` (from the pinned `sleap-roots-contracts` package)
**once**, over the request's own `params` object exactly as supplied in the request body — this
route does not call `sleap_roots_contracts.resolve_params()` or otherwise derive `params` from a
scan's Bloom metadata; the caller is responsible for supplying whatever `params` value it wants
hashed, stored, and compared — and SHALL check whether that hash string equals
the already-stored `metadata->'params'->>'param_hash'` value on **any** of a scan's existing
`cyl_trait_sources` rows (reached via `cyl_scan_traits.scan_id → source_id → cyl_trait_sources.id` —
a scan can have more than one source, from successive computations). The route MUST NOT re-hash a
stored source's whole `metadata` column and compare that to the request hash — `metadata` is the
full Provenance envelope (code shas, container digests, workflow ids, etc., which differ on every
run even for identical params), so hashing it whole would essentially never match. Only the
pre-computed `param_hash` field already embedded in `metadata` at write-back time is compared,
string-to-string. **This check MUST cover all of a scan's sources, not only the most recent one.**
This deliberately does not reuse the `cyl-trait-read` capability's `is_latest` = `max(source_id)`
rule: that rule answers "which source should a read default to display," not "has this exact param
combination ever been computed for this scan" — those are different questions, and reusing
`is_latest` here would under-count `reused_count`, since an older source with matching params could
be missed whenever a newer source with *different* params also exists for the same scan (e.g. a scan
computed at `age=14` then later at `age=21`; a new request for `age=14` again must still be recognized
as a match even though `age=21` is now the latest source). A scan with at least one matching-params
source contributes to `reused_count` **exactly once**, regardless of how many of its sources match.
The check across all enumerated scans MUST be a single batched query (e.g. filtering
`cyl_scan_traits` by `scan_id IN (...)` for every enumerated scan at once), not a per-scan query loop.
**This check is informational only: it MUST NOT change the scan's initial `status` (always written as
`'queued'`) and MUST NOT exclude the scan from batching or enqueue.** Every enumerated scan is always
written and enqueued regardless of dedup-preview outcome — the real GPU-avoidance decision is made
cluster-side by the predict loop's existing per-scan skip-if-done check, which (unlike this Bloom-side
preview) knows the actual current model versions and code shas.

#### Scenario: A scan with any matching-params source contributes to reused_count but is still enqueued

- **WHEN** at least one of a scan's `cyl_trait_sources` rows has a stored `param_hash` equal to the
  request's `compute_param_hash`, regardless of whether it is that scan's most recent source
- **THEN** `reused_count` in the response includes that scan
- **AND** its `cyl_pipeline_run_scans` row is still written with `status = 'queued'`
- **AND** the scan is still included in a batch and enqueued

#### Scenario: An older matching source is found even when a newer source has different params

- **WHEN** a scan has an earlier source recorded with `params={age: 14}` and a more recent source
  (higher `source_id`) recorded with `params={age: 21}`, and the current request resolves to
  `params={age: 14}`
- **THEN** `reused_count` includes that scan (the earlier, non-latest source's match is still found)

#### Scenario: A scan with two matching sources still contributes only once

- **WHEN** a scan has two distinct `cyl_trait_sources` rows both recording the same `param_hash` as
  the current request (e.g. the same params were legitimately computed twice historically)
- **THEN** `reused_count` includes that scan exactly once, not twice

#### Scenario: The check is a single batched query, not a per-scan loop

- **WHEN** the dedup preview runs against two requests enumerating different scan counts (e.g. 3
  scans, then 30 scans)
- **THEN** the number of queries issued against `cyl_scan_traits`/`cyl_trait_sources` is the same in
  both cases — it does not scale with the number of enumerated scans

#### Scenario: A scan with no prior source does not contribute to reused_count

- **WHEN** a scan has no `cyl_trait_sources` row at all
- **THEN** `reused_count` does not include that scan
- **AND** its row is written with `status = 'queued'` and enqueued, same as any other scan

#### Scenario: A scan whose sources all used different params does not contribute to reused_count

- **WHEN** none of a scan's recorded sources' params (by `compute_param_hash`) match the current
  request's `params` as supplied
- **THEN** `reused_count` does not include that scan

#### Scenario: All enumerated scans have a matching source

- **WHEN** every enumerated scan for a request has at least one source matching the request's `params`
- **THEN** `reused_count = scan_count` in the response
- **AND** every scan is still written with `status = 'queued'` and still enqueued in batches — the
  run does **not** short-circuit to `complete` on the strength of this preview alone

### Requirement: Run and per-scan rows are written before any enqueue

The route SHALL insert one `cyl_pipeline_runs` row per request (capturing `target_level`,
`target_id`, `params` exactly as supplied in the request body, `requested_by`, `scan_count` = the
total enumerated scan count, and
`reused_count` = the dedup-preview count) and one `cyl_pipeline_run_scans` row per enumerated scan
(capturing `scan_id`, initial `status = 'queued'`) before any batch is enqueued, so that a crash
between writing rows and enqueuing leaves a durably queryable (if incomplete) record rather than
silently losing the request. A request that enumerates to zero scans (an existing but empty
wave/experiment) SHALL still write a `cyl_pipeline_runs` row with `scan_count = 0` and
`status = 'complete'` (there is nothing to do), and SHALL write zero `cyl_pipeline_run_scans` rows.

#### Scenario: Row counts match enumeration

- **WHEN** a request enumerates to 40 scans, of which 2 match a prior source's params
- **THEN** exactly 1 `cyl_pipeline_runs` row and 40 `cyl_pipeline_run_scans` rows are written
- **AND** the run row's `scan_count = 40` and `reused_count = 2`
- **AND** all 40 `cyl_pipeline_run_scans` rows have `status = 'queued'`

#### Scenario: A zero-scan request completes immediately with no scan rows

- **WHEN** a request's target enumerates to zero scans
- **THEN** exactly 1 `cyl_pipeline_runs` row is written with `scan_count = 0`, `status = 'complete'`
- **AND** zero `cyl_pipeline_run_scans` rows are written
- **AND** nothing is enqueued

### Requirement: Every enumerated scan is chunked into batches and each batch is enqueued

The route SHALL chunk all enumerated scans (regardless of dedup-preview outcome) into groups of at
most `BATCH_SIZE` scans, assign each group a `batch_index` (written onto each scan's
`cyl_pipeline_run_scans.batch_index`), and call `enqueue_cyl_pipeline_batch` once per batch with that
batch's scan id list.

#### Scenario: An experiment larger than BATCH_SIZE produces multiple uneven batches

- **WHEN** an experiment enumerates to 92 scans and `BATCH_SIZE = 25`
- **THEN** 4 batches are enqueued (25, 25, 25, 17), each scan's `batch_index` matching its batch

#### Scenario: A scan count that is an exact multiple of BATCH_SIZE produces no spurious empty batch

- **WHEN** an experiment enumerates to exactly 50 scans and `BATCH_SIZE = 25`
- **THEN** exactly 2 batches are enqueued (25, 25) — no third, empty batch is created

### Requirement: Response shape

On success, the route SHALL return `{pipeline_run_id, scan_count, reused_count}` with HTTP `200`.

#### Scenario: Successful trigger response

- **WHEN** a valid request is processed
- **THEN** the response body includes the new run's integer `pipeline_run_id`, the total
  `scan_count`, and `reused_count`

