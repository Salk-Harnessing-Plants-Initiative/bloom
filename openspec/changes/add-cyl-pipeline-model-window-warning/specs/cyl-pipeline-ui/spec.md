## ADDED Requirements

### Requirement: Model-card proxy `GET /api/cyl/pipeline/model-cards` SHALL forward the caller's session to the workflows service and return only a well-formed card list
`web/app/api/cyl/pipeline/model-cards/route.ts` SHALL export only `GET`, with `dynamic = "force-dynamic"` and `runtime = "nodejs"`. Its server-only helpers live in `web/lib/cyl-pipeline/model-cards-proxy.ts`, and the client-safe shape check in `web/lib/cyl-pipeline/model-cards.ts`. It SHALL, in this order:

1. Respond `503` with the trigger proxy's not-enabled text when `isPipelineTriggerEnabled()` is false, without reading the session.
2. Respond `401` when `getSession()` has no access token.
3. Call `GET ${WORKFLOWS_URL ?? "http://workflows:5100"}/model-cards` with `Authorization: Bearer <access token>`, `redirect: "manual"` and an 8-second `AbortSignal.timeout`.

**Pass-through.** It SHALL pass through a `200` whose JSON body has a string `fetched_at`, a non-negative integer `skipped` and a `cards` array in which every card has a string `root_type`, `registry_id` and `version`, and a `selectors` array of `{species: string, mode: string, age_min: integer, age_max: integer}`.

**Failures.** It SHALL respond `502` with a fixed detail to:
- any other status, including a redirect;
- a non-JSON body or a body of another shape;
- a network error.

It SHALL respond `504` to its timeout. No error response SHALL include upstream text.

**Origin.** It SHALL NOT require an `Origin` header, because same-origin browser GETs send none. The trigger proxy `POST /api/cyl/pipeline` stays POST-only.

#### Scenario: A signed-in user gets the card list
- **WHEN** the switch is on, the caller has a session, the request has no `Origin` header, and upstream answers `200` with a well-formed card list
- **THEN** the route answers `200` with that list
- **AND** upstream received the caller's bearer token

#### Scenario: Switched off, the route reads nothing
- **WHEN** `CYL_PIPELINE_TRIGGER_ENABLED` is not `"true"`
- **THEN** the route answers `503`, does not read the session, and does not call the workflows service

#### Scenario: No session is refused before any upstream call
- **WHEN** the switch is on and `getSession()` returns no access token
- **THEN** the route answers `401` and does not call the workflows service

#### Scenario: Upstream failures become fixed caller-safe errors
- **WHEN** upstream answers `503` with detail `upstream-text`, answers `200` with a card whose `selectors` is not an array, or the fetch rejects with a network error
- **THEN** the route answers `502` each time
- **AND** no response body contains `upstream-text`

#### Scenario: A slow upstream times out as 504
- **WHEN** upstream does not answer within 8 seconds
- **THEN** the route answers `504` with a fixed detail

## MODIFIED Requirements

### Requirement: Confirm dialog shows read-only resolved params and a pre-check, without predicting skips
The dialog SHALL enumerate the target's scans from `cyl_scans_extended` using the trigger's filters: `scan_id`, `wave_id`, `experiment_id`, or `scan_id IN (...)`. It SHALL read in pages of 1000 ordered by `scan_id` until a page is empty, and send `scan_ids` filters in chunks of at most 200. It SHALL read K and L with one `cyl_scan_latest_source` query per chunk of at most 200 scan ids, and which scans have at least one image with one `cyl_scans` query per chunk of at most 200 ids, embedding at most one `cyl_images` row per scan.

**Model cards.** It SHALL read the production model cards, and the count of skipped cards, through `GET /api/cyl/pipeline/model-cards` with a 10-second timeout that also covers reading the body. A failed, timed-out or malformed card read SHALL NOT put the dialog in its failed state. A read that returns an empty `cards` list SHALL be treated like a failed one.

**Settling.** The dialog SHALL keep confirm disabled until enumeration, the pre-check, the concurrent-run query and the card read have all settled.

