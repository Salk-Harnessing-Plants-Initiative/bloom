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
- **Failed rows:** a likely cause from the scan's metadata (blank species; null or non-whole age) when one applies.
- **Timing note:** "*Results arrive when each batch of up to 25 scans finishes. Reload the traits page to see new results.*"
- **Matched-result note:** "*“Result recorded” includes scans already processed with the same inputs and settings: this run matched that earlier result instead of producing a new one, and the row's source is the earlier result.*" A re-delivery of an already-ingested result marks its row `written` with the existing source, so the label alone does not say this run produced the result.
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

#### Scenario: A failed no-result row whose scan has results carries no re-delivery note
- **WHEN** a failed row's `error_message` is write-back's no-result message or the status poller's backstop message, and the scan currently has pipeline results
- **THEN** the row shows no bloom#900 note, and "Re-run failed scans" shows no bloom#900 warning

#### Scenario: The page says a recorded result may have been matched, not produced
- **WHEN** the drill-down renders
- **THEN** it shows the matched-result note, which says "Result recorded" includes scans whose earlier result this run matched instead of producing a new one
