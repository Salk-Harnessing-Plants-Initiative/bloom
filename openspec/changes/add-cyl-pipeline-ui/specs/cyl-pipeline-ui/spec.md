## ADDED Requirements

### Requirement: Trigger proxy `POST /api/cyl/pipeline` rejects cross-origin and unauthenticated calls before any other work
The route handler SHALL apply these checks in order, before reading the body and before contacting upstream:
1. **Media type:** parsed from `Content-Type` by splitting on `;`, trimming and lowercasing, it SHALL be `application/json`. Otherwise the handler responds `415`.
2. **Origin:** an `Origin` header SHALL be present and not `null`. Its host, including any port, SHALL equal case-insensitively the first comma-separated value of `x-forwarded-host` if present, otherwise `host`. Otherwise the handler responds `403`.
3. **Session:** `getSession()` SHALL return an `access_token`. Otherwise the handler responds `401`.

#### Scenario: Non-JSON post is refused
- **WHEN** a request arrives with `Content-Type: text/plain`, or `text/plain; application/json`, and a valid session cookie
- **THEN** the handler responds `415`, never reads the body, and makes no upstream request

#### Scenario: JSON with parameters is accepted as JSON
- **WHEN** a same-origin signed-in request has `Content-Type: application/json; charset=utf-8`
- **THEN** it passes the media-type check

#### Scenario: Missing, null or foreign Origin is refused
- **WHEN** a JSON request has no `Origin`, or `Origin: null`, or `Origin: https://evil.salk.edu` while `Host` is `bloom.salk.edu`
- **THEN** the handler responds `403` and makes no upstream request

#### Scenario: Port is part of the comparison
- **WHEN** `Host` is `staging.bloom.salk.edu:8443` and `Origin` is `https://staging.bloom.salk.edu:8443`
- **THEN** the Origin check passes

#### Scenario: Unauthenticated same-origin call is refused
- **WHEN** a same-origin JSON request arrives with no session
- **THEN** the handler responds `401` and makes no upstream request

### Requirement: Trigger proxy validates the body locally and forwards a rebuilt body
The route handler SHALL respond `413` when the body exceeds 256 KB, measured in bytes, whether or not a `Content-Length` header is present. It SHALL respond `422` for malformed JSON, for a non-object body, and for any violation of these rules:
- `target_level` SHALL be one of `scan`, `wave`, `experiment`, `scan_ids`.
- For `scan`, `wave` and `experiment`:
  - `target_id` SHALL satisfy `Number.isSafeInteger(x) && x > 0`;
  - `scan_ids` SHALL be absent.
- For `scan_ids`:
  - `target_id` SHALL be absent or `null`;
  - `scan_ids` SHALL be an array of 1 to `MAX_TRIGGER_SCAN_IDS` safe positive integers.

`MAX_TRIGGER_SCAN_IDS` SHALL be a single exported constant equal to the trigger's own `MAX_SCAN_IDS` (5000).

The forwarded body SHALL be exactly `{target_level, target_id, params: {}}` or `{target_level, scan_ids, params: {}}`, built from the validated fields. The handler SHALL forward `Authorization: Bearer <access_token>` to `${WORKFLOWS_URL ?? "http://workflows:5100"}/pipeline`, reading `WORKFLOWS_URL` per request.

#### Scenario: Valid request is forwarded rebuilt, with empty params
- **WHEN** a same-origin signed-in user posts `{"target_level": "scan", "target_id": 42, "params": {"age": 7}, "extra": 1}`
- **THEN** upstream receives exactly `{"target_level": "scan", "target_id": 42, "params": {}}` with `Authorization: Bearer <that user's access_token>`

#### Scenario: Upper boundary of scan_ids
- **WHEN** `scan_ids` has `MAX_TRIGGER_SCAN_IDS` entries and `target_id` is `null`
- **THEN** the request is forwarded

#### Scenario: Out-of-range scan_ids are rejected locally
- **WHEN** `scan_ids` has `MAX_TRIGGER_SCAN_IDS + 1` entries, or is empty
- **THEN** the handler responds `422` and makes no upstream request

