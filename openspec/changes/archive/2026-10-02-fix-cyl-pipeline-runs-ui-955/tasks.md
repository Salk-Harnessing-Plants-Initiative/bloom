# Tasks: fix-cyl-pipeline-runs-ui-955

## Conventions

**TDD.** Each group opens with tests.
- A **red** test fails today for the reason given. Run it, see it fail, implement, and see it pass.
- A **guard** or **characterisation** test passes before and after the change. Each one is labelled, so nobody goes looking for a red.

**Commits.**
- C0 is the OpenSpec scaffold: `docs(openspec): propose fix-cyl-pipeline-runs-ui-955 (bloom#955)`. Review edits go in `docs(openspec): revise … after review`.
- Groups 1–6 are one commit each: test and implementation together, with the red evidence in the body.
- Group 7 ends with a `docs(openspec): tick …` commit.
- Group 0 makes no commit.
- Group 8 runs after merge and is recorded in the archive PR.
- The web unit suite is green after every commit.

**Issue references.**
- Commit bodies, the PR title and the PR body say "Part of #955".
- None of them puts "close(s/d)", "fix(es/ed)" or "resolve(s/d)", with or without a colon, directly before any `#<n>`. `auto-close-issues-on-staging.yml` scans the PR title and body. GitHub's own keywords fire from commit messages once staging is promoted to `main`.

**Style.** Match the touched files' double quotes and semicolons. Do **not** run Prettier, `npm run format` or `/fix-formatting` on them. `.prettierrc.json` (`semi:false, singleQuote:true`) conflicts with these files and would rewrite them. CI enforces neither ESLint nor Prettier.

**Paths.** Paths are relative to `web/`. Run tests from `web/` with `npx vitest run <paths>`.

## 0. Worktree setup (no commit)

- [x] 0.1 Run `npm ci` at the worktree root, which is the npm workspace that holds `package-lock.json`.
  - This creates the root `node_modules` (which `web/vitest.config.ts` aliases react to) and `web/node_modules` (web pins its own react).
  - It has no postinstall hooks beyond esbuild and unrs-resolver.
  - CI uses Node 20; the local machine runs Node 22, which is fine for vitest.
- [x] 0.2 Baseline: `npx vitest run app/app/cyl-pipeline-runs components/cyl-pipeline lib/cyl-pipeline` is green before any change.

## 1. One pluraliser (`lib/cyl-pipeline/run-text.ts`)

- [x] 1.1 **Red.** New file `lib/cyl-pipeline/run-text.test.ts`:
  - `plural(0, "scan")` → `"0 scans"`, `plural(1, "scan")` → `"1 scan"`, `plural(2, "scan")` → `"2 scans"`, `plural(1, "selected scan")` → `"1 selected scan"`.
  - It fails because `plural` isn't exported. The import is `undefined` and the call throws a TypeError.
- [x] 1.2 **Characterisation** (green today), in the same file:
  - `targetText`:
    - `scan_ids` with count 1 → `"1 selected scan"`; with count 3 → `"3 selected scans"`;
    - `scan` 577 with count 1 → `"scan 577 · 1 scan"`;
    - `experiment` 5 with count 40 → `"experiment 5 · 40 scans"`;
    - `experiment`, null id, count 40 → `"experiment · 40 scans"`.
  - `requesterText`: null → `"unknown requester"`; the current user → `"you"`; anyone else → `"another member · "` followed by the first 8 characters.
- [x] 1.3 Export `plural` and use it in `targetText`'s `scan_ids` branch.
  - Update the module header (`:1`) to say it also provides the shared pluraliser.
  - Update the `targetText` doc (`:7`) to mention "1 selected scan".

## 2. Failed count links only when F > 0 (`components/cyl-pipeline/RunState.tsx`)

