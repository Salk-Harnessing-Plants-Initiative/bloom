# Fix the cylinder pipeline runs UI: list headings, plain ids, unlinked "0 failed", "—" without a source, singular wording

## Why

These are follow-ups to #938 (bloom#15 PR 3), tracked in bloom#955. They were seen on staging on 2026-09-29 and, as noted in the issue comment, on 2026-10-01 at `ac5c63d5` while starting run 19 from a one-scan selection.

1. **The runs list has no column headings.** `/app/cyl-pipeline-runs` renders each run as a `<li>` grid of four slots (`web/app/app/cyl-pipeline-runs/RunRow.tsx:25`). A reader has to guess what "3 selected scans · another member · 998556ed" means, and screen readers get no column context.
2. **The drill-down formats ids as numbers.** `RunScansTable.tsx` declares scan id, wave, day, attempts and source as DataGrid `type: "number"` columns. MUI formats those with `toLocaleString()`, so a scan id reads `12,894,7…` (grouped, then truncated at width 90) and a wave reads `9,999`.
3. **"0 failed" is a link.** `RunState.tsx:33` links any label part that matches `/^\d+ failed$/`. That includes "0 failed", which the Failed, Ended and Partial labels print (`run-display.ts:82,90,98`), so the link leads to an empty filter. The runs list and the experiment page's runs panel both use this component.
4. **Rows with no source show "Current in trait views: no".** `RunDetailLive.tsx:262` sets `current = false` when `source_id` is null, and the cell shows "no". That reads as "this scan's results aren't current", when in fact the run recorded no result for the scan.
5. **"1 selected scans".** `ScanSelection.tsx:80` passes the title `` `${ids.length} selected scans` ``, so the dialog reads "Run the pipeline on 1 selected scans · 1 scan".
6. **"All 1 scans already have pipeline results."** The confirm dialog's pre-check block (`RunPipelineDialog.tsx:291–308`) always uses the plural form. Its other sentences make the same mistake at 1:
   - "1 of 40 already have";
   - "1 more scans have only traits…", which the current spec scenario pins (`openspec/specs/cyl-pipeline-ui/spec.md:203`);
   - "All 1 will be sent".

## What Changes

- **Runs list.** The list becomes a `<table>` with `<th scope="col">` headings **Run**, **Target**, **Experiments** and **State**, and one `<tr data-testid="run-N">` per run. Each row holds the same contents as today.
- **Drill-down scan table.**
  - `scan_id`, wave, day, `attempts` and `source_id` show as plain integers, with no digit grouping.
  - The columns stay numeric, so sorting and the numeric filter operators are unchanged.
  - The Scan column widens from 90 to 110 px.
- **Failed-count link.** `RunState` links the failed count only when F > 0. "0 failed" stays in the label as plain text. Both callers are fixed: the runs list and the experiment page's runs panel.
- **"Current in trait views".** A row with no `source_id` shows "—", even when the latest-source read failed. Rows that have a source keep "yes", "no" and "unknown". The column's tooltip explains what "—" means.
- **Singular wording.** The scan-selection title and the pre-check block get singular forms for one scan. The exact text is in the spec delta.
  - The runs list's "1 selected scan" is already correct in code (`run-text.ts:9`). The delta only brings the spec in line with it.
- **One pluraliser.** `plural` in `web/lib/cyl-pipeline/run-text.ts` is exported and reused. The copy in `RunPipelineDialog.tsx:86` is deleted.

Nothing changes in the database, Realtime, the trigger proxy, or `runDisplay`'s labels.

## Out of scope

- **Runs 1 and 2 on staging.** Their list state disagrees with their drill-down, because their counts were never written: both runs predate #774. Backfilling those two rows is a separate data decision (see "Context" in bloom#955).
- **Other pipeline UI work:** .slp links (#899), the pre-submit preview (#898), and trigger reliability (#956, #963, #964).

## Impact

- **Spec:** `cyl-pipeline-ui`. Four MODIFIED requirements:
  - "Shared runs list at `/app/cyl-pipeline-runs`";
  - "Per-run drill-down at `/app/cyl-pipeline-runs/[runId]`";
  - "Confirm dialog shows read-only resolved params and a pre-check, without predicting skips";
  - "Experiment page shows that experiment's runs".

  No other active change modifies them. `isolate-cyl-pipeline-environments` touches only "Starting pipeline runs can be switched off per environment".
- **Code** (`web/` only):
  - `app/app/cyl-pipeline-runs/{RunRow,RunsListLive}.tsx`
  - `app/app/cyl-pipeline-runs/[runId]/RunScansTable.tsx`. `RunDetailLive.tsx` is unchanged (design D2).
  - `components/cyl-pipeline/{RunState,ScanSelection,RunPipelineDialog}.tsx`
  - `lib/cyl-pipeline/run-text.ts`
- **Tests:**
  - `RunsListLive.test.tsx`, `RunScansTable.test.tsx`, `ExperimentRunsPanel.test.tsx`, `ScanSelection.test.tsx`, `RunPipelineDialog.test.tsx`;
  - new: `RunState.test.tsx` and `run-text.test.ts`.
- **Docs:** none outside OpenSpec. `web/README.md` doesn't describe any of this text, and `web/` has no CHANGELOG.
- **Issue:** Part of #955. The issue is closed by hand after the staging check in tasks §8.