#### Scenario: Oversized body
- **WHEN** a 257 KB body arrives without a `Content-Length` header
- **THEN** the handler responds `413` and makes no upstream request

#### Scenario: Cross-field and type violations are rejected locally
- **WHEN** the body is `{"target_level": "experiment", "target_id": 5, "scan_ids": [1]}`, or `{"target_level": "scan_ids", "target_id": 3, "scan_ids": [1]}`, or has a `target_id` of `"42"`, `true`, `0`, `-1`, `1.5` or `9007199254740993`
- **THEN** the handler responds `422` and makes no upstream request

### Requirement: Trigger proxy maps upstream outcomes to caller-safe responses
The route handler SHALL give the upstream `fetch` an abort signal with a 120 s timeout. It SHALL map outcomes as follows:
- **`2xx` with a JSON body whose `pipeline_run_id` and `scan_count` are safe integers:** returned unchanged.
- **Any other `2xx`, any unlisted status, or a non-JSON body:** `502` with a fixed detail.
- **`401`:** a fixed "session expired" detail.
- **`404` and `422`:** returned with the upstream `detail` only when it is a string, truncated to 300 characters; otherwise a fixed detail.
- **`429`:** returned with the upstream `Retry-After` header, only when it is an integer.
- **Timeout:** `504`.
- **Unreachable upstream:** `502`.

The handler MUST NOT echo upstream 5xx bodies. It SHALL log the upstream status and a detail truncated to 300 characters, and never the `Authorization` header.

#### Scenario: Upstream success is returned unchanged
- **WHEN** upstream responds `200` with `{"pipeline_run_id": 91, "scan_count": 40, "reused_count": 0}`
- **THEN** the handler responds `200` with that body

#### Scenario: Malformed success is not trusted
- **WHEN** upstream responds `200` with `{"ok": true}`
- **THEN** the handler responds `502`

#### Scenario: Rate limiting keeps an integer Retry-After
- **WHEN** upstream responds `429` with `Retry-After: 60`
- **THEN** the handler responds `429` with `Retry-After: 60`

#### Scenario: Upstream 5xx does not leak internals
- **WHEN** upstream responds `503` with `{"detail": "auth check failed: http://kong:8000 …"}`
- **THEN** the handler responds `502` with a fixed detail containing no upstream text

#### Scenario: Long 404 detail is truncated
- **WHEN** upstream responds `404` with a 20,000-character string `detail`
- **THEN** the response detail is at most 300 characters

#### Scenario: Timeout
- **WHEN** the upstream `fetch` rejects with a `TimeoutError`
- **THEN** the handler responds `504`
- **AND** the `fetch` was given an `AbortSignal`

### Requirement: Run actions are offered on scan, experiment, wave, accession and drill-down surfaces
The web app SHALL offer run actions on these surfaces. Each submits only through `POST /api/cyl/pipeline`, via one shared confirm dialog.
- **Scan page:** `scan`, when the scan exists.
- **Experiment page:**
  - the whole experiment (`experiment`);
  - each wave (`wave`), in that wave's title row. The page renders that row only when the experiment has more than one wave.
- **Wave × accession page:**
  - "Run this accession" (`scan_ids`): every scan of every listed plant, including scans the grid does not render;
  - a checkbox selection of rendered scans (`scan_ids`), keyed by scan id.
- **A run's drill-down:**
  - "Re-run failed scans (F)" (`scan_ids`: the held rows with status `failed`). Shown when the header counts satisfy `done + failed ≥ scan_count` and F > 0. It warns when any of those rows carries the bloom#900 note.
  - "Re-run scans without a result (M)" (`scan_ids`: the held rows whose status is not `written`, `reused` or `failed`, plus the `failed` rows). Shown only when the run's `status` is `complete` or `failed` and U > 0, with a warning that in-flight scans could be processed twice. M is the number of ids submitted.

A `scan_ids` action whose id count exceeds `MAX_TRIGGER_SCAN_IDS` SHALL be disabled with an explanation.

#### Scenario: Grid selection submits exactly the selected scans
- **WHEN** a user checks three scan thumbnails and confirms "Run selected (3)"
- **THEN** `POST /api/cyl/pipeline` is called once with `{"target_level": "scan_ids", "scan_ids": [<those three ids>]}`