- [x] 2.1 **Red.** New file `components/cyl-pipeline/RunState.test.tsx` (jsdom). Use exact link names, e.g. `{ name: "0 failed" }`, never `/0 failed/`. Every case passes `failedHref`:

  | status | N | D | F | expected |
  |---|---|---|---|---|
  | `failed` | 3 | 0 | 0 | text has "0 failed"; `queryByRole("link")` is null |
  | `complete` | 40 | 30 | 0 | "Ended · 30 succeeded · 0 failed · 10 without a result"; no link |
  | `partial` | 10 | 7 | 0 | no link |

  These fail today.
- [x] 2.2 **Guard.** Same file:
  - `complete`, N=40, D=37, F=3 → a link named "3 failed" pointing at the href.
  - `failed`, N=40, D=0, F=5 → a link named "5 failed".
  - Without `failedHref`, F=3 → no link.
- [x] 2.3 **Red, list level** (`app/app/cyl-pipeline-runs/RunsListLive.test.tsx`). Run 7 is `failed` with N=3, D=0, F=0:
  - `row(7).textContent` contains "Failed · 0 succeeded · 0 failed · 3 without a result";
  - `within(row(7)).queryByRole("link", { name: "0 failed" })` is null.
  - The existing "3 failed" href assertion (`:425–431`) stays.
- [x] 2.4 **Red, panel level** (`components/cyl-pipeline/ExperimentRunsPanel.test.tsx`). Covers the panel scenarios:
  - an F=0 `failed` run has no "0 failed" link;
  - run 91 with F=3 links "3 failed" to `/app/cyl-pipeline-runs/91?status=failed`.
- [x] 2.5 Gate the link on `failedHref && display.counts.F > 0` (design D3). Update the doc comment to say: `With failedHref, the "F failed" part links there when F > 0.`

## 3. Runs list as a table (`app/app/cyl-pipeline-runs/{RunRow,RunsListLive}.tsx`)

- [x] 3.1 **Red** (`RunsListLive.test.tsx`), with runs loaded:
  - `getAllByRole("columnheader").map(h => h.textContent)` equals `["Run", "Target", "Experiments", "State"]`, and each header has `getAttribute("scope") === "col"`;
  - `row(91).tagName === "TR"`;
  - `within(getByRole("table")).getAllByRole("row")` has length equal to the number of runs + 1.
- [x] 3.2 **Guard** (no `<table>` exists today):
  - No runs → "No pipeline runs yet", and `queryByRole("table")` is null.
  - "Only mine" with no runs → "You haven't started any pipeline runs", and no table.
  - Runs loaded, then a failed resync → the alert and the table both render, with `row(91)` present. This extends the case at `:452–463`.
- [x] 3.3 Render `<table>`/`<thead>`/`<tbody>` in `RunsListLive`, and `<tr data-testid="run-N">` with four `<td>`s in `RunRow` (design D4).
  - Both files change in this one commit.
  - Wrap the table in `overflow-x-auto`.
- [x] 3.4 The existing `row()`, `rowIds()` and `within(row(N))` assertions, the "Load older" and "Only mine" tests (`:177–330`), and `page.test.tsx` all pass unchanged. If any of them needed a change, say why in the commit.

## 4. Plain integers in the drill-down (`app/app/cyl-pipeline-runs/[runId]/RunScansTable.tsx`)

