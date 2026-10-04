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
  - "Re-run failed scans (F)" (`scan_ids`: the held rows with status `failed`, except rows showing the late-result note). Shown when the header counts satisfy `done + failed ≥ scan_count` and F > 0.
  - "Re-run scans without a result (M)" (`scan_ids`: the held rows whose status is not `written`, `reused` or `failed`, plus the `failed` rows, except rows showing the late-result note). Shown only when the run's `status` is `complete` or `failed` and U > 0, with a warning that in-flight scans could be processed twice. M is the number of ids submitted.
  - A `failed` row showing the late-result note already has this run's result as the scan's current traits, so neither action re-runs it. When the note cannot be computed (its lookups failed or have not landed), the row is offered as before.

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

#### Scenario: A failed row with this run's late result is not offered for re-run
- **WHEN** a `complete` run's settled rows are 37 `written` and 3 `failed`, and one failed row's scan has this run's source as its latest source (its late-result note is shown)
- **THEN** "Re-run failed scans (2)" is offered and submits only the other two scan ids
- **AND** the row itself still reads `failed` with its note, and the header counts still include it as failed

#### Scenario: Without the late-result lookup every failed row is offered
- **WHEN** the latest-source lookup failed, and two rows are `failed`
- **THEN** "Re-run failed scans (2)" submits both scan ids
