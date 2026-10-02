## MODIFIED Requirements

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
  - "Re-run failed scans (F)" (`scan_ids`: the held rows with status `failed`). Shown when the header counts satisfy `done + failed ≥ scan_count` and F > 0.
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

### Requirement: Per-run drill-down at `/app/cyl-pipeline-runs/[runId]`
The web app SHALL provide `/app/cyl-pipeline-runs/[runId]`. It SHALL:
- parse `runId` with `parseId`, look the run up with `maybeSingle`, and call `notFound()` when parsing fails or no run is visible to the caller;
- render an error, not `notFound()`, when the lookup query fails;
- load the run's `cyl_pipeline_run_scans` rows in pages of 1000, ordered by `scan_id`, until a page is empty.

It SHALL show:
- **Header:**
  - links to the experiment(s) the run touches;
  - the requested params: "from each scan's metadata (no overrides)" when `params` is `{}`, otherwise key=value pairs labelled as requested overrides not applied yet (#897);
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
  - "current in trait views" (yes when `source_id` equals the scan's `cyl_scan_latest_source.max_source_id` as last read; unknown when that read failed, or when the row's `source_id` changed since it);
  - `updated_at`;
  - a "Scan images" link, when the scan's species, experiment, wave and accession are known.
- **Failed rows:**
  - a likely cause from the scan's metadata (blank species; null or non-whole age) when one applies;
  - when the scan's latest source (`cyl_scan_latest_source.max_source_id`, as last read) was written by this run (its `cyl_trait_sources.cyl_pipeline_run_id` equals the run's id), the note: "*This run's result arrived after this row was closed: the scan's current traits are this run's (source N).*" A write-back that lands after the row was failed records its traits but leaves the row `failed` with no `source_id`, so the row alone would say this run produced nothing.
- **Timing note:** "*Results arrive when each batch of up to 25 scans finishes. Reload the traits page to see new results.*"
- **Matched-result note:** "*“Result recorded” includes scans already processed with the same images, models, parameters and pipeline code: this run matched that earlier result instead of recording a new one, and the row's source is the earlier result.*" A re-delivery of an already-ingested result marks its row `written` with the existing source, so the label alone does not say this run produced the result.
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

#### Scenario: A failed no-result row whose scan has another run's results carries no re-delivery note
- **WHEN** a failed row's `error_message` is write-back's no-result message or the status poller's backstop message, and the scan's latest source was written by another run or outside any run
- **THEN** the row shows no bloom#900 note and no late-result note, and "Re-run failed scans" shows no bloom#900 warning

#### Scenario: A result that arrived after its row was closed is named
- **WHEN** scan 577's row in run 91 is `failed` with no `source_id`, and the scan's latest source is 40, whose `cyl_pipeline_run_id` is 91
- **THEN** the row shows "*This run's result arrived after this row was closed: the scan's current traits are this run's (source 40).*"

#### Scenario: The page says a recorded result may have been matched, not produced
- **WHEN** the drill-down renders
- **THEN** it shows the matched-result note, which says "Result recorded" includes scans whose earlier result this run matched instead of recording a new one

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
- at most one auxiliary lookup per row: experiment names for a live-inserted run, on its first event whose status is not `queued`; or scan metadata, the scan's latest source, and the run that wrote that source (`cyl_trait_sources.cyl_pipeline_run_id`, read by source id), for a row that turns `failed` live.

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

#### Scenario: A row that turns failed live is looked up once
- **WHEN** the drill-down holds scan 578's row and an `UPDATE` turns it `failed`, followed by two more `UPDATE`s for that row
- **THEN** one read each of its latest source and of that source's run is made, after the batch window, and none for the later events
