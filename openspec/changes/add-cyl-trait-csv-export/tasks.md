**Landing plan: two PRs to `staging`.**

| PR | Title | Sections | Depends on |
|---|---|---|---|
| A | "Export an experiment's or scan's traits, one recipe per file (export jobs and routes)" | §0–10 | Its base contains #976. §7 and §10 need #976 deployed to staging. |
| B | "Download traits from the traits and scan pages" | §11 | PR A merged (#996). #965 (merged) owns the scan page and its `page.test.tsx`; 11.5 extends it. |

**PR A.**
- **The local proposal commits.** Before its first push, fold them into one `docs(openspec): propose add-cyl-trait-csv-export` commit, whose body describes the final design. Rebase onto the current `origin/staging` at the same time. Squash merges keep every commit body, so stale round-by-round messages would land in history.
- **Draft, then ready.** Open it as a **draft** once §0–6 pass locally. Mark it ready only after §7, §9 and §10, each with the user's go-ahead.
- **Proposal and code together.** It carries this proposal with its implementation, so it is not a proposal-only PR.
- **Say what's missing.** Its body notes that PR B implements the dialog requirement.

**PR B** is branched from a freshly fetched `origin/staging` after A merges. It is never stacked.

**Merging is the user's.** Before asking the user to enqueue, confirm that the non-required Vitest, Build and python-audit jobs are green. CI requires only the compose and migration-lint checks.

**Nothing closes #865 automatically.** After §12.1 for both PRs, draft a closing comment for the user to approve; the user closes it.

**Rules for every task:**
- **Red first.** Tests under **Test first** are written and seen failing first, against a stub that exports the signature. A failure from the stub's `not implemented` throw, or from a wrong return value, counts; a collection or import error doesn't. Record the split (N failed / M passed) in the task's done note and in the commit body before implementing.
- **Characterization tests.** A test that can't fail first is marked `(characterization)`.
- **Commits.** A test and its implementation share a commit, so every pushed head is green locally.
  - The pre-commit hook isn't installed, so run the checks yourself.
  - Stage files by explicit path.
  - Every commit body and PR body says "Part of #865". Never use a closing keyword, even behind a conventional prefix (`fix: #123` matches). Review fixes say "addresses review".
- **Web tests.** Colocated Vitest files.
  - Component tests declare `// @vitest-environment jsdom`. Zip, stream, job and route tests stay in the `node` environment.
  - Every test that touches the export registry resets it. An `afterEach` asserts that no jobs are running, no slots are held and no timers are pending.
  - Run one file with `cd web && npx vitest run <file>`. Type-check with `npx --no-install tsc --noEmit`.
  - Use only Node 20 APIs.
- **Placement.** Helpers never live in `route.ts` or `page.tsx`. Nothing goes under, or imports from, the `cyl-pipeline-ui` guard directories.
- **Golden files** are hand-written from design D4–D6, and never regenerated from the implementation.
- **Prettier.** Run it only on this PR's new source files: never on `__fixtures__/`, and not on `openspec/**`.

## PR A

## 0. Preconditions

- [x] 0.1 Run `npm ci`. Read `node_modules/next/dist/docs/` on:
  - route handlers and dynamic `params`;
  - `HEAD` auto-implementation;
  - `request.signal`;
  - module instances across route handlers.

  Confirm the installed `@supabase/supabase-js` version and the `rpc` options `count`, `head` and `.abortSignal()`, plus the `accessToken` client option and `auth.getUser(jwt)`. Record any divergence from `web/app/api/cyl/scans/[scanId]/video/route.ts`.
  **(done 2026-09-30: next 16.3.6 (not 16.3.4 as reviewed); supabase-js, postgrest-js and auth-js 2.106.2. Confirmed in the installed typings: `abortSignal(signal: AbortSignal)`, `accessToken?: () => Promise<string | null>`, `count?: 'exact' | 'planned' | 'estimated'`, `getUser(jwt?: string)`. Route handlers take `params` as a promise and may export `HEAD`, as the video route does. Local Node is v22; production runs node:20-alpine, so only Node 20 APIs are used.)**
- [x] 0.2 From the root, run `npm install --save-exact fflate@<current> -w web`. Confirm that the lock diff contains only `fflate`, because a Windows install can drop Linux optional binaries. Then run `npm ci`, `npm audit --audit-level=critical` and `cd web && npm run build`. Record the version.
  **(done 2026-09-30: fflate 0.8.3 pinned exactly; the lock diff is the one `node_modules/fflate` entry. `npm audit --audit-level=critical` exits 0 (0 critical; 2 low, 7 moderate, 7 high, all pre-existing, none in fflate). A scratchpad `Zip` + synchronous `ZipDeflate` two-entry zip passes `python -m zipfile -t`. `npm run build` passes with CI's placeholder `NEXT_PUBLIC_*` env; without it, prerendering `/test` fails on the missing Supabase URL, which predates this change.)**
- [x] 0.3 Confirm that #976's staging Deploy has completed, and that staging PostgREST serves `list_trait_recipes` (read-only, as a staging `bloom_user`). §7 and §10 wait on this; the draft PR doesn't.
  **(done 2026-10-01: #976 reached staging in Deploy run 36792301776 at `618cbeb8` (#965), after the runs for #976 and #982 were cancelled as superseded. As a new staging `bloom_user` (bloomctl profile `staging-user`, JWT role `bloom_user`), `list_trait_recipes(experiment_ids_ => [1])` returned 8 rows: 7 `pipeline` recipes (1 or 3 scans each; the default is `1911b908…`, newest source 233) and `legacy:5` (13,396 scans). The `pipeline-staging` profile is `bloom_workflows` and gets `42501`, as the grant intends.)**
- [x] 0.4 Append `web/lib/cyl-trait-export/__fixtures__/** -text` to `.gitattributes`, after the `*.csv`/`*.json text eol=lf` lines.
  - Extend the whitespace-hook excludes to `^(services/workflows/vendored/|web/lib/cyl-trait-export/__fixtures__/)`.
  - Add `web/lib/cyl-trait-export/__fixtures__/.*` to the prettier exclude regex and to `.prettierignore`.
  - Check with `git check-attr text -- web/lib/cyl-trait-export/__fixtures__/golden/K.csv`, expecting `unset`.
  - **Commit this before staging any `__fixtures__` file.**
  **(done 2026-09-30: committed alone, before any fixture. The rule is `-text -eol`; `git check-attr text eol` on `golden/K.csv` reports both `unset`.)**
- [x] 0.5 Check that the dev DB carries the merged `20260930120000`–`0300` migrations (`make migrate-local`).
  **(done 2026-09-30: `supabase_migrations.schema_migrations` on bloom_v2_dev lists 20260930120000–0300; `list_trait_recipes`, `get_trait_recipe_coverage`, `_cyl_trait_recipe_presence` and `cyl_trait_recipe_key_v1` exist, and there is one `get_experiment_traits` with the 5-argument signature.)**

## 1. Fixtures and their DB tie

- [x] 1.1 Write `__fixtures__/scan-metadata-parity.json`.
  - **Rows:** shaped like a real staging `cyl_scans_extended` select (anonymised), plus a genotype.
  - **Cases:**
    - `plant_age_days` NULL and 0;
    - `wave_number` NULL;
    - `date_scanned` NULL, and a real value;
    - a NULL accession;
    - NULL `wave_name`, `phenotyper_id` and `uploaded_at`;
    - QR codes with `/`, `:`, `\`, a trailing space, and only dots;
    - a QR code with `\0`, marked synthetic.
  **(done 2026-09-30: 13 cases. Rows use the PostgREST JSON shape of a dev-DB `cyl_scans_extended` row (`row_to_json`); the dev data is mock, not staging, which changes nothing about the shape. Cells come from the worktree's bloomctl 0.1.0a6 (`uv run` in `bloomcli/`), identical to the installed 0.1.0a5 tool. bloomctl renders a NULL age `DayNone`, a NULL date `Day6_None`, a NULL wave `Wave0`, and an all-dots QR `_`.)**
- [x] 1.2 Write `__fixtures__/golden/input.json` in a fixture id space, with scan ids like 9, 10 and 100 so numeric sorting is exercised.
  - **Scans:** at least 2 waves, with ages including 0.
    - 3 scans with pipeline recipe `K`, all in wave 1:
      - one has NULL, `"NaN"`, `"Infinity"`, `"-Infinity"`, `-0`, the float4 nearest `0.1`, and float4 max values;
      - one lacks a trait the others have, and also has an older `K` source with a different observed-only metadata value and one extra trait;
      - two are the same plant at ages 0 and 7.
    - 1 scan with only pipeline recipe `K2` (a higher source id than `K`, so `K2` is the default).
    - 1 scan with only `legacy:9`, with distinct crown-root trait names.
    - 1 scan with only `unattributed` rows, which M2 marks `legacy_only`.
    - 1 scan with no traits.
    - Wave 2 has no `K` scan.
  - **Values.** Every value must be float4-representable, because `cyl_scan_traits.value` is `real`. Trait names include a real `curve_index` name, and names that sort differently by code point than by locale, including one above U+FFFF.
  - **Contents.** The file holds:
    - the experiment row, each scan's `cyl_scans_extended` row and each genotype;
    - the source rows (`id`, `name`, `scan_id`, `recipe_key_version`, `metadata`), with each `recipe_key` computed by `SELECT cyl_trait_recipe_key_v1(...)` on a migrated DB (the query is recorded);
    - for each of `K`, `K2`, `legacy:9` and `unattributed`, the single-call coverage and trait rows;
    - `chunk_listings[size]` for sizes 1, 2 and `|S|`, each a list of `{scan_ids, rows}` in chunk order;
    - `selection_listings` for `wave=2` and `age=0`.
  **(done 2026-09-30: recorded, not hand-written, by `tests/integration/cyl_trait_export_fixture.py`, which seeds the scenario with ids `base + fixture id`, calls the RPCs as `bloom_user` and maps back. Scans 9, 10, 100–103, 200 and 201. `K2` (source 41) is the overall default, `K` wins the `age=0` selection, and wave 2 has no `K`. The superseded source 20 is excluded by M2. PostgreSQL renders the widened float4 values with 15 digits (`0.100000001490116`, `3.40282346638529e+38`, `1.0000000116861e-07`), and `-0` as `0`. `species` has a UNIQUE `(genus, species)`, so the fixture species is `Fixturia exportii`.)**
- [x] 1.2a Write `tests/integration/test_cyl_trait_export_batching.py` **before** 1.3. It follows `tests/integration/test_cyl_trait_recipes_read.py`: self-cleaning, a random id base, and `encoding="utf-8"`.
  - **Seeding.** It seeds `input.json` verbatim (QR codes, waves, ages, dates, plants, and one plant with two scans), not through `_seed_scan_in`'s random values. It maps accession names if a collision occurs.
  - **Role.** It calls the RPCs as `bloom_user` (`SET LOCAL ROLE`, as in `test_cyl_trait_recipes_read.py:496-510`).
  - **Assertions,** under a strictly increasing map from fixture ids to seeded ids (covering scan and source ids, `legacy:<id>` strings, `definition` ids and `available_recipes`):
    - (a) the computed keys equal `K` and `K2`;
    - (b) the single-call coverage and trait rows equal `input.json`, compared as `SELECT json_agg(t) FROM …` renderings;
    - (c) each recorded `scan_ids` is the consecutive slice of ascending `S`, every `chunk_listings` and `selection_listings` entry equals the real call, and the Python merge of per-chunk listings equals the single call, including order and `is_default`;
    - (d) the concatenated per-chunk coverage, and the union of the per-chunk trait rows (each call passing that chunk's included scans), equal the single calls.
  - `(characterization)`. It runs in CI's required compose job (`pytest tests/integration/`). Run `uvx ruff check` and `black --check` on it by hand, because the hooks don't cover `tests/`.
  **(done 2026-09-30, characterization: 10 passed against the dev DB. Proven able to fail: changing one recorded `K2` trait value fails 4 tests, and restoring it passes. Clean under the repo-pinned ruff 0.9.9 and black 26.3.1.)**
- [x] 1.3 **Hand-write** `golden/{K,legacy-9,unattributed}.{csv,export.json,excluded.csv}` from design D4–D6 and the now-verified `input.json`, before any implementation.
  - Fix `generated_at` and `version`.
  - Check that `git ls-files --eol` shows `i/crlf` for the CSVs.
  - Note in the PR that they are hand-authored.
  **(done 2026-09-30: each cell and field was typed by hand in a scratchpad authoring script. Only `scan_ids_sha256` is computed (`ccc7cc77…` for `9,10,100,101,102,103,200,201`). `generated_at` is fixed at `2026-10-02T12:00:00.000Z` and the version at `1.0.0`. `chosen_by` is `user`, because `K2` is the default. `git ls-files --eol` shows `i/crlf attr/-text` for the CSVs. A separate cross-check against `input.json`'s recorded coverage and values (as float4) found no mismatch.)**
- [x] 1.4 Verification, not a committed test. Write a scratchpad script in `bloommcp`'s environment, recording the sleap-roots-analyze, bloommcp and jsonschema versions.
  - **State the expected results before running:**
    - the row count per file;
    - NaN and empty cells both load as NaN;
    - `recipe_key`, `source_id` and the metadata columns are not trait columns;
    - `genotype` and `plant_qr_code` are detected.
  - **Then run:**
    - `jsonschema.Draft202012Validator` on each golden sidecar, parsing `generated_at` as RFC 3339;
    - `load_trait_data(path, barcode_col="plant_qr_code", genotype_col="genotype", replicate_col="scan_id")`, recording `get_trait_columns` (is `curve_index` dropped?);
    - `qc_clean` with `csv_content`, recording `genotype_column`, `sample_id_column`, `replicate_column`, `kept_trait_columns` and `validation_warnings` (`bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/qc_clean.py:233`).
  - Record the results here. Any golden-file change needs a recorded reason, and is committed before any implementation.
  **(done 2026-09-30: sleap-roots-analyze 0.1.0a5, bloommcp 0.1.0a1, jsonschema 4.26.0, pandas 3.0.2.**
  - **E1: all three sidecars validate, and `generated_at` is UTC.**
  - **E2: 3, 1 and 1 rows.**
  - **E3: an empty cell and `NaN` both load as NaN, `Infinity`/`-Infinity` as ±inf, `recipe_key` as str and `source_id` as int64.**
  - **E4: no metadata column, `recipe_key` or `source_id` counts as a trait.**
  - **E5: `curve_index_median` is dropped by `get_trait_columns` (the substring `index`), as predicted; this goes in the 8.2 docs and the 12.3 upstream issue.**
  - **E6: `qc_clean` raises on these 1–3-sample goldens, before reporting roles ("only 1 sample(s) remain", even with relaxed thresholds). Its resolver `resolve_columns` gives `genotype` / `plant_qr_code` / `wave_number` on K. The full `qc_clean` run moves to 10.1's real export. No golden change was needed.)**
- [x] 1.5 **Test first.** Extend `tests/unit/test_trait_recipe_export_schema.py`: every `golden/*.export.json` has the schema's required keys at every level, and no property the schema doesn't declare.
  **(done 2026-09-30, characterization: the goldens predate the test, so it passed at once (5 passed). Proven able to fail: adding a `rogue` key to `K.export.json` fails it, and restoring the file passes.)**

## 2. Metadata

- [x] 2.1 **Test first.** `web/lib/cyl-trait-export/metadata.test.ts`:
  - `METADATA_COLUMNS` equals the fixture's 22 names in order;
  - `metadataCells(row, genotype)` equals every parity row's cells;
  - `safeComponent` covers `""`, `"."`, `".."`, `"..."`, `\`, `/`, `:` and `\0`;
  - `plant_age_days: 0` gives `Day0`.
- [x] 2.2 Implement `metadata.ts`.
  **(done 2026-09-30: red 29 failed / 0 passed against the throwing stub; green 29 passed. tsc clean.)**

## 3. Selection, merge, limits and state

- [x] 3.1 **Test first.** `recipes.test.ts`:
  - `mergeRecipeListings` over each `chunk_listings[size]`'s rows equals `chunk_listings[|S|]`;
  - `n_scans` is summed, the other fields come from the batch with the max, and `unattributed` comes from any batch;
  - rows are in `newest_source_id DESC NULLS LAST` order;
  - there is exactly one default, and none for an empty input;
  - a chunk whose `n_scans` exceeds its size is an error;
  - `resolveChosenBy` covers all four combinations.
- [x] 3.2 Implement `recipes.ts`.
  **(done 2026-09-30: red 12 failed / 0 passed (a first run was a collection error from calling the stub inside describe, which doesn't count; moved into the tests); green 12 passed)**
- [x] 3.3 **Test first.** `selection.test.ts`:
  - `chunk` at sizes 1, an exact multiple, and larger than the input, numbered from 1;
  - `scanIdsSha256([12,3,7])` equals `createHash("sha256").update("3,7,12")`;
  - `buildFilters` for none, wave, age, both, and scan, and with `wave=0`/`age=0` it gives `{wave_number: 0, plant_age_days: 0}`;
  - `selectionScanIds` is null only for an unfiltered experiment;
  - keyset paging, with page size 2 over 6 ids (and an exact multiple), yields each id once and ends only on an empty page;
  - a fake returning half-pages still yields every id;
  - a duplicate id, and an exact count of `|S|+1`, each give the typed "selection changed" error.
- [x] 3.4 Implement `selection.ts` and `limits.ts`: `BATCH_SCANS`, `PG_CONCURRENCY`, `MAX_RUNNING_JOBS`, `MAX_JOBS_PER_USER`, `MAX_HELD_BYTES`, `RUNNING_JOB_RESERVE_BYTES`, `MIN_SESSION_SECONDS`, `EXPORT_MAX_SECONDS` and `RETAIN_SECONDS`.
  **(done 2026-09-30: red 20 failed / 0 passed; green 20 passed. limits.ts also names ABORTED_CALL_HOLD_MS = 9000 and SELECTION_PAGE_SIZE = 1000; errors.ts holds the ExportError type and the SELECTION_CHANGED message)**
- [x] 3.5 **Test first.** `state.test.ts`:
  - the FIFO semaphore of 3 is honoured across concurrent owners;
  - each owner has at most 3 outstanding, and owners interleave;
  - an acquire can be aborted (the waiter leaves the queue);
  - a slot is released on resolve and reject;
  - an aborted in-flight call holds its slot until 9 s after it was issued (fake timers);
  - the registry and the semaphore live on `globalThis[Symbol.for("bloom.cylTraitExport")]`: after `vi.resetModules()` and a second import, a job started through instance 1 is read through instance 2, with one semaphore count.
- [x] 3.6 Implement `state.ts`.
  **(done 2026-09-30: red 8 failed / 0 passed; green 8 passed. The per-owner limit is a pool() helper: an owner's next call only queues when one of its own finishes, so owners interleave under the FIFO semaphore. state also holds the per-user in-flight listing map)**

## 4. CSV, excluded CSV, sidecar and stem

- [x] 4.1 **Test first.** `csv.test.ts`. The float4 formatter:
  - `0.100000001490116` and `0.10000000149011612` → `0.1`;
  - `3.4028234663852886e+38` → `3.4028235e+38`;
  - `12.7` → `12.7`;
  - `-0` → `0`;
  - `1e-7` (as float4) → `1e-7`.

  Every output parses back to the same float4, and none is longer than necessary.

  The writer:
  - `NaN`, `Infinity` and `-Infinity` are written literally;
  - quoting only for `,`, `"`, CR and LF;
  - CRLF after every line, the last included, and no BOM;
  - empty cells for NULL and absent values;
  - code-point trait order (including one name above U+FFFF);
  - numeric `scan_id` order;
  - `recipe_key` in full, and an empty `source_id` for `unattributed`;
  - no `#` lines;
  - a leading `=`, `+`, `-` or `@` left unchanged;
  - fresh `TextEncoder` slices of 50,000 cells whose concatenation equals the golden CSV;
  - a scan export's header holds only that scan's traits.
- [x] 4.2 Implement `csv.ts`, a pivot over per-scan `Uint32Array`/`Float64Array`/`Uint8Array` buffers.
  **(done 2026-09-30: red 38 failed / 0 passed; green 38 passed. The writer reproduces the three hand-written golden CSVs byte for byte at slice sizes 1 and 50,000. The formatter turns the recorded 15-digit widened values back into the float4 shortest forms (0.1, 3.4028235e+38, 1e-7))**
- [x] 4.3 **Test first.** `sidecar.test.ts`:
  - each golden sidecar is byte-identical;
  - `source_ids` is distinct and ascending (`[]` for unattributed, `[9]` for legacy);
  - `excluded` is in `scan_id` order, with `available_recipes` passed through;
  - `other_recipes_in_selection` is exactly `{recipe_key, n_scans}` in listing order;
  - `observed` is present only for pipeline recipes, sorted by code point, with `inference_configs` in canonical-JSON order;
  - two `definition`s that differ only in key order serialize identically;
  - `generated_at` is `toISOString()`;
  - the injected version is `1.0.0`, with `+sha` only when `BLOOM_WEB_BUILD_SHA` is set;
  - a filtered selection has the full ascending `scan_ids`, the matching hash, and `buildFilters`' `filters`;
  - given coverage missing a scan of `S`, or holding an extra one, the builder throws the typed (i) error;
  - `excludedCsv` equals its golden files, header-only included.
- [x] 4.4 Implement `sidecar.ts` and `excluded.ts`.
  **(done 2026-09-30: red 16 failed / 0 passed; green 16 passed, byte-identical to the three hand-written golden sidecars and excluded CSVs)**
- [x] 4.5 **Test first.** `stem.test.ts`:
  - `diversity-screen_wave3_day0_legacy-12345_20261002`;
  - a 64-hex key, `unattributed`, and a scan stem;
  - an all-symbol name → `experiment-<id>`;
  - a 200-character name cut to 60 with no trailing `-`;
  - an injected `Date` at 23:30 in UTC−8, which gives the next UTC day;
  - every output matches `^[a-z0-9_-]+$`.
- [x] 4.6 Implement `stem.ts`.
  **(done 2026-09-30: red 14 failed / 0 passed; green 14 passed)**

## 5. Build, zip and jobs

- [x] 5.1 **Test first.** `build-export.test.ts`, against the D10 fake (listings served only for recorded `scan_ids`):
  - (a) outputs equal the golden files for `K`, `legacy:9` and `unattributed`:
    - at `BATCH_SCANS` 1, 2 and 100;
    - with calls resolved in issue, reverse and seeded-random order;
    - with rows shuffled within each response.
  - (b) every call has `experiment_ids_ = [e]` (or `experiment_id_ = e`) and a non-empty `scan_ids_` of at most `BATCH_SCANS` ids. Coverage and trait calls have a non-NULL `recipe_key_`. Trait calls select exactly the 4 columns with `count: "exact"`. A batch with no included scans makes no trait call.
  - (c) with calls held on deferreds, the in-flight count reaches exactly `PG_CONCURRENCY` and never exceeds it.
  - (d) each of the following fails with its typed error and produces no output:
    - each integrity failure of D2 step 6: (a)–(h), (j) (a missing accession row) and (k) (a missing source row);
    - a truncated trait response (count = rows + 1);
    - each RPC error: `57014`, `PGRST202` and `PGRST301`.

    The `detail` follows D7: it names the scan where one applies, and "batch i of n" as the 1-based chunk. For (b) and (d) it says "selection changed … retry"; `PGRST202`/`PGRST301` get their fixed messages. It contains none of the fake's `message`, `details` or `hint`.
  - (e) a key absent from the merged listing (`selection_listings` wave 2 with `K`) fails with "this recipe is not in the selection" and makes no coverage call.
  - (f) `chosen=default` for `K` while `K2` is the default gives `K` with `chosen_by: "user"`; `chosen=default` for `K2` gives `chosen_by: "default"`.
  - (g) a scan build of an included scan equals its experiment row by column name, with the experiment-only trait columns empty. `selection` is `[s]` with `{scan_id: s}`.
  - (h) aborting stops new batches and aborts in-flight calls.
  - (i) the superseded `K` source's id, extra trait and observed-only value appear nowhere, and `observed` reads only included source ids, in chunks.
  - (j) `K`'s header has none of the crown trait names.
- [x] 5.2 Implement `build-export.ts`.
  **(done 2026-09-30: red build-export 50 failed / 0 passed; green 50 passed. First green run had 6 failures in the random-order variants: the fake was shuffling keyset pages, which come from an ORDER BY scan_id query and are never unordered, so pageSelection correctly rejected them. The fake now shuffles only unordered results, and trait rows, which it had not been shuffling. Added the supabase-js adapter createExportDb in db.ts with db.test.ts: red 12 failed / 0 passed, green 12 passed (query shapes, captured token, abort signal, semaphore peak 3, {code,message} errors, empty scan ids refused))**
- [x] 5.3 **Test first.** `zip.test.ts` (node env): `zip.ts` takes an `AsyncIterable<Uint8Array>` per entry and uses synchronous `ZipDeflate`, yielding with `setImmediate` between slices.
  - Unzipped with `fflate.unzipSync`, the output holds exactly the three names with the golden bytes.
  - No input chunk exceeds the slice size.
  - A mid-stream error rejects.
- [x] 5.4 Implement `zip.ts`.
  **(done 2026-09-30: red 4 failed / 0 passed; green 4 passed. Synchronous ZipDeflate with a setImmediate yield per slice; an abort terminates the zip and throws cancelled)**
- [x] 5.5 **Test first.** `jobs.test.ts`, with fake timers, an injected clock and an injected build function:
  - **Lifecycle:**
    - start → `running` with progress → `ready`, with a UUID id;
    - another user → not found.
  - **Retention:**
    - a finished job is present at 599 s and absent at 601 s by exact lookup, whatever the sweep timing;
    - a user's accepted new job drops their previous finished job;
    - a refused start keeps it.
  - **Limits:**
    - held bytes are ready zips plus `RUNNING_JOB_RESERVE_BYTES` per running job, excluding the caller's own finished job, and are refused at the boundary;
    - `MAX_RUNNING_JOBS` and `MAX_JOBS_PER_USER` are enforced, with the slot released after success, failure, cancel and deadline;
    - a per-user refusal returns the running `job_id`.
  - **Cancel and deadline:**
    - cancel → `cancelled`, with the slot released and in-flight calls aborted;
    - `DELETE` of a finished job drops it;
    - the deadline timer fails a job whose build never settles, and releases the slot;
    - a non-typed build error gives `failed` with a generic `detail`, and releases the slot.
  - **Exposure:**
    - the status JSON's keys are exactly the whitelist;
    - the token and client are dropped at the terminal state.
- [x] 5.6 Implement `jobs.ts`.
  **(done 2026-09-30: red 16 failed / 0 passed; green 16 passed. A reservation counts as running until started or released, so the pre-202 selection can't race the limits; the sweep timer lives on the shared state and is cleared by the test reset)**

## 6. Routes

- [x] 6.1 **Test first.** `web/app/api/cyl/trait-export/jobs/route.test.ts`. Mock the auth helper so that `getUser(token)` runs on the captured token.
  - `dynamic`/`runtime` are set, and `HEAD` → `405`.
  - **The D7 order:**
    1. `403` for `Sec-Fetch-Site: cross-site`;
    2. `401` with no session or a rejected token, with no slot taken;
    3. `503` when GoTrue errors;
    4. `401` at 1,799 s left on the verified token, and acceptance at 1,800 s;
    5. `422` for: `age=-1`, `07`, `1.5`, `+7`, `1e3`, a repeated `age`, 16 digits, `experiment=0`, `wave` with `scan`, neither or both of `experiment`/`scan`, a bad `recipe`, or a missing `chosen`;
    6. `429` for the per-user limit (with the `job_id`), the global limit and the memory budget;
    7. `404` for an invisible or deleted experiment, a scan's deleted experiment, or an empty selection;
    8. `409` for a selection count mismatch;
    9. `502` for a selection read error.

    Steps 7–9 release the slot.
  - `age=0` and `wave=0` are accepted and reach the query as `.eq("plant_age_days", 0)`.
  - Every refusal before step 7 makes zero `from`/`rpc` calls.
  - Success returns `202 {job_id}`.
- [x] 6.2 Implement `jobs/route.ts` and `web/lib/cyl-trait-export/request.ts` (identity, guard and parameters).
  **(done 2026-09-30: red 35 failed / 1 passed (the module-contract test passes against the stub's exports); green 36 passed after one fix: the session floor compared a whole-second JWT exp with fractional now, so exactly 1,800 s left read as 1,799.x; it now compares whole seconds. 499 is returned if the client aborts during the pre-202 selection)**
- [x] 6.3 **Test first.** Status, download and `DELETE` route tests.
  - **Each route:**
    - `403` cross-site;
    - `401`/`503` identity;
    - `404` for a non-UUID, unknown or non-owner id (a non-owner `DELETE` doesn't cancel);
    - `HEAD` → `405`.
  - **Status** returns the whitelisted JSON.
  - **Download:**
    - `409` while the job is `running`, `failed` or `cancelled`;
    - once ready, `200` with the three headers plus `Content-Length`, and a streamed body that unzips to the golden files, repeatable within retention;
    - `404` after retention.
  - **`DELETE`** returns `204`.
  - **One chained test:** POST → status `running` → `ready` → download → unzip → golden.
- [x] 6.4 Implement both routes.
  **(done 2026-09-30: red 15 failed / 0 passed; green 15 passed. The chained test runs POST, status polling and download through the real build and zip over the golden fake: the CSV and excluded CSV are byte-identical to the goldens and the sidecar equal apart from generated_at)**
- [x] 6.5 **Test first.** `recipes/route.test.ts`:
  - the guard runs without the session floor and job limits, and without `recipe`/`chosen`;
  - every `list_trait_recipes` call has `experiment_ids_ = [e]` and a non-empty `scan_ids_` of at most `BATCH_SCANS` ids;
  - `n_selected` and the merged rows are correct at batch sizes 1 and `|S|`;
  - `wave=2` and `age=0` match `selection_listings`;
  - an empty selection returns `200 {n_selected: 0, rows: []}`;
  - an all-`no_traits` selection returns no rows;
  - a deleted experiment returns `404`;
  - a failed batch returns `502`, whose `detail` names the code and "batch i of n" and contains none of the fake's raw text;
  - a user's newer listing aborts the older one;
  - `request.signal` aborts the calls;
  - `HEAD` → `405`.
- [x] 6.6 Implement `recipes/route.ts`.
  **(done 2026-09-30: red 17 failed / 0 passed (with 6.7 in the same file, 18 failed); green 18 passed. The file mocks BATCH_SCANS to 1, so listings run 8 batches and the limit is exercised; the batch-size-1 listings the tests need are all recorded)**
- [x] 6.7 **Test first.** The "two jobs and one listing" test: the real `jobs.ts` and recipes handler against one blocking fake. In-flight calls never exceed 3, and the listing's calls interleave with the jobs'.
  **(done 2026-09-30: passes with limitedDb (peak exactly 3). Proven meaningful: a throwaway copy without limitedDb fails at peak 9. Needed the process-wide semaphore moved out of the supabase adapter into limitedDb (refactor b0ea669a) so the fake could be wrapped by the real limiter)**
  **(2026-10-01, after 7.4: listings now run in batches of `LISTING_BATCH_SCANS`, which the route tests do not mock, so the 8-scan fixture lists in one call; the mocked `BATCH_SCANS` of 1 still splits the jobs' coverage and trait calls. 6.5's 502 test now names "batch 1 of 1". 6.7 still passed with peak 3, but the review of #996 showed it no longer checked the listing (a serial listing never contended); 10a.6(a) rewrote it.)**
- [x] 6.8 **Test first.** The listing returns each recipe's `definition`, so PR B's dialog can say what each recipe is.
  **(done 2026-10-01, user decision: without it the dialog could show only key prefixes and counts. Red (`recipes/route.test.ts`): 2 failed, 17 passed; rows had no `definition`. Green: `recipes/route.ts` passes `definition` through from the merged rows; web 2,161/2,161, tsc clean. Spec: the listing requirement lists `definition`, with the new scenario "Each row says what its recipe is", and the dialog requirement says how a recipe is described.)**

## 7. Measurement gate (staging, before PR A is ready)

- [x] 7.0 Land the `get_experiment_traits` plan fix (PR #992, migration `20261001180000`) and its staging deploy before running 7.1.
  **(done 2026-10-01: #992 merged and deployed, Deploy run 36913017384; staging `proconfig` is `join_collapse_limit=11, plan_cache_mode=force_custom_plan`. Through PostgREST as `bloom_user`, 7 calls on one connection: recipe reads 0.07–0.15 s at 10, 50 and 100 scans (were `57014`), the no-key path 0.53–0.59 s at 50 scans (was 3.34 s). bloommcp's shape (source 5, whole experiment 1) still hits `57014` at 8.11 s, as before.)**
  **(found 2026-10-01: the first 7.1 run, as the `staging-user` `bloom_user`, hit `57014` on the first `get_experiment_traits(recipe_key_)` call, at 50 scans and again at 10. A read-only staging `EXPLAIN (ANALYZE, BUFFERS)` showed a seq scan of all 28.9M `cyl_scan_traits` rows (14.1 s): the function joins 9–10 relations, above the default `from_collapse_limit` of 8, so the view is planned on its own and the scan ids never reach it. With both collapse limits at 12: 50 ms, same 2,070 rows. The no-key path (3.3 s per 50 scans, against 0.34 s for the bare view) has the same plan. Migration PRs must be isolated (`scripts/lint_migration_isolation.py`), so the fix ships in #992 ahead of PR A. 7.2 passed in the same run: `expires_in` = 3600.)**
- [x] 7.1 Write a read-only scratchpad script using bloomctl's `make_authed_client` (as for the 2026-09-30 production measurement). For staging experiments 1, 269327, 7206207 and 3313, time the per-batch `list_trait_recipes`, `get_trait_recipe_coverage` and `get_experiment_traits(recipe_key_, 4-column select, count=exact)` at 50, 100 and 200 scans. Record rows, bytes and p50/p95/max. Run it with the user's go-ahead.
  **(done 2026-10-01, `scratchpad/measure_7.py` as `staging-user`: experiments 1 (18,471 scans), 269327 (9,291), 7206207 (2,680) and 3313 (5,547); no errors, no truncation. p50/p95/max in s: `get_experiment_traits` 0.98/1.51/1.58 at 50, 1.77/2.28/2.53 at 100, 3.52/4.30/5.06 at 200 (max 207,000 rows, 22.6 MB); `get_trait_recipe_coverage` ≤ 0.11 at every size; `list_trait_recipes` ≤ 0.40 at every size.)**
- [x] 7.2 Confirm that the hosts don't override `JWT_EXPIRY` (committed as 3600): check a fresh session's `expires_in`, never printing a token. `MIN_SESSION_SECONDS` = 1,800 and `EXPORT_MAX_SECONDS` = 1,500 must fit within it.
  **(done 2026-10-01: a fresh `staging-user` session on staging reported `expires_in` = 3600, so the hosts don't override `JWT_EXPIRY`; `MIN_SESSION_SECONDS` 1,800 and `EXPORT_MAX_SECONDS` 1,500 fit. No token was printed.)**
- [x] 7.3 Set `BATCH_SCANS` to the largest size whose p95 is under 4 s for every RPC.
  **(done 2026-10-01: `BATCH_SCANS` stays 100 (200 fails on `get_experiment_traits` p95 4.30 s). Job time: (0.12 + 0.08 + 1.77) s × 185 ÷ 3 ≈ 121 s at p50, 163 s at p95, within `EXPORT_MAX_SECONDS`. Listing time FAILED: 185 calls through the shared FIFO semaphore, with each job keeping 3 calls queued, is about 65 s with one job and 123 s with two. Resolved by 7.4, not by moving the listing into the job.)**
  - **Job time.** Estimate experiment 1's production job time as the per-batch time × `ceil(18471 / BATCH_SCANS)` ÷ `PG_CONCURRENCY`. If that exceeds `EXPORT_MAX_SECONDS`, stop and bring the durable-job follow-up to the user.
  - **Listing time.** Estimate the listing time with 2 jobs running. If it would exceed 60 s, stop and propose moving the listing into the job.

- [x] 7.4 List recipes in batches of `LISTING_BATCH_SCANS` (20,000) instead of `BATCH_SCANS`.
  **(done 2026-10-01. Measured first on staging: one `list_trait_recipes` call took 0.84 s experiment-wide, 0.45 s for the largest age (3,819 scans) and 1.26 s for all 18,471 scans, with the same 8 recipes and default as the batched listing. Red (`64f077de`): 5 failed, 49 passed; the constant was undefined, 250 scans listed as [100, 100, 50], and `buildExport` listed per `batchSize`. Green: `listMergedRecipes` and `buildExport` default to `LISTING_BATCH_SCANS`; `BuildOptions.listingBatchSize` overrides it. Two tests written for per-batch listings now read the new behaviour (the drift tamper subtracts 1 from K's count; the route's 502 names "batch 1 of 1"). Web 2,160/2,160, tsc clean, integration batching test 10 passed.)**

## 8. Docs

- [x] 8.1 Update `_WIKI/SUPABASE/trait-recipes.md` §"Export sidecar v1" so it states design D4–D6 **in full** for every exporter.
  **(done 2026-09-30: added an Export file conventions subsection (interchangeable, CSV layout, float4 values, selection, orderings, excluded CSV, stem, batched exporters), amended :8, replaced the stem line, edited the four Notes cells, updated Performance boundary and Selections. Example sidecar: scalar filters, 13 scan_ids, real hash a4d95875…, key-sorted free-form objects. Schema unit test 5 passed)**
  - **Rules to add:**
    - the "interchangeable" definition;
    - float4 value spelling;
    - the batched-exporter rules: explicit key on coverage and trait calls, never a NULL `scan_ids_`, the D2 listing merge, the exact-count checks, and fail rather than write a partial file.
  - **Edits to existing text:**
    - amend `:8` so it says the export conventions are defined on this page;
    - replace the stem line (`:114`);
    - edit the Notes cells of `selection.scan_ids`, `selection.filters`, `selection.scan_ids_sha256` and `recipe.chosen_by`;
    - update the "Performance boundary" and "Selections" notes.
  - **Don't add** a table whose header starts `| Field | Source |`, or new field rows.
  - **Example sidecar.** Update `trait-recipes.export.example.json` with scalar `filters`, the full `scan_ids` and a real `scan_ids_sha256`.
  - **Then run** `uv run --extra test pytest tests/unit/test_trait_recipe_export_schema.py`.
- [x] 8.2 In the same page, add a "Using a trait export" section:
  **(done 2026-09-30, without the button. The numpy note was checked: str(numpy.float32) gives 0.1 / 90.0 / 1e-07, so the page says spellings may differ with the same value)**
  - what the zip holds;
  - how it differs from the Box file: `genotype` added; `recipe_key`/`source_id` added; no `primary`/`crown`/`lateral`/`plant_name`; `scan_path` names bloomctl's layout; and scan exports carry their own trait columns;
  - values are the stored float4 in shortest form, so compare against float8 sources with float32 rounding;
  - the `load_trait_data` arguments, and passing trait columns explicitly (1.4's `curve_index` finding);
  - the quirks:
    - cells starting `=`/`+`/`-`/`@` can run as formulas in Excel;
    - without a BOM, Excel may mis-decode non-ASCII;
    - pandas reads a genotype named `NA` or `None` as NaN unless `keep_default_na=False`.

  Don't mention the button; PR B adds it.
- [x] 8.3 Add a "Cylinder trait export" section to `web/README.md`, beside "Cylinder pipeline runs":
  **(done 2026-09-30)**
  - the routes and error codes;
  - the limit constant names, pointing to `limits.ts`;
  - the single-process assumption;
  - that a restart loses jobs.

## 9. Pre-merge (PR A)

- [x] 9.1 Run the following and record the outputs in the PR:
  - from the root, `npm ci`, then `npm audit --audit-level=critical`;
  - `cd web && npx tsc --noEmit && npm run test:unit && npm run build`;
  - under a local `next build && next start`, POST a job and read its status and download through the other routes, which proves the shared state works in the production bundle;
  - `uv run --extra test pytest tests/unit/`, and the 1.2a integration test against `make dev-up`;
  - `pre-commit run --files <changed files outside openspec/>`;
  - `openspec validate add-cyl-trait-csv-export --strict`.
  **(partial, 2026-09-30:**
  - **`npm ci` and `npm audit --audit-level=critical`: exit 0, 0 critical.**
  - **`tsc --noEmit` clean; `npm run test:unit` 139 files / 2,000 tests passed; `npm run build` passes with CI's placeholder env.**
  - **Integration test: 10 passed.**
  - **Root `tests/unit`: the export schema test passes. 63 other tests fail and `test_weekly_backup.py` cannot be collected, all in deploy, doctor, env and shell-script tests that need Unix (`os.geteuid`, bash). These are the known Windows-only failures, in no file this branch touches; CI runs them on Linux.**
  - **pre-commit on the changed files outside `openspec/`: all hooks pass, after one prettier reformat committed as `95d87b67`.**
  - **`openspec validate --strict`: valid.**
  - **The `next build && next start` smoke passes. Run against the local dev stack after restarting its Kong, which had stopped serving port 8000 (with the user's OK), and with `SUPABASE_URL_HOSTS_ALLOWED` set as the app's startup check requires. A real sign-in, then the recipe listing, job start, status polling to ready, and the download of `soybean-mock-demo_unattributed_20261001.zip`: three files, 240 rows × 51 columns, `chosen_by: default`. `DELETE` gave 204 and the status then 404. One route module started the job and others read, downloaded and deleted it, so the shared state holds in the production bundle.)**

## 10. Verification on staging (PR A; user go-ahead for each run)

- [x] 10.1 Under a local `next start` pointed at staging, with a real session, export experiment 3313 at its default recipe through a scratchpad script. Then:
  - unzip it;
  - run 1.4's checks, with `qc_clean` on a filtered export under 5 MiB;
  - as an independent value oracle, fetch the `cyl_scan_traits_source` rows for (scan, coverage `source_id`) for 20 random included scans through bloomctl's client, and compare every cell as float4 (NaN = NaN, NULL = empty). Zero mismatches are required;
  - compare against #865's reference file on the shared scans and trait names, after rounding both sides to float32, and record the exact and float32 mismatch counts.
  **(done 2026-10-01, bloom-web built and started locally against staging (`next start`, `127.0.0.1:3107`), driven by `scratchpad/r101/drive_export.py` as `staging-user` (`bloom_user`). The selection matches the reference file: experiment 3313 (FN2023_Round3, rice) at `age=3`. Listing: 647 selected, one recipe (`unattributed`, 647 scans, the default), 1.45 s. Job: ready in 7.0 s; zip 1.93 MB (CSV 5,242,948 bytes, 68 over the 5 MiB `qc_clean` cap). Wave 1 (`age=3&wave=1`): 165 scans, 2.9 s, CSV 1,331,046 bytes. Delete 204, then 404.**
  - **1.4's checks (bloommcp env; sleap-roots-analyze 0.1.0a5, bloommcp 0.1.0a1, jsonschema 4.26.0, pandas 3.0.2): both sidecars validate and `generated_at` is UTC; rows 647 and 165; included + excluded = `n_selected` (0 excluded); 918 trait columns, `get_trait_columns` keeps 909 (the 9 `curve_index_*` dropped, as 1.4 predicted) and none of the metadata, `recipe_key` or `source_id`. `qc_clean` on the wave-1 export: genotype `genotype`, sample id `plant_qr_code`, replicate `wave_number`, 841 traits kept, no warnings.**
  - **Value oracle: 20 random included scans, 18,360 cells, 0 mismatches as float4 (NULL = empty, NaN = NaN). The oracle must read `value::float8`: PostgREST renders a bare `REAL` with 6 significant digits (`extra_float_digits` 0), which gave 15,646 false mismatches on a first read. The export is unaffected because `get_experiment_traits` returns `value::float`.**
  - **Reference (`Z:/users/eberrigan/20260910_Sanghwa_Lee_FN2023_Round3/3_day_old/sleap_roots_traits_output/traits_summary.csv`, 647 rows, 943 columns): all 647 scans shared; 342 of 918 trait names shared. The other 576 are `main_*` in Bloom and `crown_*` in the reference (the same root family under the older and newer sleap-roots naming). On the shared cells (221,274): 162,681 exact and 153,360 float32 mismatches; median relative difference 0.003%, p90 1.3%, p99 61%; 27.8% equal in float32. Staging's unattributed rows for 3313 are an earlier pipeline run than this 2026-09-13 local run, so the reference is not an oracle for them; the value oracle above is.)**
- [x] 10.2 Export staging experiment 1 with a wave filter and without one. Record the job time, the zip size and the peak RSS under `next start` (`ps -o rss` every 1 s). Confirm `included + excluded = n_selected`.
  **(done 2026-10-01, same local `next start` against staging; peak memory is the `next` server's Windows working set, sampled every 1 s (`PeakWorkingSet64`; `ps -o rss` does not exist here). Experiment 1 has 18,471 scans, the largest experiment.**
  - **`wave=1`: 1,420 selected; default `legacy:5`, 1,419 included + 1 excluded; job 15.7 s; zip 4.08 MB (CSV 11.6 MB); max working set 312 MB.**
  - **No filter, default recipe: the default is the newest recipe, pipeline `1911b908…`, which covers 3 scans, so 3 included + 18,468 excluded; job 10.7 s; zip 0.36 MB (excluded CSV 0.70 MB, sidecar 2.86 MB); max 237 MB.**
  - **No filter, `recipe=legacy:5&chosen=user` (the largest real export): 13,396 included + 5,075 excluded; job 129.9 s (7.3 estimated 121 s at p50); zip 40.2 MB (CSV 110.5 MB, 13,396 rows); max working set 547 MB, lifetime peak 575 MB, 544 MB after the job was deleted (not yet collected). Within `MAX_HELD_BYTES` (768 MB of zips plus reserves; the zip held was 40 MB).**
  - **included + excluded = `n_selected` in all three. `chosen_by` is `default` for the first two and `user` for the third.**
  - **For PR B: with no filter, the default recipe for experiment 1 is a 3-scan pipeline recipe, not the 13,396-scan legacy one, because the default is the newest source (#976). The dialog shows each recipe's `n_scans`, so a user can pick `legacy:5`; whether the default rule should change is a product question for the user. Decided 2026-10-01 by the user: the default stays the newest recipe; the dialog shows each recipe's count and definition (6.8).)**
- [x] 10.3 Export one scan, and check that it matches the experiment export by column.
  **(done 2026-10-01: scan 5386712, picked at random from 10.2's `legacy:5` export of experiment 1, exported at scan grain with its default recipe `legacy:5` in 1.6 s (zip 7 KB, 0 excluded). Against its row in the experiment file: the same 1,059 columns (24 fixed + 1,035 traits) in the same order, the trait order following the experiment file's, `recipe_key` `legacy:5` and `source_id` 5, and 0 differing cells. This scan carries all 1,035 of the recipe's traits, so the subset case (a scan file holds only its own trait columns) is covered by the goldens, not by this real-data check.)**
- [x] 10.4 Run `/pr-description` for PR A.
  **(done 2026-10-01: draft #996 opened with the approved body, then updated after §7 and §10 with their results; "Part of #865", no closing keywords.)**

## 10a. Review fixes (PR #996 review, 2026-10-01; test first, red/green recorded here and in each commit)

The five-reviewer review of #996 (review 5386494362) found one blocking bug and a set of clear-cut fixes. The items needing a user decision (held-slot starvation, the memory reserve, the accession-less plant count, `BLOOM_WEB_BUILD_SHA`, the bloomctl parity test) are not in this section.

- [x] 10a.1 **Blocking: a job cancelled before it starts must not run.** Test first in `jobs.test.ts`: reserve, `deleteJob` before `start`, then `start` — the run is never invoked, `start` returns null, the record stays `cancelled`, and the user can reserve again. Route test: the POST answers `409` with a fixed `detail` when its reservation was cancelled before it started. Spec scenario "Cancelled before it starts".
  **(done 2026-10-01. Red (`test(web): a trait-export job cancelled before it starts must not run`): 2 failed, 52 passed; `start()` returned an id and the POST answered 202. Green: `start()` returns null unless the record is still `running`, and the POST answers 409 "the export was cancelled before it started"; three test helpers assert non-null. The review's "always abort in `deleteJob`/`onDeadline`" is dropped: once `start()` refuses, no live controller can belong to a job that isn't running. 295/295 in the trait-export suites, tsc clean.)**
- [x] 10a.2 **An unreachable GoTrue is `503`, not `401`.** Test first on the jobs, job and listing routes: `getUser` returning `{error: {name: 'AuthRetryableFetchError', status: 0}}` (auth-js's network-failure shape) gives `503`. Spec: the GoTrue-unreachable scenario names status 0.
  **(done 2026-10-01. Red: 3 failed, 55 passed; status 0 and a missing status gave 401. Green: `verifyIdentity` returns 401 only for a 4xx rejection, otherwise 503. Spec: the routes table names status 0, a missing status and 5xx. 297/297 in the trait-export suites, tsc clean.)**
- [x] 10a.3 **No hidden retries.** Test first in `db.test.ts`: every PostgREST builder the adapter sends has `.retry(false)` applied (postgrest-js 2.106.2 retries GET/HEAD up to 3 times on network errors and 503/520). Spec: "No retries" covers the client library's own retries.
  **(done 2026-10-01. Red: 1 failed, 12 passed in `db.test.ts`; no request carried `retry(false)`. Green: `send()` applies `.retry(false)` before `.abortSignal()`; checked against the installed postgrest-js 2.106.2 that `maybeSingle()`, RPC and `head` builders all expose `retry()` and return themselves. 298/298, tsc clean.)**
- [x] 10a.4 **Fewer selection page reads.** Test first: `SELECTION_PAGE_SIZE` is at least 5,000, `pageSelection`'s default page size is that constant, and experiment 1's 18,471 scans resolve in at most 5 page reads plus the count. No PostgREST `max-rows` is set in any compose file, and keyset paging still ends on an empty page.
  **(done 2026-10-01. Red: 1 failed, 20 passed; the page size was 1,000 and `pageSelection` defaulted to its own literal 1000. Green: `SELECTION_PAGE_SIZE` = 5,000 and the default reads it; experiment 1 resolves in 4 pages plus the empty one. A 5,000-row page has not been timed on staging; the 2026-10-01 whole-experiment listing (about 20 pages of 1,000 plus the count) took 3.92 s, so a page should stay well under 8 s. 12.1 re-measures after deploy. 299/299, tsc clean.)**
- [x] 10a.5 **`listMergedRecipes` takes `listingBatchSize`, not `batchSize`.** Test first: `listMergedRecipes(db, e, ids, { listingBatchSize: 1 })` makes one call per scan. Rename the option and fix the stale "per batch" comments in `recipes.ts` and `build-export.ts`.
  **(done 2026-10-01. Red: 1 failed, 54 passed; `listingBatchSize` was ignored. Green: `listMergedRecipes` takes `Pick<BuildOptions, 'listingBatchSize' | …>`, `buildExport` passes `listingBatchSize` through, and the headers of `build-export.ts` and `recipes.ts` describe LISTING_BATCH_SCANS listings. 300/300, tsc clean.)**
- [x] 10a.6 **Tests the review showed to be weak** (test-only; each strengthened test is shown to fail against a mutation in a throwaway copy, recorded here):
  - (a) the process-wide-limit test makes the listing contend with two jobs and asserts its call waits in the semaphore queue; correct 6.7's note;
  - (b) error details are asserted exactly ("scan N has no coverage row", "… in batch i of n");
  - (c) `Sec-Fetch-Site: same-site` and `none` get `403` on every route;
  - (d) the download `409` covers a cancelled job and asserts its `detail`;
  - (e) the (a) golden variants also run with `listingBatchSize` 1 and 2, so the multi-chunk listing merge is exercised end to end;
  - (f) the session-floor boundary test uses fake timers.
  **(done 2026-10-01; each mutation was applied to the worktree file, run, and restored byte for byte (`git diff --quiet`):**
  - **(a) `recipes/route.test.ts`: both jobs' builds hold all 3 slots, then the listing issues no request until a slot frees. Mutation, `limitedDb` not wrapping `experiment`: fails (14 calls issued, 13 expected). 6.7's note and the file's header comment corrected.**
  - **(b) `build-export.test.ts`: the five loose details are now exact (`scan 9 has no coverage row`, `a coverage row fell outside batch 1 of 4`, `a trait read was truncated in batch 1 of 4`, `accession 1 returned no row`, `source 21 returned no row`). The review's mutation (details without scan, batch or accession): 3 failed.**
  - **(c) `same-site` and `none` get 403 on POST jobs, GET/DELETE job, download and the listing. Mutation `site !== 'cross-site'`: 8 failed.**
  - **(d) the download 409 covers running, failed and cancelled, each with its `detail`. Mutation, cancelled not refused: 1 failed.**
  - **(e) the golden variants at batch 1 and 2 also pass `listingBatchSize` 1 and 2. Mutation, the merge keeping the larger `n_scans` instead of the sum: 15 failed; the previous test file passes 55/55 under it. (A mutation of `newest_source_id` to the minimum survives: with `chosen=user` it only orders recipes, which these files don't show.)**
  - **(f) the session-floor test fakes only `Date`, pinned at 12:00:00.999; moving it 1 ms across the second between signing and posting turns the 1,800 s case into a 401, which is the flake the real clock allowed. Three runs pass.)**
- [x] 10a.7 **Researcher guide** (`_WIKI/SUPABASE/trait-recipes.md`, "Using a trait export"): the 5 MiB `qc_clean` inline cap; pandas `dtype={'plant_qr_code': str, 'genotype': str}` in place of the bare `keep_default_na=False` advice; whole-experiment exports mix plant ages (and the age-window Pipeline-class blind spot); `unattributed` and an empty pipeline payload mean provenance unknown. README: the listing's empty selection is `200`, and the full `limits.ts` list.
  **(done 2026-10-01. Guide: the 5 MiB `qc_clean` inline cap (`MAX_INLINE_CSV_BYTES`, `bloommcp/src/bloom_mcp/tools/_inline_input.py`) with a wave-or-age workaround; a pandas `read_csv` recipe (`dtype` text for `plant_qr_code`/`genotype`, `keep_default_na=False`, `na_values=['', 'NaN']`), checked with pandas: it keeps `00123`, `NA` and `None`, and trait columns stay float with empty and `NaN` cells missing; mixed plant ages and the Pipeline-class blind spot; `unattributed` and empty pipeline payloads mean unknown provenance. README: `Sec-Fetch-Site` other than same-origin, GoTrue unreachable is 503, the listing's empty selection is 200, the 409 for a job cancelled before it started, no postgrest-js retries, and all 13 `limits.ts` constants.)**
- [x] 10a.8 Pre-merge as in §9 (web suite, tsc, build, integration, pre-commit, `openspec validate --strict`), then show the user the push and the PR-body change.
  **(done 2026-10-01: web 2,174/2,174 (148 files), tsc clean, `next build` passes (tsconfig restored), integration batching + root unit 15 passed, root `npm audit --audit-level=critical` clean, pre-commit clean on the 18 changed files outside `openspec/`, `openspec validate --strict` passes. Push and PR-body change shown to the user before sending.)**

## 10b. Review decisions (PR #996 review; each decided with the user, test first)

- [x] 10b.1 **Held-slot starvation** (user decision 2026-10-01: options A + B). A: an issued PostgREST call is never aborted; a cancelled caller stops scheduling calls, an issued call runs to completion, its slot frees when it returns, and its result is discarded (the caller gets `cancelled`). This removes the 9 s hold (`ABORTED_CALL_HOLD_MS`). B: a user has at most one listing in flight; a newer listing cancels the older and waits for it to settle before issuing anything. Spec: the "Concurrency" bullet's hold line, and the listing requirement. Design: "How the semaphore behaves".
  **(done 2026-10-01. Red (`test(web): issued trait-export calls finish; one listing in flight per user`): 2 failed, 25 passed; the issued call's signal was aborted, and the newer listing issued a call while the older one's was in flight. Green: `Semaphore.run` gives the call its own never-aborting signal, releases the slot when the call returns, and turns a late result into `cancelled`; `ABORTED_CALL_HOLD_MS` and the hold are removed. The listing route keeps `{ctrl, settled}` per user and waits for the previous listing to settle. Spec: the Concurrency bullet, the "Cancel" scenario (issued calls finish and are discarded), the listing requirement, and the new scenario "Rapid re-listing". Design: "How the semaphore behaves" and the listing route. README updated. 306/306, tsc clean.)**

- [x] 10b.2 **Memory** (user decision 2026-10-01: the recommendation). `RUNNING_JOB_RESERVE_BYTES` at least the measured 340 MB per job (10.2), with two reserves still inside `MAX_HELD_BYTES`; `TraitPivot` stores values as float32 (9 bytes per value, not 13; the database values are float4, so nothing is lost and the goldens stay byte-identical); a zip still being downloaded counts against the budget after its job is dropped, until its stream closes or is cancelled. Draft an infra issue (restart policy and memory limit for `bloom-web`) for the user to approve; it gates production (12.3).
  **(done 2026-10-01 in code; the infra issue is filed as #1007. Red (`test(web): the trait-export memory budget covers measured jobs and open downloads`): 3 failed, 55 passed. Green: `RUNNING_JOB_RESERVE_BYTES` 384 MB; `TraitPivot` values in `Float32Array` with `storedBytes()`, and the golden files are byte-identical; `openDownload` registers a stream's bytes in `ExportState.downloads`, `reserveJob` counts them unless the job's ready zip is already counted, and the download route closes them when the stream ends or is cancelled (route test; a mutation leaving `cancel` unwired fails it). With two reserves filling the 768 MB budget, a second concurrent job is refused while another user's zip is held. 310/310, tsc clean.)**

- [x] 10b.3 **Plants with no accession** (user decision 2026-10-01: measure, then C). Staging count first (read-only); then, test first, `buildExport` fails before any recipe read with a detail naming the first such scan and plant, the count of others, and what to do, instead of "scan N has no coverage row".
  **(done 2026-10-01. Staging (read-only, over SSH): 0 of 40,209 plants lack an accession, in any experiment, deleted ones included; `cyl_scans_extended` does not join `accessions`, so such scans would enter a selection. Red: 2 failed, 55 passed (the fake's coverage does not model the inner join, so the export succeeded where real data fails with "no coverage row"). Green: `buildExport` checks the resolved selection first and fails with e.g. "scan 9 (plant FX-P1) and 2 other scans have no accession, so this export cannot run; ask an admin to set the plant's accession, then try again", with no recipe read. Spec: new scenario "A plant with no accession". 312/312, tsc clean.)**

- [x] 10b.4 **`BLOOM_WEB_BUILD_SHA`** (user decision 2026-10-01: option B). Not wired in this PR: it goes in the same small infra PR as 10b.2's restart policy and memory limit (both edit `bloom-web`'s compose service and need a staging deploy to verify). The route reads it at runtime, so the infra PR needs only `bloom-web`'s `environment` entry and an `export BLOOM_WEB_BUILD_SHA=$(git rev-parse --short HEAD)` before `docker compose up` in both deploy blocks. Filed as #1007 (with 10b.2's restart policy and memory limit); it gates production. Until then sidecars record `"1.0.0"`.

- [x] 10b.5 **bloomctl metadata parity** (user decision 2026-10-01: option A, an exception to the earlier "no bloomcli tests" decision; no bloomcli code changes). `bloomcli/tests/test_cyl_scan_metadata_parity.py` reads `web/lib/cyl-trait-export/__fixtures__/scan-metadata-parity.json`, asserts `CSV_COLUMNS` equals its columns, and runs each of its 13 cases through `build_scan_row` and `write_scans_csv`, comparing the read-back cells with `expected`. **(done 2026-10-01: 14 passed. Mutation, `scan_relative_dir` writing `day` for `Day`: 13 failed; restored byte for byte. The full non-integration bloomcli suite on Windows has 14 failures, none in this file: all POSIX permission, symlink, unwritable-directory and console-encoding tests that CI runs on Linux.)**

## PR B

## 11. Dialog and entry points

PR B was branched from `origin/staging` 9966cdf5, in worktree `.worktrees/add-cyl-trait-export-dialog` on branch `eberrigan/add-cyl-trait-export-dialog`. Its design is D8. Its behaviour is the three dialog requirements in the spec.

**PR B test rules** (on top of the rules for every task):
- Helper tests run in the `node` environment. Component tests start with `// @vitest-environment jsdom`.
- Only `@testing-library/react` and `fireEvent` are available. There is no user-event and no jest-dom.
- Mocks are written inline. Nothing is imported from `web/lib/cyl-pipeline/__fixtures__/` (D11).
  - Mock `@/lib/supabase/client` with `createClientSupabaseClient` and `auth.refreshSession`.
  - `fetch` is a `vi.stubGlobal` spy that answers plain `{ ok, status, headers, json, blob }` objects. Read request URLs with `new URL(call[0], "http://localhost")`.
  - Install `URL.createObjectURL` and `URL.revokeObjectURL` with `Object.defineProperty` (`configurable: true`) and remove them in `afterEach`. Spy on `HTMLAnchorElement.prototype.click`.
- `afterEach` runs, in this order: `vi.useRealTimers()`, `cleanup()`, `vi.unstubAllGlobals()`, `vi.restoreAllMocks()`. Unmounting sends `DELETE`, so `fetch` must still be stubbed during `cleanup()`.
- Timers:
  - Advance time only with bounded `act(() => vi.advanceTimersByTimeAsync(ms))`, plus a `settle()` loop of zero-ms ticks. Never use `runAllTimers`.
  - MUI's focus trap holds timers while the dialog is open. "Nothing after close" is therefore asserted as no further `fetch` calls and no `console.error` over 120 s after unmount.
- Use only Node 20 APIs: no `Promise.withResolvers`, `Object.groupBy` or the new `Set` methods. CI runs Node 20; this machine runs 22.

- [x] 11.1 **Test first: helpers.** In `web/lib/cyl-trait-export/client/`, write `recipe-view.test.ts`, `requests.test.ts` and `poll.test.ts` against stubs that export the signatures.
  - **`recipe-view`:**
    - **Descriptions:**
      - pipeline: model names and versions, plus both code SHAs;
      - legacy: `source_name`;
      - unattributed: "no source recorded";
      - a pipeline `definition` with no `models` (or `{}`) still describes;
      - an unknown key falls back to the raw key instead of throwing.
    - **Fewer-scans note:**
      - default 3 of 100, `legacy:5` 60 → names `legacy-5` and 60;
      - when the default already covers the most scans → no note;
      - a tie → the first in listing order;
      - only `unattributed` covers more → no note.
    - **Prefill:**
      - a value in the loaded list is kept, including `0`;
      - a value not in the list → "All";
      - an empty list → "All".
    - **Empty states:**
      - `n_selected` 0 → "No scans match this wave and age";
      - no rows → "No trait results for this selection";
      - both disable Download.
  - **`requests`:**
    - Listing URL:
      - `experiment=E` with `wave`/`age` only when not "All";
      - wave 0 → `wave=0`;
      - `scan=S` alone.
    - Job URL:
      - the selection, `recipe` (`legacy:5` arrives decoded as `legacy:5`), and `chosen`;
      - `chosen` is `default` exactly when the key is the listing's default, otherwise `user`.
    - Errors:
      - a `429` with a `job_id` → resume; without one → busy;
      - a `401` "session expires too soon" → retry, any other `401` → no retry;
      - a non-JSON body → the generic message.
  - **`poll`:**
    - delays: 2 s up to and including 60 s after the start or resume, then 5 s, as a table over 0, 58, 60, 62 and 120 s;
    - the phase lines in D8's table;
    - the latest-wins guard drops a response, or a `499`, only once a newer listing has been sent;
    - after 3 consecutive non-`404` failures it gives up; a success resets the count.
  - Static test: every value import under `@/lib/cyl-trait-export/` from `client/` or `components/cyl-trait-export/` is `stem` or `limits`; `import type` may name anything.
  **(done 2026-10-02: red against stubs, 33 failed / 3 passed. The 3 that passed are the static import test `client-imports.test.ts`, marked (characterization), since the stubs import nothing it forbids. Mutation: adding `import { reserveJob } from '../jobs'` to `poll.ts` fails it, 1 failed / 2 passed, naming `client/poll.ts: ../jobs`; the file was restored byte for byte.)**
- [x] 11.2 Implement the three helpers. **(done 2026-10-02: green, 36/36 across the 4 files; `tsc --noEmit` clean; prettier on the new files only.)**
- [x] 11.3 **Test first: the dialog and its button.** Write `web/components/cyl-trait-export/TraitExportDialog.test.tsx` and `TraitExportButton.test.tsx` against stubs. The dialog test drives the wiring, and the helpers' cases stay in 11.1.
  - **Listing:**
    - Open:
      - `refreshSession()` runs before the first `GET recipes`, checked with `invocationCallOrder`, and the first listing goes out at once;
      - a failed refresh (`{ error }` or a throw) shows the sign-in message and sends no `GET`.
    - Filter changes:
      - a change sends no `GET` at 499 ms and one at 500 ms;
      - two changes within 500 ms send one `GET`, for the second value;
      - each re-list refreshes first;
      - Download is disabled from the change until that listing arrives.
    - Display:
      - the default is selected and labelled, and each recipe shows its description and "N of M scans";
      - the note appears for the "Default covers fewer scans" data.
    - Stale and `499` responses:
      - a stale response, or a `499` for a superseded listing, changes nothing;
      - a `499` for the only listing shows its `detail` with Retry.
    - Listing errors: `401`, `403`, `404`, `422`, `502` or `503` shows its `detail` in `role="alert"`, with Retry; Retry refreshes, then re-lists.
    - Picking a recipe after a re-list:
      - a picked recipe that is still listed stays picked;
      - one that is gone falls back to the new default.
  - **Starting the job:**
    - refresh, then `POST`; two clicks in one `act` send one `POST`;
    - "session expires too soon" twice → exactly 2 `POST`s, then the `detail`; "Sign in to download traits." → no retry;
    - `429` with a `job_id`:
      - Resume polls that job through to save;
      - Cancel sends `DELETE` and returns to Download;
    - `429` without a `job_id`: the `detail` with Retry, and no Resume;
    - `403`, `404`, `409` (both kinds), `422` or `502`: the `detail` with Retry, and no polling.
  - **Polling:**
    - no second `GET` while a status `GET` is held;
    - phase `traits` 3/10 → "Reading batch 3 of 10";
    - `cancelled` → a message with Retry, nothing saved, no `DELETE` on close;
    - three `503`s → "Check again", which polls the same job and sends no `POST`;
    - a `404` → "the export was interrupted" with Retry;
    - `failed` with the 57014 `detail` → that `detail` with Retry, and `createObjectURL` not called;
    - Retry after a failure refreshes and `POST`s a new job.
  - **Saving:**
    - `ready` → `GET download`, `blob()`, then an anchor whose `download` is the job's `filename` is clicked;
    - `revokeObjectURL` gets the URL that `createObjectURL` returned;
    - then `DELETE` is sent;
    - a download that is not ok (`404` or `409`) shows its `detail`, and `blob()` and `createObjectURL` are not called;
    - a rejected `blob()` or a non-JSON error shows the generic message.
  - **Closing:**
    - **Mid-job:** Close and Escape each send one `DELETE`, call `onClose`, and stop polling; a bare `unmount()` while running sends one `DELETE`.
    - **Before the job exists:**
      - closed during `refreshSession()` → no `POST`;
      - closed during the `POST`, with a `202` arriving after → `DELETE` for that `job_id`, and no status `GET`;
      - closed during a listing → its signal is aborted.
    - **On the resume offer:** closing → no `DELETE` for that job.
    - **After close:** nothing else happens for 120 s.
  - **Help:** the link's `href` is D8's URL, with `target="_blank"` and `rel="noopener noreferrer"`.
  - **`TraitExportButton`:**
    - it honours `disabled`;
    - a click mounts the dialog with its props;
    - the dialog's `onClose` unmounts it.
  **(done 2026-10-02: red against stubs that render nothing, 56 failed / 0 passed (53 dialog, 3 button), all on assertions or missing elements, none on imports.)**
- [x] 11.4 Implement `TraitExportDialog.tsx` and `TraitExportButton.tsx`. **(done 2026-10-02: green, 56/56; with the helpers, 92/92; `tsc --noEmit` clean. Mutations, each restored byte for byte, all fail their tests: no `DELETE` for a start that answers after close (1 failed); `blob()` without the `ok` check (2 failed, the 404 and 409 refusals); dropping every `499` (1 failed, "shows a 499 for its only listing").)**
- [x] 11.5 **Test first: the entry points.**
  - First add `ScanTraitExportButton.tsx` (`"use client"`) as a stub that renders `null`, so the tests below fail on assertions rather than on imports.
  - Extend `TraitExplorer.test.tsx`, mocking `@/components/cyl-trait-export/TraitExportButton` the way the boxplot is mocked:
    - **Disabled state:** disabled while loading, and again while a trait change reloads;
    - **Props once loaded:**
      - for `trait_a`: `experimentId` 5, the current `waveNumber`/`plantAge`, waves [1, 2, 3] and ages [7, 14, 21];
      - for the `empty` trait: enabled, with empty lists;
    - **Existing queries:** the positional combobox and `role="status"` queries still work.
  - Write `ScanTraitExportButton.test.tsx`:
    - the first `GET recipes` query is exactly `scan=577`;
    - there are no wave or age comboboxes;
    - the `POST` query is `scan=577&recipe=…&chosen=…`.
  - Extend the scan page's `page.test.tsx` with `vi.mock("./ScanTraitExportButton")` carrying `scanId`:
    - the button is shown for scan 577 whether `CYL_PIPELINE_TRIGGER_ENABLED` is true or false;
    - it is absent when the scan doesn't exist.
  **(done 2026-10-02: red, 7 failed / 19 passed. The 19 are the existing TraitExplorer and scan-page tests, plus "offers no Download traits when the scan doesn't exist", which a `null` stub satisfies (characterization).)**
- [x] 11.6 Implement:
  - Add the button to `TraitExplorer.tsx`, after "Plant age" in the controls row, with `disabled={isLoading}`.
  - Implement `ScanTraitExportButton.tsx` and add it to the scan page with one import and one block beside `RunPipelineButton`.
  **(done 2026-10-02: green, 60/60 across the traits and scan directories; `tsc --noEmit` clean. Mutation: `disabled={false}` in TraitExplorer fails 2 ("disabled until the first trait's waves and ages have loaded" and "disabled again while a trait change reloads"); restored byte for byte. Prettier ran only on the two new files; the four edited files keep their own style.)**
- [x] 11.7 Docs:
  - **`_WIKI/SUPABASE/trait-recipes.md`:** add a "Getting one" paragraph at the top of "Using a trait export". Keep the heading, because the help link's anchor depends on it. The paragraph says:
    - where the button is, on the traits page and on a scan's page;
    - that the dialog lists each recipe with its scan count and what produced it;
    - that **the default is the newest recipe, not the one covering the most scans**, and the dialog names the larger one;
    - that scans the recipe doesn't cover are in `<stem>.excluded.csv`.
  - **`web/README.md` "Cylinder trait export":**
    - the dialog is in `components/cyl-trait-export/`, with its helpers in `lib/cyl-trait-export/client/`, and the scan page's button is `ScanTraitExportButton.tsx` beside `page.tsx`;
    - add `499` to the checks: "a newer listing from the same user replaced it, or the request was aborted".
  - **PR body:** the guide edit reaches the linked `main` page only at the next promotion.
  **(done 2026-10-02: "Getting one" paragraph added; heading unchanged. README names the dialog, `client/` and `ScanTraitExportButton.tsx`, and lists `499` among the checks. The PR body note is carried into 11.9's `/pr-description`.)**
- [x] 11.8 **Pre-merge (PR B).**
  - From the root: `npm ci`, then `npm audit --audit-level=critical`.
  - `cd web && npx tsc --noEmit && npm run test:unit && npm run build`. The build catches a server-only import in the client bundle.
  - `pre-commit` on this PR's new files.
    - For the four existing files it edits (`TraitExplorer.tsx`, `TraitExplorer.test.tsx`, the scan `page.tsx` and `page.test.tsx`), run the hooks other than prettier.
    - Those four files aren't prettier-clean on staging today, so any reformatting of them is reverted.
  - `openspec validate add-cyl-trait-csv-export --strict`.
  - After pushing, record that the **Web Unit Tests (Vitest)** and **Build & npm CVE Audit** jobs are green on the head SHA. They aren't required checks, so the merge gate won't show it.
  **(partial, 2026-10-02, local on Node 22:**
  - **`npm ci`; `npm audit --audit-level=critical`: exit 0.**
  - **`tsc --noEmit`: clean.**
  - **`npm run test:unit`: 157 files / 2,329 tests passed.** It prints one jsdom "Not implemented: navigation" error from an existing test; none of PR B's tests produce it (0 in a run of PR B's directories).
  - **`npm run build`: passes with CI's placeholder `NEXT_PUBLIC_SUPABASE_*` env.** Without it, `/test` fails to prerender, as on staging. The build rewrites `web/tsconfig.json`, which was restored.
  - **pre-commit:**
    - All hooks pass on the new files after the prettier hook (pinned 3.1.0) changed one line of `TraitExportDialog.tsx`. The repo's `npx prettier` (3.6.2) formats that line the other way; the hook's version is kept.
    - The four edited files pass every hook except prettier, which was skipped; `prettier --check` already flags all four on staging.
    - The two docs pass.
  - **`openspec validate --strict`: valid.**
  - **CI on #1025 head `064e3426` (Node 20): Web Unit Tests (Vitest) pass, Build & npm CVE Audit pass.)**
- [ ] 11.9 **Browser checks, after PR B deploys to staging** (decided 2026-10-02: on the deployed site, not on a local build, so the checks see the real Caddy, cookies and env with no local anon key or credentials; done together with 12.1's PR B row).
  - In Chrome and Firefox on `https://staging.bloom.salk.edu:8443`, signed in as the staging test user:
    - 3313 at its default;
    - experiment 1 unfiltered: the note names `legacy-5` over the 3-scan default;
    - experiment 1 with `wave=1`: the default is `legacy:5`, so no note;
    - one scan;
    - closing mid-job: then a new Download is accepted, not `429`;
    - two tabs: the second gets the resume offer;
    - the help link opens the "Using a trait export" section on `main`.
  - Delete each downloaded zip after reading its counts.
  - Record the results, and that Safari is unchecked (#1024). A failure is fixed in a follow-up PR to `staging` before promotion to `main`.
  - Before merge, PR B's evidence is 11.1–11.8 (unit tests with mutation checks, build, CI); its body says the browser checks follow the staging deploy.

## 11a. Review fixes (PR #1025 review 5396953901, 2026-10-02; test first, red/green recorded here and in each commit)

- [x] 11a.1 **Listing correctness.** Test first in `TraitExportDialog.test.tsx` and `client/requests.test.ts`:
  - a filter change makes the in-flight listing stale at once: if it answers inside the debounce, it fills nothing, Download stays disabled, and its signal is aborted;
  - a stale **200** answering after a newer listing is ignored;
  - a `200` with a JSON `null` body, or a body that is not a listing, shows the generic message with Retry (`parseListing`);
  - a `200` that is not JSON shows the generic message;
  - an automatic pick follows a moved default; a user's pick sticks while listed;
  - StrictMode double-mount shows no error before the first listing.
- [x] 11a.2 **Job lifecycle.** Test first:
  - **Retry gate:**
    - Retry after a job error, with a filter changed and the listing not yet back, starts nothing;
    - ~~with two errors at once, only one Retry acts~~: unreachable, because a filter change clears the job error (11b.3).
  - **Session refresh:**
    - no `refreshSession()` for a listing, nor for a start whose session has more than `MIN_SESSION_SECONDS` left;
    - a refresh when it has less;
    - a `4xx` refresh error shows the sign-in message; a network or `5xx` one shows a retryable error.
  - **Polling:**
    - two clicks inside one `act` send one start (kills removing the `busy` guard);
    - a good poll between failures resets the count (503, 503, running, 503, 503, running: no "Check again");
    - a poll `401` shows the sign-in message;
    - a poll `404` says the export is no longer on the server;
    - the filters and radios are disabled while a job is active.
  - **Resume and cancel:**
    - the offer says it may be another tab's export;
    - a resumed job is not deleted on close, and saving it deletes it;
    - resuming while holding a failed job deletes that one;
    - Cancel export waits for its `DELETE` before Download is enabled.
  - **Closing:** a backdrop click while a job is active does not close; Escape does.
  - **Saving:**
    - on `ready`, the dialog says "Download started";
    - the URL is not revoked until unmount, and "Save again" re-clicks it;
    - `DELETE` follows.
  - **Shape checks:** a `job_id` that is not a UUID, or a `filename` that is not `<stem>.zip`, is not used (fallback `traits.zip`).
  - **Other failures:**
    - Retry on a refused download;
    - a POST or download fetch that rejects;
    - Check again, then a `404`.
- [x] 11a.3 **What the dialog says.** Test first in `client/recipe-view.test.ts` and the dialog test:
  - a pipeline recipe:
    - shows each model's short weights checksum;
    - flags output params;
    - says "no models or code recorded" when it has neither;
  - the note says recipes differ in models and trait columns, and drops "pick it" once that recipe is picked;
  - counts read "N of M selected scans", with thousands separators;
  - the heading names the wave and day filters;
  - the `429` offer is `role="alert"`;
  - the progress and saved lines share one always-mounted `role="status"`.
- [x] 11a.4 Update the guide's "Getting one" paragraph: the dialog starts on the traits page's wave and age, and All/All exports the whole experiment.
  **Helpers (2026-10-02):** `parseListing`, `parseJobView`, `isJobId`, `safeFilename`, `sessionNeedsRefresh`, `refreshFailureKind`, `countLabel` and `selectionTitle` added, and the weights checksum, output params and empty-pipeline line added to `describeRecipe`. Red against stubs: 20 failed / 30 passed. Green: 50/50.
  **Dialog (11a.1-11a.4, 2026-10-02):**
  - **Red:** against the old dialog with the new message constants: 25 failed / 57 passed (dialog, scan button and helpers).
  - **Green:** 132/132 in those directories; the full web suite passes, 157 files / 2,362 tests; `tsc --noEmit` clean.
  - **Mutations,** each restored byte for byte, each failing exactly its own test:
    - bumping the listing guard inside `list()` (after the debounce) fails "makes the previous listing stale as soon as a filter changes";
    - keeping any still-listed pick fails "moves an automatic pick to the new default";
    - dropping the backdrop guard fails "ignores a click outside while a job is active";
    - deleting resumed jobs on close fails "keeps a job it only resumed when closed".
  - **Retry gate:** the 11a test that clicked whatever Retry was showing was vacuous, because the filter change clears the error. 11b.3 replaced it:
    - "clears a failed job and its Retry as soon as a filter changes" tests the reset;
    - "keeps a failed job's Retry disabled… while a re-listing is pending" tests the disabled Retry;
    - the `start()` check is unreachable from the UI, so it is defence in depth.
  - **Guide:** the "Getting one" paragraph now says the dialog starts on the page's wave and age (All/All for the whole experiment), that recipes differ in models and trait columns, and what "Download started" and "Save again" mean.
- [ ] 11a.5 Pre-merge again as in 11.8, push (with the user's yes), record CI, and update the PR body's review-fixes section.

## 11b. Round-2 review fixes (PR #1025 review 5397656269, 2026-10-02; test first)

- [x] 11b.1 **Job ownership and lifecycle.** Test first in the dialog test:
  - **Ownership:**
    - after a poll `401`, Retry, and a `429` naming the held job, the dialog keeps following it as its own with no offer, and deletes it on close;
    - resuming another tab's job and then a third job deletes neither;
    - a resumed job is not deleted after saving.
  - **Stale answers:**
    - an answer for a job it no longer follows is ignored: a stale `cancelled` poll after Cancel export, Resume and a new Download leaves the new job followed;
    - the offer's Resume and Cancel are disabled while its cancel is in flight.
  - **Closing:**
    - closing aborts the in-flight start, poll and download requests;
    - Escape does not close while a job is active;
    - Close still closes and cancels.
- [x] 11b.2 **Sign-out classification.** Test first in `client/requests.test.ts` with auth-js's own error classes:
  - `AuthRetryableFetchError` (status 0 and 503) is `retry`;
  - `AuthApiError` 500, `AuthUnknownError` and `AuthSessionMissingError` are `signin`;
  - a non-auth throw is `retry`.
- [x] 11b.3 **Tests that prove each guard on its own** (from the round-2 testing review, each killing a named mutant):
  - a filter change clears a failed job and its Retry;
  - a failed job's Retry stays disabled, and starts nothing, while a re-listing is pending;
  - the fetch mock honours `AbortSignal`, and StrictMode shows no error when the aborted first listing rejects;
  - an automatic pick follows the default again after the user's pick was dropped;
  - Check again allows three more failures;
  - a filter change clears "Download started" and Save again, and revokes the URL;
  - a thrown refresh shows the retryable message;
  - a stale body arriving after a filter change is ignored;
  - `removeJob` warns on a failed DELETE but not on a `404`.
  - Correct the 11a notes: untick "two errors at once" as unreachable, and replace "guarded three ways".
- [x] 11b.4 **What the dialog says.** Test first:
  - **Recipe descriptions:**
    - a legacy recipe says its models and code were not recorded, and so does the note when it points at one;
    - output params show `name=value`;
    - a model with no name is "unnamed model";
    - the key segment has the full key as its title.
  - **Messages:**
    - an unreadable listing error is the listing message, not the export one;
    - a listing `499` says another window started listing;
    - a poll or download `404` names an earlier download among the causes;
    - the 429 offer reads as one sentence;
    - for a resumed job, the status line says its selection may differ.
  - **Docs:**
    - the guide's "Getting one" paragraph uses "most recently added" and avoids "image";
    - it no longer overclaims "Download started", and says a legacy recipe has no recorded models or code;
    - fix the stale doc comments in `TraitExportButton.tsx` and `poll.ts`.
  **(done 2026-10-02):**
  - **Red:** 10 failed / 45 passed for the helpers, and 11 failed / 86 passed for the dialog and scan button.
    - One red test came from the round-2 refresh test still using a plain `{status: 400}` object; it now uses auth-js's real `AuthApiError` and `AuthRetryableFetchError`.
    - The single-guard tests from the review pass on the current code, as characterization.
  - **Green:** 152/152 in the changed directories, and the full web suite passes, 157 files / 2,382 tests; `tsc` clean.
  - **Mutations,** each restored byte for byte, each failing exactly its own test:
    - a `429` naming the held job going back to the offer;
    - the offer enabled while cancelling;
    - Escape closing during a job;
    - save deleting a resumed job;
    - no abort signal on polls.
  - **Untested by design:** the "answer for a job no longer followed" check in `poll()`/`save()` is defence in depth, because the offer is disabled while cancelling and no other UI path changes the followed job while a poll is in flight.
  - **Closing:** it aborts the listing, poll and download requests but not the start, so a start answering after the close still yields its id and is deleted (spec and D8 updated to match).
  - **Pick tracking:** the user's pick is now tracked in a ref (`userPick`), so the listing effect reads no state outside its dependencies.
- [ ] 11b.5 Pre-merge as in 11.8; record 11a.5's CI (green on `ffc25ee6`: 33 pass, 2 skipped) and this push's; update the PR body.

## 12. After merge

- [ ] 12.1 After each PR deploys to staging, repeat its largest export through the deployed Caddy path. Record the job time, zip size and `bloom-web` peak RSS.
  **PR A done (2026-10-02); PR B still to come.** Staging was serving `26ca7b7c` (Deploy run
  36997056580). bloom-web was built from `f79a1598`: its `BLOOM_WEB_BUILD_SHA` is the last
  bloom-web-input commit, `deploy.yml:327`, and it contains #996 (`306ab03a`). That build has
  `SELECTION_PAGE_SIZE` 5000, `RUNNING_JOB_RESERVE_BYTES` 384 MiB and the Float32 pivot. #1009's
  runtime was live: `restart: unless-stopped`, a 3 GiB memory limit, container started
  10:59:30Z. With the user's yes, 10.2's largest export went through
  `https://staging.bloom.salk.edu:8443`, driven by a local script (not committed) as the
  bloomctl `staging-user` (`bloom_user`). Like §10's driver, it takes the session from
  `make_authed_client(load_credentials("staging-user"))` and sends it as an @supabase/ssr cookie
  (`base64-` + base64url of the session JSON, chunked at 3180 characters) under bloom-web's
  `SUPABASE_COOKIE_NAME` (`sb-bloom-staging-auth-token`), with `Sec-Fetch-Site: same-origin`.
  It then calls `GET …/recipes`, `POST …/jobs`, polls `GET …/jobs/<id>` every 1 s, and makes
  `GET …/download` and `DELETE …/jobs/<id>`:
  - Query `experiment=1`, `recipe=legacy:5&chosen=user`. The listing came back in 2.72 s with
    `n_selected` 18,471 and 8 recipes; the default is pipeline `1911b908…` (3 scans), and
    `legacy:5` has 13,396.
  - Job ready in **137.5 s** (local `next start`: 129.9 s), file
    `diversity-screen_legacy-5_20261002.zip`, **40,163,127 bytes** (local: 40.2 MB). It holds
    a CSV of 110,503,355 bytes (13,396 rows), an `.excluded.csv` (5,075 rows) and a 686,698-byte
    sidecar.
  - 13,396 + 5,075 = 18,471 = `n_selected`. The sidecar has `chosen_by` `user`, `recipe_key`
    `legacy:5`, and `generated_by.version` `1.0.0+f79a15985ae5…` (#1007's build SHA, as wired by
    #1009).
  - **bloom-web sampled peak memory 377.2 MiB** (`docker stats` MemUsage, sampled about every
    1–2 s over SSH, read-only). It idled at 79 MiB, peaked at 17:00:53Z as the job finished,
    and was still 373 MiB after the delete, not yet collected. By 17:11Z it was back to 83 MiB.
    That is 12% of the 3 GiB limit. It is not directly comparable with 10.2's 547–575 MB, which
    was the Windows working set of a local `next start`.
  - Delete answered 204, then 404. The downloaded files were deleted locally once the counts
    were read.
- [ ] 12.2 After promotion to main, with the user's go-ahead, rerun 7.1 read-only on production. Open a tuning PR if any p95 is over 4 s.
- [ ] 12.3 Draft these for the user to approve before filing:
  - ~~a follow-up issue for `restart: unless-stopped`, a `mem_limit` and a single-replica comment on `bloom-web` in compose~~ and ~~a follow-up issue for `BLOOM_WEB_BUILD_SHA` (Open Question 1)~~: filed together as #1007 (2026-10-01);
  - a follow-up issue for `TraitExplorer` on recipe reads (Open Question 2);
  - an upstream sleap-roots-analyze issue on the substring exclusion in `get_trait_columns`;
  - a follow-up issue for a durable export job, if §7 or 12.1 shows the need;
  - a follow-up issue for the superseded `param_hash` wording in add-cyl-pipeline-ui D8;
  - the #865 closing comment.
- [ ] 12.4 Archive with `/openspec:archive add-cyl-trait-csv-export` only after all of these:
  - 12.1 for both PRs;
  - 12.3's drafts;
  - `add-cyl-trait-recipe-key` archived, which itself waits on the staging→main promotion and on `fix-cyl-redelivery-blob-collision`.

## Scenario → test

| Spec scenario | Test |
|---|---|
| Job lifecycle | 5.5, 6.3 (chained) |
| Download before the job is ready | 6.3 |
| Another user's job | 5.5, 6.3 |
| Cancel | 5.5, 5.1(h), 6.3 |
| Cancelled before it starts | 10a.1 |
| Retention | 5.5 |
| A refused start keeps the finished job | 5.5 |
| Deadline | 5.5 |
| Batch size and completion order | 5.1(a) |
| Merged listing equals a single call | 3.1, 1.2a |
| A listing is one call for every current selection | 7.4 (build-export.test.ts "listMergedRecipes batching", 5.1(b)) |
| No call reads a whole experiment | 5.1(b), 6.5 |
| Process-wide limit | 3.5, 5.1(c), 6.7 |
| Truncated trait response | 5.1(d) |
| A scan with no coverage row | 5.1(d) |
| Statement timeout | 5.1(d) |
| Data changed mid-export | 5.1(d) |
| Missing accession | 5.1(d) |
| Default kept | 5.1(f) |
| Default moved | 3.1, 5.1(f) |
| Key not in the selection | 5.1(e) |
| Trait columns follow the data | 5.1(j), golden `K` |
| Values are the stored float4 | 4.1, golden |
| NULL, NaN and absent values | 4.1, golden |
| Excluded scans | 4.3, golden |
| A superseded source is not exported | 5.1(i), golden |
| Unattributed recipe | 4.3, 1.5, golden |
| Filtered selection | 3.3, 4.3 |
| Legacy stem and source ids | 4.5, 4.3 |
| Scan id hash | 3.3 |
| A scan export agrees | 5.1(g), 10.3 |
| Metadata edge cases | 2.1 |
| Unverified identity | 6.1, 6.3, 6.5 |
| Deleted experiment | 6.1, 6.5 |
| Selection changed before the job starts | 3.3, 6.1 |
| Age zero is valid | 6.1, 6.5 |
| Malformed parameters | 6.1 |
| Cross-site request | 6.1, 6.3, 6.5 |
| One job per user | 5.5, 6.1 |
| Session near expiry | 6.1 |
| Listing error | 6.5 |
| Filtered listing | 6.5 |
| Each row says what its recipe is | 6.8 |
| Rapid re-listing | 10b.1 |
| A plant with no accession | 10b.3 |
| Metadata matches bloomctl (bloomctl side) | 10b.5 |
| No recipes | 6.5 |
| Empty selection | 6.5 |
| Button waits for the page, Filters prefilled from the page | 11.1 (prefill), 11.5 |
| Scan grain | 11.5 (`ScanTraitExportButton.test.tsx`) |
| Recipe described, Default covers fewer scans, Default covers the most scans | 11.1 (`recipe-view`), 11.3 |
| Default preselected, Nothing to download | 11.1, 11.3 |
| Stale listing discarded, Listing replaced elsewhere | 11.1 (`poll`), 11.3 |
| Progress and save, Download refused | 11.3 (Saving) |
| Session refreshed and retried once, Own job already running, Server busy | 11.1 (`requests`), 11.3 |
| Failure shown, Interrupted export | 11.3 (Polling) |
| Close cancels, Closed before the job started | 11.3 (Closing) |
| Automatic pick follows the default, Unreadable listing | 11a.1 |
| Retry needs a current listing, Session refreshed only when needed, Export no longer on the server | 11a.2, 11b.3 |
| Resumed job kept on close, Own job named by a 429, Click outside or Escape during a job | 11b.1 |
| Stale listing discarded | 11.3, 11a.1, 11b.3 |