- [x] 4.1 **Red** (`RunScansTable.test.tsx`). Read cells by index, as the existing tests do. Use the fixture from the spec scenario "Ids are shown as plain integers":
  - `cells[0]`, `[2]`, `[3]`, `[5]` and `[8]` equal "12894712", "9999", "1000", "1200" and "1048576" exactly.
  - It fails today with "12,894,712" (Node's default en-US locale).
- [x] 4.2 **Red, then guard.** Export the column definitions as `scanTableColumns` and the formatter as `plainInteger`. Covers the scenario "Integer columns stay numeric":
  - `scan_id`, `wave_number`, `plant_age_days`, `attempts` and `source_id` each have `type: "number"`;
  - `plainInteger(12894712) === "12894712"`, `plainInteger(0) === "0"`, and `plainInteger(null) === ""`;
  - the `scan_id` column's `width` is ≥ 110.

  It is red until the exports and width exist. After that it guards against someone dropping `type: "number"` (design D1), which no sort test in jsdom can catch.
- [x] 4.3 **Characterisation:** an `attempts: 0` row renders "0", and null wave, day and source render "".
- [x] 4.4 Apply `plainInteger` to the five columns and set the Scan width to 110.

## 5. "—" for rows without a source (`RunScansTable.tsx`)

- [x] 5.1 **Red** (`RunScansTable.test.tsx`). The `cells[9]` matrix, from design D2. The dash is U+2014; assert the literal `"—"`.

  | `source_id` | `current` | cell | red today? |
  |---|---|---|---|
  | null | false | "—" | yes ("no") |
  | null | null | "—" | yes ("unknown") |
  | 40 | null | "unknown" | no |
  | 40 | true | "yes" | no |
  | 40 | false | "no" | no |

  - Rewrite the existing case at `:98–103` (`current: null` with the fixture's default `source_id: null`, expecting "unknown") as the `40 / null` row, so it still tests "unknown".
  - Assert that the column's `description` mentions "—".
- [x] 5.2 In the column's `valueFormatter(value, row)`, check `row.source_id` first.
  - Extend the `description` to say: "— means this run recorded no result for the scan."
  - Add a line to the `current` field doc (`:30`) saying the cell shows "—" whenever `source_id` is null.
  - `RunDetailLive` and its tests are unchanged. They mock the table, so the formatter matrix above is the coverage.

## 6. Singular wording in the confirm dialog (needs group 1)

- [x] 6.1 **Red** (`components/cyl-pipeline/ScanSelection.test.tsx`). Use the real-dialog flow already in the file (`:108–116`):
  - Render `<Grid ids={[7]} />`, tick box 7, click "Run selected (1)", then `await settle()`.
  - The level-2 heading's `textContent` is **exactly** "Run the pipeline on 1 selected scan · 1 scan". An exact match is needed because "1 selected scan" is a substring of "1 selected scans".
  - Also add an exact-heading assertion ("Run the pipeline on 3 selected scans · 3 scans") to the existing "Run selected (3)" test.
- [x] 6.2 **Red** (`components/cyl-pipeline/RunPipelineDialog.test.tsx`). Each expected string is the exact text of the named spec scenario:
  - "A single scan that already has results": K = N = 1, `latest = [{ scan_id: 1, max_source_id: 10 }]`.
  - "A single scan with only legacy traits": N = 1, `max_source_id: null`. Check both the pre-check line and the details.
  - "A single scan with no traits at all": N = 1 with no `latest` row.
  - "One scan of many already has results": N = 40, K = 1, L = 0.
  - "Pre-check separates pipeline results from legacy traits": rewrite the existing assertion at `:296–304` (it expects "1 more scans have…") to the singular text.
  - N = 40, L = 2 → "2 more scans have only traits".
- [x] 6.3 **Tighten existing guards:**
  - The L = 0 check at `:311` becomes `not.toContain("only traits without a recorded source")`.
  - The forbidden-phrase guard (`:381–387`) also renders the K = N = 1 and N = 1, L = 1 dialogs.
  - "All 12 scans…" (`:315–324`) stays as it is.
- [x] 6.4 Implement (design D5):
  - `ScanSelection` titles with `plural(ids.length, "selected scan")`.
  - `RunPipelineDialog` imports `plural`, deletes its local copy (`:86`), and renders the pre-check sentences from the spec delta.

## 7. Validation (all local; CI runs the first three)

- [x] 7.1 `cd web && npm run test:unit` is green.
- [x] 7.2 `cd web && npx tsc --noEmit` is green. It covers the test files too.
- [x] 7.3 `cd web && NEXT_PUBLIC_SUPABASE_URL=http://localhost:8000 NEXT_PUBLIC_SUPABASE_ANON_KEY=placeholder NEXT_PUBLIC_SUPABASE_COOKIE_NAME=sb-localhost-auth-token npm run build` succeeds.
- [x] 7.4 `openspec validate fix-cyl-pipeline-runs-ui-955 --strict` passes. CI doesn't run this one.
- [x] 7.5 Tick the tasks above in a `docs(openspec): tick …` commit.

## 8. Staging check (after merge; recorded in the archive PR)

- [x] 8.1 Approve the Deploy run at the `staging` environment gate and wait for "Staging deployed successfully!".
  - Confirm that the deployed sha contains the merge commit: `git merge-base --is-ancestor <merge_sha> <deployed_sha>`.
  - A later merge may supersede this run, so don't expect the two shas to be equal.
  - **2026-10-02:** #1006's own run 36960837578 (`1ab65e24`) was cancelled because a later run
    superseded it. Run 36969184325 on `88cbcbf3` logged "Staging deployed successfully!" at
    05:43:45Z, and `git merge-base --is-ancestor 1ab65e24 88cbcbf3` holds.
- [x] 8.2 On `/app/cyl-pipeline-runs`:
  - the Run, Target, Experiments and State headings show;
  - runs 1 and 2, or any run with F = 0, have no "0 failed" link;
  - a run with failures still links. The same holds on an experiment page's runs panel.
- [x] 8.3 In a drill-down with scan ids ≥ 1000:
  - the Scan cell shows the full id, with no separator and no "…";
  - Wave shows no separator;
  - a failed or queued row with no source shows "—".
- [ ] 8.4 Open the dialog on a one-scan selection. It shows "1 selected scan". If that scan already has results, it shows "This scan already has pipeline results…".
  - **8.2 and 8.3 passed; 8.4 needs a re-check (2026-10-02, about 06:20Z, eberrigan in the
    browser on `https://staging.bloom.salk.edu:8443`).** Staging was serving `88cbcbf3` (run
    36969184325); the next Deploy, 36972180555 for #1009, was still waiting at the gate. Targets
    were picked from staging's data, read-only. eberrigan reported each item as passing. One
    screenshot (the runs list's top rows) was shared in the session and is not attached.
    - 8.2: the runs list headings read Run, Target, Experiments, State. Runs 19–22 read
      "Finished · 1 succeeded" with no "0 failed" link, and Target reads "1 selected scan" /
      "scan 12894761 · 1 scan" (both in the screenshot). Runs 1–8 (F = 0) have no failed link.
      Runs 14 (F = 1) and 16 (F = 6) link. Experiment 12880747's runs panel matches.
    - 8.3: run 16's drill-down (11 scans, ids 12894756–12894766, wave 9999). Scan ids show in
      full with no separator, Wave reads 9999, and the six `failed` rows with no `source_id`
      read "—".
    - 8.4, **wrong path checked.** The dialog was opened, and cancelled, from scan 12894766's
      **scan page**. That page titles it "Run the pipeline on scan <id> · 1 scan"
      (`RunPipelineButton`; run 23's screenshot of scan 12894767 shows exactly that). Only the
      accession page's selection ("Run selected (1)", `ScanSelection.tsx:81`) titles it
      "1 selected scan". The "This scan already has pipeline results." line is target-independent
      (`RunPipelineDialog.tsx:88`), and it was reported as shown. So "1 selected scan" in the
      dialog is unverified. Re-check it from
      `/app/phenotypes/2/12880747/12880747/12877043` by selecting scan 12894766 alone. (An
      earlier version of this record said the scan page showed "1 selected scan"; corrected in
      #1023's review.)
- [x] 8.5 In the archive PR, record the deployed sha and what you saw, tick 8.x, and then close #955 by hand. Don't archive before 8.x is recorded.
  **2026-10-02:** 8.1–8.3 recorded above (deployed `88cbcbf3`). With eberrigan's yes, #955 was
  closed by hand with the evidence (issuecomment-5946760656). That comment repeats 8.4's
  "1 selected scan" from the wrong path, so it needs a correction once 8.4 is re-checked.

## 9. Archive-ordering hazard (`openspec validate --strict` cannot see it)

- [x] 9.1 `fix-cyl-noop-redelivery-scan-resolution` (bloom#900; on staging via #1001, not yet archived) also MODIFIES `cyl-pipeline-ui` "Per-run drill-down at `/app/cyl-pipeline-runs/[runId]`". This change's block is raised onto that block: its one-line "Failed rows" and its no-op scenario are copied byte-identical, and the only line of theirs not kept is the "current in trait views" bullet this change rewrites. An HTML note above the block says so.
- [x] 9.2 Archive `fix-cyl-noop-redelivery-scan-resolution` before this change, or in the same PR with that change first. Then `diff` the archived drill-down requirement against this block's intent, and record the result here. If that change's block is revised before archiving, raise this block again first.
  **2026-10-02:** `fix-cyl-noop-redelivery-scan-resolution` is archived first, in the same PR
  (`2026-10-02-fix-cyl-noop-redelivery-scan-resolution`). Its block was not revised after 9.3.
  The live `openspec/specs/cyl-pipeline-ui/spec.md` drill-down block `diff`s against this
  change's block as only this change's edits: the "current in trait views" bullet replaced,
  then the plain-integer paragraph and seven scenarios added (32 new lines including the
  replacement). Nothing to raise. No other unarchived change touches any of this change's four
  requirements. After `openspec archive`, all four blocks in `openspec/specs/cyl-pipeline-ui/spec.md`
  `diff` byte-identical to this change's delta. `isolate-cyl-pipeline-environments`' only
  `cyl-pipeline-ui` requirement ("Starting pipeline runs can be switched off per environment") is
  untouched by it. `openspec validate --all --strict` fails only the same nine pre-existing
  changes (eight bloommcp, one langchain) that fail on `88cbcbf3`.
  `openspec archive` carried this change's HTML note ("This block is raised onto …", the
  `<!-- -->` above the delta's drill-down block) into the live spec. It landed above "Run display
  state is derived from counts first". #1023's review found it; it was deleted from
  `openspec/specs/cyl-pipeline-ui/spec.md`, so the live spec has no HTML notes.
- [x] 9.3 Raised again (2026-10-01, bloom PR #1008, which revised the noop change's drill-down block
  after review: two-bullet "Failed rows" with a late-result note, a matched-result note, three
  scenarios). This block is now that block plus only this change's edits: the "current in trait
  views" bullet, the plain-integer paragraph, and seven scenarios (diff: 1 line replaced, 32 added).

## 10. PR #1006 review, round 1 (Important 1–3)

- [x] 10.1 **Red** (`RunScansTable.test.tsx`). The column's `description` equals the spec delta's text exactly ("— means no result is linked to this row. …"). Fails today: the old text says "this run recorded no result for the scan".
- [x] 10.2 **Red** (`RunScansTable.test.tsx`). Covers the scenario "The column sorts by what it shows": four rows reading "no", "—", "no", "—"; click the column header to sort; the "—" rows are adjacent and the "no" rows are adjacent. Also, the column's `valueGetter(false, { source_id: null })` returns "—". Fails today: sorting uses the raw `false`, so the order doesn't change.
- [x] 10.3 Replace the column's `valueFormatter` with a `valueGetter` returning the shown string, and reword its `description`. Update the `current` field doc and the `RunDetailLive.tsx` header comment (`:19-22`) to point at it. `RunDetailLive`'s computation is unchanged.
- [x] 10.4 **Red** (`RunPipelineDialog.test.tsx`). "0 of 40" (K = 0, N = 40, L = 0): the pre-check line reads "0 of 40 already have pipeline results.", and the details paragraph is exactly "All 40 will be sent; the cluster skips scans it has already processed with the same models and code." Red by mutation: changing `precheckLine`'s `N === 1` to `K === 0` must fail it. Confirm, then revert the mutation.
- [x] 10.5 Tighten the details assertions to exact equality on the details `<p>`: the 38-of-40 scenario and both N = 1 scenarios.
- [x] 10.6 **Guard** (`RunsListLive.test.tsx`). "A failed resync keeps the held runs": the alert comes before the table in document order.
- [x] 10.7 The web unit suite, `tsc --noEmit` and `openspec validate --strict` all pass.