#### Scenario: Run this accession includes scans hidden by the grid
- **WHEN** a plant has two scans on day 3 (only the first is rendered) and one scan with no frame-1 image
- **THEN** "Run this accession" submits all three scan ids

#### Scenario: Single-wave experiment has no separate wave action
- **WHEN** an experiment has exactly one wave
- **THEN** only "Run experiment" is shown

#### Scenario: Re-run failed is hidden until the counts settle
- **WHEN** the drill-down's header counts are `done + failed < scan_count`
- **THEN** no "Re-run failed scans" action is rendered

#### Scenario: Re-run failed submits exactly the failed scans
- **WHEN** the header counts are complete and two rows are `failed`
- **THEN** "Re-run failed scans (2)" submits `{"target_level": "scan_ids", "scan_ids": [<those two ids>]}`

#### Scenario: Scans without a result can be retried on an ended run
- **WHEN** a run has `status = 'complete'`, `scan_count = 40`, 30 `written`, 2 `failed` and 8 `queued` rows
- **THEN** "Re-run scans without a result (10)" is offered, with the double-processing warning

#### Scenario: No duplicate action once counts settle
- **WHEN** a `complete` run's rows are 38 `written` and 2 `failed`
- **THEN** only "Re-run failed scans (2)" is offered

#### Scenario: Oversized selection is blocked
- **WHEN** an action would submit more than `MAX_TRIGGER_SCAN_IDS` ids
- **THEN** it is disabled and explains the limit

### Requirement: Confirm dialog shows read-only resolved params and a pre-check, without predicting skips
The dialog SHALL enumerate the target's scans from `cyl_scans_extended` using the trigger's filters: `scan_id`, `wave_id`, `experiment_id`, or `scan_id IN (...)`. It SHALL read in pages of 1000 ordered by `scan_id` until a page is empty, and send `scan_ids` filters in chunks of at most 200. It SHALL read K and L with one `cyl_scan_latest_source` query per chunk of at most 200 scan ids.

The dialog SHALL keep confirm disabled until enumeration, the pre-check and the concurrent-run query have all settled.

It SHALL display, in this order:
1. **Headline:** the target and N.
2. **Blocking reasons.** Confirm is disabled when any of these holds:
   - N = 0: "No scans to run".
   - A `scan_ids` selection enumerates fewer scans than selected: list the missing ids.
   - N > `MAX_TRIGGER_SCAN_IDS` for a `scan_ids` target.
3. **Stage-in warning:** a count of scans whose species is blank, or whose age is null or not a whole number: "*will fail at stage-in — ask a Bloom admin to fix the plant metadata*". These scans are not included in the params groups.
4. **Concurrent runs.** Runs that meet all of the following, up to 10, then "and M more":
   - created within the last 7 days;
   - `status` not `complete` or `failed`;
   - counts incomplete;
   - touching any experiment the enumerated scans belong to, per `cyl_pipeline_run_experiments`.

   Each entry shows its id, requester, counts-first display state and age, and links to its drill-down.
5. **Pre-check line.**
   - When K = N > 0, the all-results notice replaces it: "*All N scans already have pipeline results. Unless images, parameters, models or code changed, this will likely re-confirm existing results and the trait views won't change.*"
   - Otherwise: "*K of N already have pipeline results.*"

   A details disclosure holds the full text: "*L more scans have only traits without a recorded source (typically older, pre-pipeline data), which a successful run replaces in trait views. All N will be sent; the cluster may skip work for scans it has already processed with the same images, parameters, models and code.*" Its first sentence is omitted when L = 0.
   - N is the number of enumerated scans.
   - K is the number with `max_source_id IS NOT NULL`.
   - L is the number with a `cyl_scan_latest_source` row whose `max_source_id IS NULL`.
6. **Resolved params:** a count per distinct `(species, mode, age)`, where species is `species_name` trimmed and lowercased, mode is `cylinder`, and age is `plant_age_days`. Collapsed beyond 3 groups. Captioned "*Parameters come from each scan's metadata; overrides aren't supported yet*", linking bloom#897. No param input.
7. **Large-run acknowledgement:** when N ≥ 500, confirm stays disabled until the user ticks "*I understand this queues N scans on the shared GPU cluster; runs can't be cancelled from Bloom.*"