**Model groups.** The model rules below apply to the **model groups**: the resolved-params groups (item 6) of the enumerated scans that have at least one image, ordered by count descending, then species, then age. Scans with no images are counted only by item 3's no-images line. For a model group `(species, mode, age)`, consider every selector, on every card, whose `species` and `mode` equal the group's:
- **Past its window:** such selectors exist and `age` is greater than the largest `age_max` among them. This matches sleap-roots-predict's `past_window_age` without overrides.
- **No model:** the group is not past its window, and no such selector has `age_min <= age <= age_max`. This covers species and modes with no selector, and ages below or between windows, where predict selects no model for any root type.

It SHALL display, in this order:
1. **Headline:** the target and N. A selection made with the scan-selection bar is titled "N selected scans", or "1 selected scan" for one; other `scan_ids` targets keep their caller's title.
2. **Blocking reasons.** Confirm is disabled when any of these holds:
   - N = 0: "No scans to run".
   - A `scan_ids` selection enumerates fewer scans than selected: list the missing ids.
   - N > `MAX_TRIGGER_SCAN_IDS` for a `scan_ids` target.
   - The card read returned cards with `skipped` = 0, at least one model group exists, and every model group is **no model**: "*None of these scans has a production model for its species and age, so the pipeline can't produce results.*" ("*This scan has no production model for its species and age, so the pipeline can't produce results.*" when N = 1). When `skipped` > 0 the card list may be incomplete, so this is not a blocking reason and the no-model warning below is shown instead.
