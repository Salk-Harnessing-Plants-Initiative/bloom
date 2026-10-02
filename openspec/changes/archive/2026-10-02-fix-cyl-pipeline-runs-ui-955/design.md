# Design: fix-cyl-pipeline-runs-ui-955

These are small display fixes, but each one involves a choice that a reviewer would otherwise have to guess at. This document records those choices.

## D1. Plain integers: keep `type: "number"` and override `valueFormatter`

In MUI x-data-grid 8.x, a numeric column (`GRID_NUMERIC_COL_DEF`, `gridNumericColDef.js`) brings three things:

- the formatter `value => isNumber(value) ? value.toLocaleString() : value || ''`, which is where the separators come from;
- `gridNumberComparator`;
- numeric filter operators (`=`, `!=`, `>`, `>=`, `<`, `<=`, is empty).

**Options considered:**

- **Drop `type: "number"`.** Sorting would barely change, because the sort reads the raw value and `gridStringOrNumberComparator` subtracts numbers. Filtering would change: the column-menu filter is enabled (no `disableColumnFilter`) and would offer string operators (contains, starts with…). "wave > 3" would stop working.
- **Keep the type and pass `valueFormatter: (v) => (v == null ? "" : String(v))`** (chosen). Only the displayed text changes.

**Implementation.**

- The five columns (`scan_id`, `wave_number`, `plant_age_days`, `attempts`, `source_id`) share one exported `plainInteger` formatter.
- The column definitions are exported, as `scanTableColumns`, so a test can pin `type: "number"` and the width. jsdom has no layout, so a sort or truncation test there can't fail. The width check is done by eye on staging (tasks §8).
- The Scan column goes from 90 to 110 px, which fits a ten-digit id at compact density.
- A null Source cell stays blank (""), not "—". It's a numeric column, and "" is what the grid shows for an empty number.

## D2. "—" without a source: decided in the column's `valueGetter`, not in `RunDetailLive`

`current` stays `boolean | null`. `RunDetailLive` computes it, and its tests pin those values (`RunDetailLive.test.tsx:336–396`, `:489–498`). That test file mocks the table, so nothing it checks changes.

The "Current in trait views" column's `valueGetter` reads the row and returns the text it shows (DataGrid v8 calls `valueGetter(value, row, colDef, apiRef)`):

```
row.source_id == null → "—"
current === null      → "unknown"
current               → "yes"
otherwise             → "no"
```

**Why "—" comes before "unknown" (user decision, 2026-10-01).** A row with no `source_id` has no result linked to it, so there's nothing whose currency could be checked. "Unknown" keeps its own meaning: "there is a linked result, but we couldn't check it".

**What "—" does not mean (PR #1006 review).** It is not "this run produced nothing". The write-back RPC's step 9 skips the `written`/`source_id` update for a row already `failed` (`supabase/migrations/20261001220000_resolve_cyl_noop_redelivery_scan_from_source.sql`), so a delivery that lands after reconciliation stores this run's traits but leaves the row `failed` with no `source_id`. The description therefore says only that no result is linked to the row, and tells the reader to check the scan's traits before re-running. Today `NO_OP_NOTE` also covers that case; bloom#900 PR B removes it, after which the description is the only explanation.

**Why a `valueGetter`, not a `valueFormatter`.** The first version decided "—" in `valueFormatter`. DataGrid sorts and filters on the value, not the formatted text, so "—" rows sorted in among "no" (`false`) or "unknown" (`null`) rows, and the column filter couldn't find "—". A `valueGetter` returning the shown string makes display, sort and filter agree. Adding a third state to `current` instead would change a type the live reducer and its tests depend on, just to fix a label.

**Accessibility.** The column's `description` tooltip explains "—". A bare dash means nothing to a screen reader.

**What the archived design asked to keep.** D8 of the archived add-cyl-pipeline-ui design (`openspec/changes/archive/2026-10-01-add-cyl-pipeline-ui/design.md:225`) treats `source_id` and current-in-trait-views as the handles a future pinned export needs. Both columns stay.

**Live changes still work.** When a row's `source_id` goes from null to a value, the row joins `changed` (`RunDetailLive.tsx:106–115`). Its `current` becomes null, and the cell shows "unknown", as the spec already requires.

## D3. Failed-count link: gated on `counts.F`, not on the label text

`runDisplay` already returns `counts: { N, D, F, U }`. `RunState` links the `/^\d+ failed$/` part only when `failedHref && display.counts.F > 0`. Gating on the number means a future change to the label's wording can't bring back an empty link.

**Callers.** The fix lives in the shared component, so it covers both callers that pass `failedHref`:

- the runs list (`RunRow.tsx:52`);
- the experiment panel (`ExperimentRunsPanel.tsx:214`).

The drill-down header passes no `failedHref`. Each caller's requirement states the rule and has its own scenario.

## D4. Runs list as a `<table>`

The `<ul>`/`<li>` markup becomes `<table>`/`<thead>`/`<tbody>`.

**Styling.** It follows `components/accessions/accession-knn.tsx:109–123`: an uppercase `text-xs` header on `bg-stone-50`, and rows divided by `border-t border-stone-100`. That file's `max-h-96 overflow-y-auto` wrapper is not copied, because the list pages with "Load older runs". It uses `overflow-x-auto` instead, for narrow screens.

**Columns.** The headings are exactly the four the issue names. The requester and the elapsed time stay inside the Target and Run cells, as they are today.

**Test hooks.** `<tr>` keeps `data-testid="run-N"`, so `row()`, `rowIds()` and `within(row(N))` keep working. `RunRow` and `RunsListLive` change in the same commit, because a `<tr>` inside a `<ul>` is invalid nesting.

**Empty and error states.**

- With no rows, the empty message replaces the table.
- A failed resync's error sits above the rows already held. That is today's behaviour (`RunsListLive.test.tsx:452–463`).

## D5. One pluraliser, and full sentences where the wording changes

**One shared helper.**

- `run-text.ts` exports `plural(n, one)`.
- `targetText` uses it in place of its inline ternary. The output is unchanged.
- `ScanSelection` titles its button `plural(ids.length, "selected scan")`.
- `RunPipelineDialog` imports `plural` and deletes its local copy.

**Sentences that change at one.** Some sentences change their wording at one, not just a trailing "s":

- the all-results notice at N = 1;
- the pre-check line at K = 1 and at N = 1;
- the details paragraph at N = 1.

These are written as whole sentences, with their exact text in the spec delta, rather than assembled from fragments.

**The N = 1 wording.** It talks about "this scan" and "the scan", never "1 more scan". With K = 0, "more" would have nothing to be more than. The new strings avoid the dialog's forbidden phrases ("will run", "will be skipped", "reused"). The existing guard test now also renders the N = 1 branches.

**Left alone.** `expression-de-summary.tsx` has its own `plural`, which uses `toLocaleString()` for a different feature.