The dialog MUST NOT contain the phrases "will run", "will be skipped" or "reused", and MUST NOT compute a parameter hash.

#### Scenario: Pre-check separates pipeline results from legacy traits
- **WHEN** 40 scans are enumerated: 38 have `max_source_id` not null, 1 has a row with `max_source_id` null, and 1 has no row
- **THEN** the pre-check line reads "38 of 40 already have pipeline results."
- **AND** its details read "1 more scans have only traits without a recorded source (typically older, pre-pipeline data), which a successful run replaces in trait views. All 40 will be sent; the cluster may skip work for scans it has already processed with the same images, parameters, models and code."

#### Scenario: Everything already has results
- **WHEN** K = N = 12
- **THEN** the all-results notice is shown in place of the pre-check line

#### Scenario: Resolved params are grouped, and stage-in failures are flagged
- **WHEN** 30 scans have species `" Pennycress "` and age 14, 8 have `"Pennycress"` and age 21, and 2 have a null age
- **THEN** the dialog shows `pennycress · cylinder · 14 — 30` and `pennycress · cylinder · 21 — 8`
- **AND** it warns that 2 scans will fail at stage-in

#### Scenario: Totals span multiple pages
- **WHEN** an experiment has 2,500 scans
- **THEN** the dialog's N is 2500

#### Scenario: Confirm waits for the queries
- **WHEN** the pre-check query has not yet settled
- **THEN** confirm is disabled

#### Scenario: Empty target
- **WHEN** a wave has zero scans
- **THEN** the dialog shows "No scans to run" and confirm is disabled

#### Scenario: Selection references scans that no longer enumerate
- **WHEN** 3 scans are selected but only 2 appear in `cyl_scans_extended`
- **THEN** the dialog names the missing id and confirm is disabled

#### Scenario: A large experiment is allowed
- **WHEN** an experiment-level target enumerates 8,000 scans
- **THEN** no size blocker is shown, and confirm is enabled once the large-run acknowledgement is ticked

#### Scenario: A concurrent run is surfaced with its real state
- **WHEN** run 88, started by another member 12 minutes ago, touches this experiment, has `status = 'running'` and has incomplete counts
- **THEN** the dialog lists run 88 with its display state (e.g. "Running · 10 / 40 succeeded"), "started 12 min ago", and a link to its drill-down

#### Scenario: Large runs need acknowledgement
- **WHEN** N = 800
- **THEN** confirm stays disabled until the acknowledgement is ticked

### Requirement: Confirm dialog submits once and reports outcomes without inviting duplicate runs
The dialog SHALL guard submission with a synchronous in-flight flag, so that repeated clicks before the request settles produce exactly one request. It SHALL report outcomes as follows:
- **Success:** replace the confirm action with a success state. It shows the returned `pipeline_run_id` and `scan_count`, notes any difference between `scan_count` and N, and links to `/app/cyl-pipeline-runs/<id>`. It says: "*Results arrive when each batch of up to 25 scans finishes; counts often stay at 0 for most of the run. Reload the traits page to see new results.*"
- **`429`:** "*Too many requests — this limit is shared with video generation. Try again in about a minute.*" Confirm is re-enabled.
- **`502` or `504`:** a message that the run may have started, with a link to `/app/cyl-pipeline-runs`. Confirm is not re-enabled.
- **`401`:** "session expired — sign in again".
- **`404` or `422`:** the returned detail, with correction allowed.
- **Enumeration or pre-check query failure:** an error, and confirm is disabled.

#### Scenario: Double click creates one request
- **WHEN** confirm is clicked twice synchronously
- **THEN** exactly one `POST /api/cyl/pipeline` request is made

#### Scenario: Success links to the new run
- **WHEN** the response is `{"pipeline_run_id": 91, "scan_count": 40, "reused_count": 0}`
- **THEN** the dialog shows that run 91 started with 40 scans, links to `/app/cyl-pipeline-runs/91`, and offers no confirm button

