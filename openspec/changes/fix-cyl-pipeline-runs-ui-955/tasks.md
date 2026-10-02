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

- [ ] 8.1 Approve the Deploy run at the `staging` environment gate and wait for "Staging deployed successfully!".
  - Confirm that the deployed sha contains the merge commit: `git merge-base --is-ancestor <merge_sha> <deployed_sha>`.
  - A later merge may supersede this run, so don't expect the two shas to be equal.
- [ ] 8.2 On `/app/cyl-pipeline-runs`:
  - the Run, Target, Experiments and State headings show;
  - runs 1 and 2, or any run with F = 0, have no "0 failed" link;
  - a run with failures still links. The same holds on an experiment page's runs panel.
- [ ] 8.3 In a drill-down with scan ids ≥ 1000:
  - the Scan cell shows the full id, with no separator and no "…";
  - Wave shows no separator;
  - a failed or queued row with no source shows "—".
- [ ] 8.4 Open the dialog on a one-scan selection. It shows "1 selected scan". If that scan already has results, it shows "This scan already has pipeline results…".
- [ ] 8.5 In the archive PR, record the deployed sha and what you saw, tick 8.x, and then close #955 by hand. Don't archive before 8.x is recorded.

## 9. Archive-ordering hazard (`openspec validate --strict` cannot see it)

- [x] 9.1 `fix-cyl-noop-redelivery-scan-resolution` (bloom#900; on staging via #1001, not yet archived) also MODIFIES `cyl-pipeline-ui` "Per-run drill-down at `/app/cyl-pipeline-runs/[runId]`". This change's block is raised onto that block: its one-line "Failed rows" and its no-op scenario are copied byte-identical, and the only line of theirs not kept is the "current in trait views" bullet this change rewrites. An HTML note above the block says so.
- [ ] 9.2 Archive `fix-cyl-noop-redelivery-scan-resolution` before this change, or in the same PR with that change first. Then `diff` the archived drill-down requirement against this block's intent, and record the result here. If that change's block is revised before archiving, raise this block again first.