3. **Stage-in and model warnings.** None of these disables confirm.
   - A count of scans whose species is blank, or whose age is null or not a whole number: "*will fail at stage-in — ask a Bloom admin to fix the plant metadata*". These scans are not included in the params groups.
   - Separately, a count of scans with no images: "*have no images and will fail at stage-in*" (bloomctl's stage-in fails a scan with no frames).
   - When the card read returned cards and at least one model group is **no model**, and the blocking reason above does not apply: the heading "*N scans have no production model for their species and age and will fail:*" (N = the summed counts; "*1 scan has no production model for its species and age and will fail:*" when N = 1), then one line per such group, uncollapsed, in model-group order: "*<species> · day <age> (<count>)*".
   - When the card read returned cards and at least one model group is **past its window**: the heading "*N scans are past their models' validated age and are predicted with the nearest models:*" (N = the summed counts; "*1 scan is past its models' validated age and is predicted with the nearest models:*" when N = 1), then one line per such group, uncollapsed, in model-group order: "*<species> · day <age> — models validated up to day <max> (<count>)*", where `<max>` is that largest `age_max`.
   - When the card read failed, timed out or returned no cards, and at least one model group exists: the muted line "*Couldn't check the models' age ranges.*" in place of the two model warnings.
   - Nothing about models is shown while the card read is pending, or when no model group exists.
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
6. **Resolved params:** a count per distinct `(species, mode, age)`, where species is `species_name` trimmed and lowercased, mode is `cylinder`, and age is `plant_age_days`. Collapsed beyond 3 groups. Captioned "*Parameters come from each scan's metadata. Choosing models isn't supported yet*", linking bloom#897. No param input.
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

#### Scenario: Confirm waits for the card read
- **WHEN** enumeration, the pre-check and the concurrent-run query have settled but the card read has not
- **THEN** confirm is disabled
- **AND** no model warning and no muted line is shown

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

#### Scenario: Past-window groups are named with the window's top
- **WHEN** the target holds, all with images, 60 arabidopsis scans at day 21, 30 at day 28 and 120 at day 14, and the cards' arabidopsis·cylinder selectors reach `age_max` 14
- **THEN** the dialog shows "*90 scans are past their models' validated age and are predicted with the nearest models:*"
- **AND** the lines "*arabidopsis · day 21 — models validated up to day 14 (60)*" then "*arabidopsis · day 28 — models validated up to day 14 (30)*"
- **AND** the day-14 group is in neither model warning
- **AND** confirm is enabled once the other checks pass

#### Scenario: Model warnings follow count order, not age order
- **WHEN** the target holds 30 arabidopsis scans at day 21 and 60 at day 28, all with images
- **THEN** the day-28 line is listed before the day-21 line

#### Scenario: Rice past both windows is named with the higher window
- **WHEN** the target holds 5 rice scans at day 18 with images, and the rice·cylinder selectors are 2–5 and 6–10
- **THEN** the dialog shows "*5 scans are past their models' validated age and are predicted with the nearest models:*"
- **AND** the line "*rice · day 18 — models validated up to day 10 (5)*"

#### Scenario: One past-window scan uses the singular heading
- **WHEN** the target holds 1 soybean scan at day 10 with images, and the soybean·cylinder selectors reach `age_max` 8
- **THEN** the dialog shows "*1 scan is past its models' validated age and is predicted with the nearest models:*"
- **AND** the line "*soybean · day 10 — models validated up to day 8 (1)*"

#### Scenario: Another mode's window does not count
- **WHEN** the cards have canola·cylinder 2–13 and canola·multiplant cylinder 2–20, and the target holds 4 canola scans at day 15 with images
- **THEN** the dialog lists "*canola · day 15 — models validated up to day 13 (4)*"

#### Scenario: Too-young scans in a supported species are warned about, not blocked
- **WHEN** the target holds, all with images, 100 canola scans at day 7 and 12 at day 0, and the canola·cylinder selectors are 2–13
- **THEN** the dialog shows "*12 scans have no production model for their species and age and will fail:*" and the line "*canola · day 0 (12)*"
- **AND** the day-7 group is in neither model warning
- **AND** confirm is enabled once the other checks pass

#### Scenario: A run where no scan has a model is blocked
- **WHEN** the target holds 50 sorghum scans at day 10 with images, and no card has a sorghum selector
- **THEN** the blocking reason "*None of these scans has a production model for its species and age, so the pipeline can't produce results.*" is shown
- **AND** the no-model warning is not shown in addition
- **AND** confirm is disabled

#### Scenario: Ages at a window's top get no model warning
- **WHEN** the target holds pennycress scans at day 14 with images, and the pennycress·cylinder selectors reach 14
- **THEN** neither model warning nor the muted line is shown

#### Scenario: Scans with no images are not counted in the model warnings
- **WHEN** the target holds 4 arabidopsis scans at day 28, of which 1 has no images
- **THEN** the past-window warning counts 3 scans
- **AND** the no-images line counts 1 scan

#### Scenario: Scans that all lack images get no model warning and no model block
- **WHEN** the target holds only sorghum scans at day 10, none of which has images
- **THEN** the dialog shows the no-images line, no model warning, no muted line and no model blocker

#### Scenario: A failed or empty card read does not block the run
- **WHEN** at least one model group exists and the card read answers `502`, does not settle within 10 seconds, or returns an empty `cards` list
- **THEN** the dialog shows "*Couldn't check the models' age ranges.*"
- **AND** no model blocker is shown, confirm is enabled once the other checks pass, and no failed-state alert is shown

#### Scenario: The caption points at model choice
- **WHEN** the dialog shows its Parameters section
- **THEN** the caption reads "*Parameters come from each scan's metadata. Choosing models isn't supported yet*", linking bloom#897

#### Scenario: A possibly incomplete card list never blocks
- **WHEN** the target holds 50 sorghum scans at day 10 with images, and the card read returned the production cards with `skipped: 1`
- **THEN** no model blocker is shown and confirm is enabled once the other checks pass
- **AND** the dialog shows "*50 scans have no production model for their species and age and will fail:*" and the line "*sorghum · day 10 (50)*"

#### Scenario: One scan with no model is blocked in the singular
- **WHEN** the target is 1 sorghum scan at day 10 with images, and the card read returned cards with `skipped: 0`
- **THEN** the blocking reason reads "*This scan has no production model for its species and age, so the pipeline can't produce results.*"

#### Scenario: Both model warnings appear in a mixed target, and nothing blocks
- **WHEN** the target holds, all with images, 10 sorghum scans at day 10 and 5 arabidopsis scans at day 28
- **THEN** the no-model warning appears before the past-window warning
- **AND** no model blocker is shown and confirm is enabled once the other checks pass

#### Scenario: The first day of each window is covered
- **WHEN** the target holds canola scans at day 2 and rice scans at day 6, all with images
- **THEN** neither model warning is shown