#### Scenario: Ambiguous failure does not invite a blind retry
- **WHEN** the proxy responds `504`
- **THEN** the dialog says the run may have started, links to `/app/cyl-pipeline-runs`, and confirm stays disabled

#### Scenario: Expired session
- **WHEN** the proxy responds `401`
- **THEN** the dialog says the session expired

### Requirement: Live views synchronise from Realtime without polling
Every live view (the runs list, the drill-down and the experiment panel) SHALL subscribe to Supabase Realtime `postgres_changes`, on a channel topic unique per mounted instance. Each view SHALL:
- **Resync:**
  - refetch its snapshot on the first transition to `SUBSCRIBED`, immediately;
  - after any refetch, collapse all further `SUBSCRIBED` transitions within 2 s of its start into exactly one more refetch, issued when that window ends.
- **Buffer:** hold events that arrive while a snapshot fetch is in flight, and apply them after the snapshot.
- **Merge:** merge each event's `new` record into the held row, so fields absent from the payload keep their held values.
- **Counts:** never let a held run's `done_count` or `failed_count` decrease.
- **Connection state:** show connecting until the first `SUBSCRIBED`, then live. After `CHANNEL_ERROR`, `TIMED_OUT` or `CLOSED`, show offline with a manual refresh control.
- **Cleanup:** remove its channel on unmount.

A live view MUST NOT fetch periodically. Every fetch SHALL be caused by one of these:
- mount;
- a `SUBSCRIBED` transition;
- a user action;
- the experiment panel's membership rule;
- at most one auxiliary lookup per row: experiment names for a live-inserted run, on its first event whose status is not `queued`; or scan metadata and latest-source data for a row that turns `failed` live.

A live view MUST NOT call the workflows `GET /runs/{run_id}` route.

#### Scenario: First subscription closes the gap after server render
- **WHEN** a view mounts with a server snapshot and its channel first reports `SUBSCRIBED`
- **THEN** the view refetches its snapshot once, immediately

#### Scenario: Reconnects inside the window still resync
- **WHEN** the channel reports `CLOSED` then `SUBSCRIBED` 500 ms after a resync started
- **THEN** exactly one more refetch occurs, when the 2 s window ends

#### Scenario: Events during a resync are not lost
- **WHEN** an `UPDATE` with `done_count = 13` arrives while a resync fetch is in flight, and that fetch returns `done_count = 12`
- **THEN** after the snapshot is applied, the held row shows 13

#### Scenario: A missing TOASTed field keeps its value
- **WHEN** a held run has a 4 KB `error_message` and an `UPDATE` payload for it omits `error_message`
- **THEN** the held `error_message` is unchanged

#### Scenario: Offline is visible
- **WHEN** the channel reports `CHANNEL_ERROR` and does not recover
- **THEN** the view shows offline with a refresh control

#### Scenario: No polling
- **WHEN** a live view stays open for five minutes after its initial resync, with no events and no user action
- **THEN** it issues no further queries and no requests to `/workflows/runs`

### Requirement: Shared runs list at `/app/cyl-pipeline-runs`
The web app SHALL provide `/app/cyl-pipeline-runs`, linked from the app navigation as "Pipeline runs", listing `cyl_pipeline_runs` for every signed-in member.

**Ordering and paging.** The list SHALL:
- order by `created_at` then `id`, both descending, and show the most recent 50;
- load older runs by keyset, using the raw `(created_at, id)` of the oldest loaded row;
- compare timestamps at microsecond precision, sorting an unparsable value as newest;
- when at least one row is loaded, insert an event for an unknown run only if it sorts at or after the oldest loaded row; when none is loaded, insert every event;
- de-duplicate rows on "load older".

**Filter.** The list SHALL offer an "Only mine" filter, which filters by `requested_by` on the server.

**Row contents.** Each row SHALL show:
- the run id;
- the target: level, plus the scan count, and for `scan_ids` runs "N selected scans";
- the experiment name(s) from `cyl_pipeline_run_experiments`, linked. These are absent until looked up; soft-deleted experiments are shown without a name.
- the requester: "you" for the current user, otherwise "another member · " followed by the first 8 characters of `requested_by`;
- the elapsed time since creation;
- the counts-first display state, with the failed count linking to the drill-down.

