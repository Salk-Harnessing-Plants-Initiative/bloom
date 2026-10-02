## MODIFIED Requirements

### Requirement: Confirm dialog shows read-only resolved params and a pre-check, without predicting skips
The dialog SHALL enumerate the target's scans from `cyl_scans_extended` using the trigger's filters: `scan_id`, `wave_id`, `experiment_id`, or `scan_id IN (...)`. It SHALL read in pages of 1000 ordered by `scan_id` until a page is empty, and send `scan_ids` filters in chunks of at most 200. It SHALL read K and L with one `cyl_scan_latest_source` query per chunk of at most 200 scan ids, and which scans have at least one image with one `cyl_scans` query per chunk of at most 200 ids, embedding at most one `cyl_images` row per scan.

The dialog SHALL keep confirm disabled until enumeration, the pre-check and the concurrent-run query have all settled.

It SHALL display, in this order:
1. **Headline:** the target and N. A selection made with the scan-selection bar is titled "N selected scans", or "1 selected scan" for one; other `scan_ids` targets keep their caller's title.
2. **Blocking reasons.** Confirm is disabled when any of these holds:
   - N = 0: "No scans to run".
   - A `scan_ids` selection enumerates fewer scans than selected: list the missing ids.
   - N > `MAX_TRIGGER_SCAN_IDS` for a `scan_ids` target.