**Empty and error states.** With no runs, the list SHALL show "No pipeline runs yet". When the snapshot fails, it SHALL show an error rather than an empty list.

#### Scenario: A run update arrives live
- **WHEN** run 91 shows "12 / 40 succeeded" and an `UPDATE` arrives with `done_count = 13`
- **THEN** run 91 shows 13 succeeded, without any query

#### Scenario: A new run from another member appears at the top
- **WHEN** an `INSERT` arrives for a run created after every loaded run
- **THEN** it appears first, attributed to "another member · <8 chars>", with no experiment names yet and no query issued
- **AND** its names appear after its first non-`queued` event

#### Scenario: Old runs do not enter the window
- **WHEN** 50 runs are loaded and an `UPDATE` arrives for an unloaded run older than all of them
- **THEN** the list is unchanged
- **AND** the next "load older" returns that run in order, with no run skipped or duplicated

### Requirement: Run display state is derived from counts first
Run display state SHALL be computed by one total, pure function of a run row. Let `N = scan_count`, `F = min(failed_count, N)`, `D = min(done_count, N − F)` and `U = N − D − F`. The function SHALL evaluate these rules in order:
1. `N = 0` → "No scans matched".
2. `D + F = N` and `F = 0` → "Finished · D succeeded".
3. `D + F = N` and `F > 0` → "Finished · D succeeded · F failed".
4. `status = 'failed'` → "Failed · D succeeded · F failed · U without a result".
5. `status = 'complete'` → "Ended · D succeeded · F failed · U without a result".
6. `status = 'partial'` → "Partial · D / N succeeded · F failed".
7. `status` is `queued`, `submitted` or `running` → the stage, then "· D / N succeeded", plus "· F failed" when F > 0.
8. Otherwise → the raw status, then "· D / N succeeded".

The function SHALL also:
- always show the raw `status` as secondary text;
- show the run's `error_message` whenever `status = 'failed'` and it is present, whichever rule applies;
- give each stage an explanatory tooltip. Queued's tooltip says a long wait may mean the dispatcher is down.

`completed_at` MUST NOT affect the state, and `reused_count` MUST NOT be displayed.

Every view SHALL use this function: the list, the experiment panel, and the concurrent-run notice. The drill-down header SHALL call it with `done_count` and `failed_count` replaced by its held-row tallies.

#### Scenario: complete with failures
- **WHEN** `status = 'complete'`, `scan_count = 40`, `done_count = 37`, `failed_count = 3`
- **THEN** the state is "Finished · 37 succeeded · 3 failed", with secondary text `complete`

#### Scenario: complete with scans that have no result
- **WHEN** `status = 'complete'`, `scan_count = 40`, `done_count = 30`, `failed_count = 2`
- **THEN** the state is "Ended · 30 succeeded · 2 failed · 8 without a result"

#### Scenario: partial with settled counts is finished
- **WHEN** `status = 'partial'`, `scan_count = 50`, `done_count = 25`, `failed_count = 25`
- **THEN** the state is "Finished · 25 succeeded · 25 failed"

#### Scenario: partial with unsettled counts is Partial, never Running
- **WHEN** `status = 'partial'`, `scan_count = 50`, `done_count = 20`, `failed_count = 25`
- **THEN** the state is "Partial · 20 / 50 succeeded · 25 failed"

#### Scenario: A failed run keeps its error message
- **WHEN** `status = 'failed'`, `scan_count = 40`, `done_count = 0`, `failed_count = 40`, and `error_message = 'dispatch rejected'`
- **THEN** the state is "Finished · 0 succeeded · 40 failed" and "dispatch rejected" is shown

#### Scenario: completed_at does not mean finished
- **WHEN** `status = 'running'`, `completed_at` is non-null, `scan_count = 40`, `done_count = 10`, `failed_count = 0`
- **THEN** the state is "Running · 10 / 40 succeeded"

#### Scenario: Zero-scan run
- **WHEN** `scan_count = 0` and `status = 'complete'`
- **THEN** the state is "No scans matched"

#### Scenario: Over-count keeps failures and clamps successes
- **WHEN** `scan_count = 10`, `done_count = 9`, `failed_count = 3`
- **THEN** the state is "Finished · 7 succeeded · 3 failed"

### Requirement: Per-run drill-down at `/app/cyl-pipeline-runs/[runId]`
The web app SHALL provide `/app/cyl-pipeline-runs/[runId]`. It SHALL:
- parse `runId` with `parseId`, look the run up with `maybeSingle`, and call `notFound()` when parsing fails or no run is visible to the caller;
- render an error, not `notFound()`, when the lookup query fails;
- load the run's `cyl_pipeline_run_scans` rows in pages of 1000, ordered by `scan_id`, until a page is empty.

It SHALL show:
- **Header:**
  - links to the experiment(s) the run touches;
  - the requested params: "from each scan's metadata (no overrides)" when `params` is `{}`, otherwise key=value pairs;
  - the elapsed time since `created_at`;
  - "last scan update", the maximum `updated_at`;
  - the counts-first display state, computed from the held rows.
- **Scan table.** Columns:
  - `scan_id`;
  - plant QR code, wave and day, from `cyl_scans_extended`;
  - `status` label (`queued` and `predicted` → Waiting; `written` and `reused` → Result recorded; `failed` → Failed; anything else → the raw value), with the raw value as secondary text;
  - `attempts`;
  - `error_message`;
  - `argo_workflow_name`;
  - `source_id`;
  - "current in trait views" (yes when `source_id` equals the scan's `cyl_scan_latest_source.max_source_id`);
  - `updated_at`;
  - a "Scan images" link.
- **Failed rows:**
  - a likely cause from the scan's metadata (blank species; null or non-whole age) when one applies;
  - when the row's `error_message` equals the status poller's backstop message *and* the scan currently has pipeline results, the note: "*If this scan already had results before this run, this may be an unrecognised no-op re-delivery; re-running won't change it (bloom#900).*"
- **Timing note:** "*Results arrive when each batch of up to 25 scans finishes. Reload the traits page to see new results.*"
- **Empty state:** "No scan rows recorded", when `scan_count > 0` and there are no rows.

It SHALL subscribe to `cyl_pipeline_runs` filtered `id=eq.<runId>`, and to `cyl_pipeline_run_scans` filtered `run_id=eq.<runId>`.

#### Scenario: A scan row turns written live, and the header follows
- **WHEN** scan 577 of run 91 is `queued` and an `UPDATE` for `(91, 577)` arrives with `status = 'written'`
- **THEN** the row shows "Result recorded" and the header's succeeded count increases by one

#### Scenario: Events for another run are ignored
- **WHEN** the drill-down for run 91 receives a scan event whose `run_id` is 92
- **THEN** the table is unchanged

#### Scenario: Invalid or unknown run id
- **WHEN** `runId` is `abc`, `0`, `-1`, `1.5`, `0x10`, `1e2` or `9007199254740993`, or a valid id with no visible run
- **THEN** the page renders the not-found page

#### Scenario: Large run loads fully
- **WHEN** a run has 5000 scan rows
- **THEN** the page makes 6 page requests (the last empty) and reports 5000 rows

#### Scenario: A stage-in failure is explained
- **WHEN** a failed row's scan has a null `plant_age_days`
- **THEN** the row shows "Likely cause: plant age missing"

#### Scenario: The no-op note is narrow
- **WHEN** a failed row has an `error_message` other than the backstop message
- **THEN** no bloom#900 note is shown, even if the scan has pipeline results

### Requirement: Experiment page shows that experiment's runs
The experiment page SHALL show the 10 most recent runs that include at least one of its scans. It SHALL read them from `cyl_pipeline_run_experiments`, ordered by `created_at` descending, and show each run's counts-first display state.

**Links.** Each run SHALL link to its drill-down. The panel SHALL link to "All pipeline runs".

**Live events.**
- The panel SHALL apply live events for runs it holds.
- When a newly listed run arrives, it SHALL keep only the 10 most recent.
- When an event concerns an unheld run, the panel SHALL re-query membership, debounced by 1 s.
- It SHALL cache a negative membership result only when that query was triggered by an event whose run status is not `queued`.
- It SHALL issue no query for a run already cached as a non-member.

**Triggered runs.** Runs triggered from this page SHALL be added from the trigger response.

**Unavailable view.** When the view is unavailable, the panel SHALL show "Runs unavailable" without breaking the page.

#### Scenario: A run appears after its scan rows land
- **WHEN** an `INSERT` for run 95 arrives and the membership query returns no rows, and a later `UPDATE` with `status = 'submitted'` arrives and membership now returns experiment 5
- **THEN** experiment 5's panel lists run 95

#### Scenario: Foreign runs stop costing queries
- **WHEN** ten `UPDATE`s arrive for a `running` run whose membership query returned no rows
- **THEN** the panel issues exactly one membership query for it

#### Scenario: View missing
- **WHEN** the view query fails with relation-not-found
- **THEN** the panel shows "Runs unavailable" and the rest of the experiment page renders

### Requirement: Run results link only through requested scans
Result links SHALL be derived only from a run's `cyl_pipeline_run_scans` rows and those scans' metadata:
- **Per scan:** "Scan images", linking to the existing cylinder scan page.
- **Per experiment the run touches:** up to 3 links, for the most common `(wave_number, plant_age_days)` pairs among that experiment's requested scans.
  - Pairs SHALL be counted jointly. Ties break by the lowest wave, then the lowest age.
  - A null age omits `age`.
  - Each link is labelled "Wave W · day A traits (all scans in the experiment, latest result per scan)" and points at `/app/traits/{speciesId}/{experimentId}?wave=<W>&age=<A>`.

**Traits page.** The traits page SHALL accept optional `wave` and `age` search params.
- It SHALL parse each as a single strict positive integer, and ignore arrays and other values.
- It SHALL use them as the selection on its first data load, when they match that load's options.
- On a later trait change, it SHALL keep the current wave and age when they are still available, and otherwise fall back to its defaults.
- Whenever a requested or kept selection is unavailable, it SHALL show a visible note naming what was requested and what is shown.

**Guard.** Source files under `web/lib/cyl-pipeline`, `web/components/cyl-pipeline` and `web/app/app/cyl-pipeline-runs`, and the `@/lib` modules they import transitively, SHALL be checked, excluding the generated `database.types.ts` files. They MUST NOT contain any of these run-id matching surfaces:
- `get_scan_traits`;
- `cyl_scan_traits_source`;
- `cyl_scan_traits_latest`;
- the identifier token `run_id_` (matched by `\brun_id_\b`);
- a `metadata ->> 'pipeline_run_id'` read (matched by `->>\s*'pipeline_run_id'`).

Provenance run ids are unpopulated (bloom#864), so no source can be tied to a run (design D6).

#### Scenario: A one-scan run links to one scan and one traits view
- **WHEN** run 91 has exactly one scan row: scan 577 in experiment 5, wave 1, age 14
- **THEN** its drill-down shows one "Scan images" link, to scan 577's page
- **AND** one traits link, to `/app/traits/<species>/5?wave=1&age=14`

#### Scenario: Wave and age are chosen as a pair
- **WHEN** a run's scans are (wave 1, age 3) ×3, (wave 2, age 5) ×2 and (wave 2, age 7) ×2
- **THEN** the first traits link is `?wave=1&age=3`, followed by `?wave=2&age=5` and `?wave=2&age=7`

#### Scenario: Traits page honours params on first load, then keeps valid selections
- **WHEN** the traits page opens with `?wave=1&age=14` and both exist
- **THEN** wave 1 and age 14 are selected
- **WHEN** the user then changes trait and wave 1 · age 14 is still available
- **THEN** wave 1 and age 14 stay selected

#### Scenario: Unavailable selection is explained
- **WHEN** the traits page opens with `?wave=9` and wave 9 does not exist
- **THEN** it shows its default selection and a note that wave 9 has no data

#### Scenario: Forbidden identifiers are absent
- **WHEN** the static guard scans the three directories (each non-empty) and their transitive `@/lib` imports, excluding generated types
- **THEN** it finds none of the forbidden surfaces
- **AND** `cyl_pipeline_run_scans_run_id_fkey` does not match