3. **Stage-in warning:** a count of scans whose species is blank, or whose age is null or not a whole number: "*will fail at stage-in — ask a Bloom admin to fix the plant metadata*". These scans are not included in the params groups. Separately, a count of scans with no images: "*have no images and will fail at stage-in*" (bloomctl's stage-in fails a scan with no frames).
4. **Concurrent runs.** Runs that meet all of the following, up to 10, then "and M more":
   - created within the last 7 days;
   - `status` not `complete` or `failed`;
   - counts incomplete;
   - touching any experiment the enumerated scans belong to, per `cyl_pipeline_run_experiments`.

   Each entry shows its id, requester, counts-first display state and age, and links to its drill-down.
5. **Pre-check line.**
   - When K = N > 0, the all-results notice replaces it: "*All N scans already have pipeline results. The run will still be created and sent to the cluster, which skips scans it has already processed with the same models and code.*" When N = 1, its first sentence is "*This scan already has pipeline results.*"
   - Otherwise: "*K of N already have pipeline results.*" When K = 1 it reads "*1 of N already has pipeline results.*" When N = 1 (so K = 0) it reads "*This scan has no pipeline results yet.*"

   A details disclosure holds the full text: "*L more scans have only traits without a recorded source (typically older, pre-pipeline data), which a successful run replaces in trait views. All N will be sent; the cluster skips scans it has already processed with the same models and code.*" Its first sentence is omitted when L = 0, and begins "*1 more scan has only traits*" when L = 1. When N = 1 the details read "*This scan has only traits without a recorded source (typically older, pre-pipeline data), which a successful run replaces in trait views. The scan will be sent; the cluster skips it if it has already been processed with the same models and code.*", again without the first sentence when L = 0.
   - N is the number of enumerated scans.
   - K is the number with `max_source_id IS NOT NULL`.
   - L is the number with a `cyl_scan_latest_source` row whose `max_source_id IS NULL`.
6. **Resolved params:** a count per distinct `(species, mode, age)`, where species is `species_name` trimmed and lowercased, mode is `cylinder`, and age is `plant_age_days`. Collapsed beyond 3 groups. Captioned "*Parameters come from each scan's metadata; overrides aren't supported yet*", linking bloom#897. No param input.
7. **Large-run acknowledgement:** when N ≥ 500, confirm stays disabled until the user ticks "*I understand this queues N scans on the shared GPU cluster; runs can't be cancelled from Bloom.*"

The dialog MUST NOT contain the phrases "will run", "will be skipped" or "reused", and MUST NOT compute a parameter hash.

#### Scenario: Pre-check separates pipeline results from legacy traits
- **WHEN** 40 scans are enumerated: 38 have `max_source_id` not null, 1 has a row with `max_source_id` null, and 1 has no row
- **THEN** the pre-check line reads "38 of 40 already have pipeline results."
- **AND** its details read "1 more scan has only traits without a recorded source (typically older, pre-pipeline data), which a successful run replaces in trait views. All 40 will be sent; the cluster skips scans it has already processed with the same models and code."

#### Scenario: One scan of many already has results
- **WHEN** 40 scans are enumerated, 1 has `max_source_id` not null, and none has a row with `max_source_id` null
- **THEN** the pre-check line reads "1 of 40 already has pipeline results."

#### Scenario: One selected scan is named in the singular
- **WHEN** the dialog opens from the scan-selection bar with one scan selected
- **THEN** its headline reads exactly "Run the pipeline on 1 selected scan · 1 scan"

#### Scenario: A single scan that already has results
- **WHEN** K = N = 1
- **THEN** the all-results notice reads "This scan already has pipeline results. The run will still be created and sent to the cluster, which skips scans it has already processed with the same models and code."

#### Scenario: A single scan with only legacy traits
- **WHEN** N = 1, K = 0 and L = 1
- **THEN** the pre-check line reads "This scan has no pipeline results yet."
- **AND** its details read "This scan has only traits without a recorded source (typically older, pre-pipeline data), which a successful run replaces in trait views. The scan will be sent; the cluster skips it if it has already been processed with the same models and code."

#### Scenario: A single scan with no traits at all
- **WHEN** N = 1, K = 0 and L = 0
- **THEN** the pre-check line reads "This scan has no pipeline results yet."
- **AND** its details read "The scan will be sent; the cluster skips it if it has already been processed with the same models and code."

#### Scenario: Everything already has results
- **WHEN** K = N = 12
- **THEN** the all-results notice is shown in place of the pre-check line

#### Scenario: Resolved params are grouped, and stage-in failures are flagged
- **WHEN** 30 scans have species `" Pennycress "` and age 14, 8 have `"Pennycress"` and age 21, and 2 have a null age
- **THEN** the dialog shows `pennycress · cylinder · 14 — 30` and `pennycress · cylinder · 21 — 8`
- **AND** it warns that 2 scans will fail at stage-in

#### Scenario: Scans without images are flagged
- **WHEN** 40 scans are enumerated and 2 of them have no `cyl_images` rows
- **THEN** the dialog says "2 scans have no images and will fail at stage-in"

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

### Requirement: Shared runs list at `/app/cyl-pipeline-runs`
The web app SHALL provide `/app/cyl-pipeline-runs`, linked from the app navigation as "Cylinder Pipeline Runs" and headed "Cylinder pipeline runs", listing `cyl_pipeline_runs` for every signed-in member. The name says which pipeline: other pipelines (such as RNA-seq) have runs of their own.

**Ordering and paging.** The list SHALL:
- order by `created_at` then `id`, both descending, and show the most recent 50;
- load older runs by keyset, using the raw `(created_at, id)` of the oldest loaded row;
- compare timestamps at microsecond precision, sorting an unparsable value as newest;
- when at least one row is loaded, insert an event for an unknown run only if it sorts at or after the oldest loaded row; when none is loaded, insert every event;
- de-duplicate rows on "load older".

**Filter.** The list SHALL offer an "Only mine" filter, which filters by `requested_by` on the server.

**Table.** The list SHALL be a table whose header row has the column headings **Run**, **Target**, **Experiments** and **State**, each a column header (`<th scope="col">`), with one table row per run. When no run is listed, the table is not rendered.

**Row contents.** Each row SHALL show:
- **Run:** the run id, and the elapsed time since creation;
- **Target:** the level, plus the scan count, and for `scan_ids` runs "N selected scans" ("1 selected scan" for one); then the requester: "you" for the current user, otherwise "another member · " followed by the first 8 characters of `requested_by`;
- **Experiments:** the experiment name(s) from `cyl_pipeline_run_experiments`, linked. These are absent until looked up. A soft-deleted experiment is absent for `bloom_user`, because the view drops it, so a run whose only experiment is soft-deleted shows no experiment link.
- **State:** the counts-first display state, with the failed count linking to the drill-down's failed filter only when it is greater than zero; "0 failed" is plain text.

**Empty and error states.** With no runs, the list SHALL show "No pipeline runs yet", or "You haven't started any pipeline runs" when "Only mine" is on. When the snapshot fails, it SHALL show an error rather than an empty list; runs already held stay listed below the error.

#### Scenario: The navigation names the cylinder pipeline
- **WHEN** a signed-in member opens the app navigation
- **THEN** it has a "Cylinder Pipeline Runs" entry linking to `/app/cyl-pipeline-runs`, and no entry named "Pipeline runs"

#### Scenario: The list has column headings
- **WHEN** a signed-in member opens `/app/cyl-pipeline-runs` and at least one run is listed
- **THEN** the list exposes the column headers "Run", "Target", "Experiments" and "State", in that order, each with `scope="col"`
- **AND** each run is one row under them

#### Scenario: No runs, no table
- **WHEN** no run is listed
- **THEN** "No pipeline runs yet" is shown and no table is rendered

#### Scenario: A failed resync keeps the held runs
- **WHEN** run 91 is listed and a later snapshot fails
- **THEN** the error is shown and run 91 is still a row of the table

#### Scenario: Zero failures are not a link
- **WHEN** run 7 has `status = 'failed'`, `scan_count = 3`, `done_count = 0` and `failed_count = 0`
- **THEN** its state reads "Failed · 0 succeeded · 0 failed · 3 without a result"
- **AND** "0 failed" is not a link

#### Scenario: A failure count links to the failed filter
- **WHEN** run 91 is `complete` with 37 succeeded and 3 failed
- **THEN** "3 failed" links to `/app/cyl-pipeline-runs/91?status=failed`

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

<!-- This block is raised onto fix-cyl-noop-redelivery-scan-resolution's block for the same requirement (its one-line "Failed rows" and its no-op scenario), plus this change's edits. Archive that change first; see tasks 9.1. -->

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
  - "current in trait views": "—" when the row has no `source_id` (no result is linked to this row), even if the latest-source read failed; otherwise "unknown" when that read failed or the row's `source_id` changed since it; otherwise "yes" when `source_id` equals the scan's last-read `cyl_scan_latest_source.max_source_id`, else "no". The column sorts and filters by the value it shows. Its description reads: "— means no result is linked to this row. The scan may still have pipeline results (for example a result that arrived after the run closed, or another run's), so check its traits before re-running." A "—" does not mean the scan has no results: a delivery that lands after reconciliation failed the row stores its traits but leaves the row `failed` with no `source_id`;
  - `updated_at`;
  - a "Scan images" link, when the scan's species, experiment, wave and accession are known.

  `scan_id`, wave, day, `attempts` and `source_id` SHALL be shown as plain integers, without digit grouping, and SHALL stay numeric columns, so sorting and the column filter's numeric operators are unchanged. The scan column SHALL be at least 110 px wide, enough for a ten-digit id at compact density.
- **Failed rows:** a likely cause from the scan's metadata (blank species; null or non-whole age) when one applies.
- **Timing note:** "*Results arrive when each batch of up to 25 scans finishes. Reload the traits page to see new results.*"
- **Empty state:** "No scan rows recorded", when `scan_count > 0` and there are no rows.

It SHALL subscribe to `cyl_pipeline_runs` filtered `id=eq.<runId>`, and to `cyl_pipeline_run_scans` filtered `run_id=eq.<runId>`.

#### Scenario: A scan row turns written live, and the header follows
- **WHEN** scan 577 of run 91 is `queued` and an `UPDATE` for `(91, 577)` arrives with `status = 'written'`
- **THEN** the row shows "Result recorded" and the header's succeeded count increases by one

#### Scenario: Ids are shown as plain integers
- **WHEN** a scan row has `scan_id = 12894712`, wave 9999, day 1000, `attempts = 1200` and `source_id = 1048576`
- **THEN** its cells read "12894712", "9999", "1000", "1200" and "1048576", with no digit separators

#### Scenario: Integer columns stay numeric
- **WHEN** the scan table's columns are inspected
- **THEN** the scan, wave, day, attempts and source columns are numeric columns whose formatter returns "12894712" for 12894712 and "0" for 0
- **AND** the scan column is at least 110 px wide

#### Scenario: A row without a source shows a dash
- **WHEN** a `failed` or `queued` row has no `source_id`
- **THEN** its "current in trait views" cell reads "—", not "no"

#### Scenario: A row without a source shows a dash even when the read failed
- **WHEN** a `failed` row has no `source_id` and the latest-source read failed
- **THEN** its "current in trait views" cell reads "—", not "unknown"

#### Scenario: A row with a source reads unknown when the read failed
- **WHEN** a `written` row has `source_id = 40` and the latest-source read failed
- **THEN** its "current in trait views" cell reads "unknown"

#### Scenario: The column sorts by what it shows
- **WHEN** the rows' "current in trait views" cells read "no", "—", "no", "—" and the column is sorted
- **THEN** the "—" rows are adjacent and the "no" rows are adjacent

#### Scenario: A row with a source that is no longer latest reads no
- **WHEN** a `written` row has `source_id = 40` and the scan's last-read `max_source_id` is 41
- **THEN** its "current in trait views" cell reads "no"

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

### Requirement: Experiment page shows that experiment's runs
The experiment page SHALL show the 10 most recent runs that include at least one of its scans. It SHALL read them from `cyl_pipeline_run_experiments`, ordered by `created_at` then `run_id`, both descending, and show each run's counts-first display state.

**Links.** Each run SHALL link to its drill-down. The panel SHALL link to "All cylinder pipeline runs". A run's failed count SHALL link to the drill-down's failed filter only when it is greater than zero; "0 failed" is plain text.

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

#### Scenario: Zero failures are not a link in the panel
- **WHEN** a run in experiment 5's panel has `status = 'failed'`, `scan_count = 3`, `done_count = 0` and `failed_count = 0`
- **THEN** its state reads "Failed · 0 succeeded · 0 failed · 3 without a result" and "0 failed" is not a link

#### Scenario: A failure count in the panel links to the failed filter
- **WHEN** run 91 in experiment 5's panel is `complete` with 37 succeeded and 3 failed
- **THEN** "3 failed" links to `/app/cyl-pipeline-runs/91?status=failed`

#### Scenario: View missing
- **WHEN** the view query fails with relation-not-found
- **THEN** the panel shows "Runs unavailable" and the rest of the experiment page renders

