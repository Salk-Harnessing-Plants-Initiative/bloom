**Landing plan: two PRs to `staging`.**

| PR | Title | Sections | Depends on |
|---|---|---|---|
| A | "Export an experiment's or scan's traits, one recipe per file (export jobs and routes)" | §0–10 | Its base contains #976. §7 and §10 need #976 deployed to staging. |
| B | "Download traits from the traits and scan pages" | §11 | PR A merged. Check #965 first, which edits the same scan page. |

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
- [ ] 0.3 Confirm that #976's staging Deploy has completed, and that staging PostgREST serves `list_trait_recipes` (read-only, as a staging `bloom_user`). §7 and §10 wait on this; the draft PR doesn't.
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

- [ ] 4.1 **Test first.** `csv.test.ts`. The float4 formatter:
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
- [ ] 4.2 Implement `csv.ts`, a pivot over per-scan `Uint32Array`/`Float64Array`/`Uint8Array` buffers.
- [ ] 4.3 **Test first.** `sidecar.test.ts`:
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
- [ ] 4.4 Implement `sidecar.ts` and `excluded.ts`.
- [ ] 4.5 **Test first.** `stem.test.ts`:
  - `diversity-screen_wave3_day0_legacy-12345_20261002`;
  - a 64-hex key, `unattributed`, and a scan stem;
  - an all-symbol name → `experiment-<id>`;
  - a 200-character name cut to 60 with no trailing `-`;
  - an injected `Date` at 23:30 in UTC−8, which gives the next UTC day;
  - every output matches `^[a-z0-9_-]+$`.
- [ ] 4.6 Implement `stem.ts`.

## 5. Build, zip and jobs

- [ ] 5.1 **Test first.** `build-export.test.ts`, against the D10 fake (listings served only for recorded `scan_ids`):
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
- [ ] 5.2 Implement `build-export.ts`.
- [ ] 5.3 **Test first.** `zip.test.ts` (node env): `zip.ts` takes an `AsyncIterable<Uint8Array>` per entry and uses synchronous `ZipDeflate`, yielding with `setImmediate` between slices.
  - Unzipped with `fflate.unzipSync`, the output holds exactly the three names with the golden bytes.
  - No input chunk exceeds the slice size.
  - A mid-stream error rejects.
- [ ] 5.4 Implement `zip.ts`.
- [ ] 5.5 **Test first.** `jobs.test.ts`, with fake timers, an injected clock and an injected build function:
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
- [ ] 5.6 Implement `jobs.ts`.

## 6. Routes

- [ ] 6.1 **Test first.** `web/app/api/cyl/trait-export/jobs/route.test.ts`. Mock the auth helper so that `getUser(token)` runs on the captured token.
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
- [ ] 6.2 Implement `jobs/route.ts` and `web/lib/cyl-trait-export/request.ts` (identity, guard and parameters).
- [ ] 6.3 **Test first.** Status, download and `DELETE` route tests.
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
- [ ] 6.4 Implement both routes.
- [ ] 6.5 **Test first.** `recipes/route.test.ts`:
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
- [ ] 6.6 Implement `recipes/route.ts`.
- [ ] 6.7 **Test first.** The "two jobs and one listing" test: the real `jobs.ts` and recipes handler against one blocking fake. In-flight calls never exceed 3, and the listing's calls interleave with the jobs'.

## 7. Measurement gate (staging, before PR A is ready)

- [ ] 7.1 Write a read-only scratchpad script using bloomctl's `make_authed_client` (as for the 2026-09-30 production measurement). For staging experiments 1, 269327, 7206207 and 3313, time the per-batch `list_trait_recipes`, `get_trait_recipe_coverage` and `get_experiment_traits(recipe_key_, 4-column select, count=exact)` at 50, 100 and 200 scans. Record rows, bytes and p50/p95/max. Run it with the user's go-ahead.
- [ ] 7.2 Confirm that the hosts don't override `JWT_EXPIRY` (committed as 3600): check a fresh session's `expires_in`, never printing a token. `MIN_SESSION_SECONDS` = 1,800 and `EXPORT_MAX_SECONDS` = 1,500 must fit within it.
- [ ] 7.3 Set `BATCH_SCANS` to the largest size whose p95 is under 4 s for every RPC.
  - **Job time.** Estimate experiment 1's production job time as the per-batch time × `ceil(18471 / BATCH_SCANS)` ÷ `PG_CONCURRENCY`. If that exceeds `EXPORT_MAX_SECONDS`, stop and bring the durable-job follow-up to the user.
  - **Listing time.** Estimate the listing time with 2 jobs running. If it would exceed 60 s, stop and propose moving the listing into the job.

## 8. Docs

- [ ] 8.1 Update `_WIKI/SUPABASE/trait-recipes.md` §"Export sidecar v1" so it states design D4–D6 **in full** for every exporter.
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
- [ ] 8.2 In the same page, add a "Using a trait export" section:
  - what the zip holds;
  - how it differs from the Box file: `genotype` added; `recipe_key`/`source_id` added; no `primary`/`crown`/`lateral`/`plant_name`; `scan_path` names bloomctl's layout; and scan exports carry their own trait columns;
  - values are the stored float4 in shortest form, so compare against float8 sources with float32 rounding;
  - the `load_trait_data` arguments, and passing trait columns explicitly (1.4's `curve_index` finding);
  - the quirks:
    - cells starting `=`/`+`/`-`/`@` can run as formulas in Excel;
    - without a BOM, Excel may mis-decode non-ASCII;
    - pandas reads a genotype named `NA` or `None` as NaN unless `keep_default_na=False`.

  Don't mention the button; PR B adds it.
- [ ] 8.3 Add a "Cylinder trait export" section to `web/README.md`, beside "Cylinder pipeline runs":
  - the routes and error codes;
  - the limit constant names, pointing to `limits.ts`;
  - the single-process assumption;
  - that a restart loses jobs.

## 9. Pre-merge (PR A)

- [ ] 9.1 Run the following and record the outputs in the PR:
  - from the root, `npm ci`, then `npm audit --audit-level=critical`;
  - `cd web && npx tsc --noEmit && npm run test:unit && npm run build`;
  - under a local `next build && next start`, POST a job and read its status and download through the other routes, which proves the shared state works in the production bundle;
  - `uv run --extra test pytest tests/unit/`, and the 1.2a integration test against `make dev-up`;
  - `pre-commit run --files <changed files outside openspec/>`;
  - `openspec validate add-cyl-trait-csv-export --strict`.

## 10. Verification on staging (PR A; user go-ahead for each run)

- [ ] 10.1 Under a local `next start` pointed at staging, with a real session, export experiment 3313 at its default recipe through a scratchpad script. Then:
  - unzip it;
  - run 1.4's checks, with `qc_clean` on a filtered export under 5 MiB;
  - as an independent value oracle, fetch the `cyl_scan_traits_source` rows for (scan, coverage `source_id`) for 20 random included scans through bloomctl's client, and compare every cell as float4 (NaN = NaN, NULL = empty). Zero mismatches are required;
  - compare against #865's reference file on the shared scans and trait names, after rounding both sides to float32, and record the exact and float32 mismatch counts.
- [ ] 10.2 Export staging experiment 1 with a wave filter and without one. Record the job time, the zip size and the peak RSS under `next start` (`ps -o rss` every 1 s). Confirm `included + excluded = n_selected`.
- [ ] 10.3 Export one scan, and check that it matches the experiment export by column.
- [ ] 10.4 Run `/pr-description` for PR A.

## PR B

## 11. Dialog and entry points

- [ ] 11.1 **Test first.** `web/components/cyl-trait-export/TraitExportDialog.test.tsx` (jsdom; fake timers with `act()` and `vi.advanceTimersByTimeAsync`).
  - **Opening and listing:**
    - on open, refresh, then list;
    - the recipe list shows the `<keyseg>`, kind and "N of M scans", with the default preselected and labelled;
    - wave and age are prefilled only when the loaded lists contain the value, otherwise "All" (0 is a value);
    - a filter change re-lists after a 500 ms debounce, and a stale response is ignored;
    - `n_selected: 0` shows "No scans match this wave and age", and an empty list shows "No trait results for this selection"; both disable Download;
    - a listing error is shown.
  - **Downloading:**
    - `refreshSession()` runs, then a `POST` with the explicit `recipe` and `chosen`; a failed refresh sends no `POST`;
    - a per-user `429` offers to resume or cancel the returned job;
    - polling runs every 2 s, then every 5 s after a minute, showing "Reading batch d of n";
    - on `ready`, fetch, `blob()` and save `<stem>.zip`.
  - **Failures:**
    - on `failed`, the `detail` is shown with Retry, and nothing is saved;
    - a non-JSON error body or a rejected `blob()` shows a generic message;
    - a `401` "session expires too soon" refreshes and retries once;
    - a `404` while polling says "the export was interrupted".
  - **Closing and help:**
    - closing sends `DELETE` and stops polling, with no state update after close;
    - the "What's in this file?" link is present.
- [ ] 11.2 Implement `TraitExportDialog.tsx` and `TraitExportButton.tsx`.
- [ ] 11.3 **Test first.** Extend `TraitExplorer.test.tsx`: the button is disabled until waves and ages load, then opens the dialog with the current `waveNumber` and `plantAge`. Write `ScanTraitExportButton.test.tsx`, extending #965's `page.test.tsx` if #965 has merged by then.
- [ ] 11.4 Add the button to `TraitExplorer.tsx`. Add `ScanTraitExportButton.tsx` beside `web/app/app/phenotypes/[speciesId]/[experimentId]/[waveId]/[accessionId]/[scanId]/page.tsx`, with a one-line import in the page. Add the button to 8.2's section. Note in PR B that the help link targets `main`, so it resolves only after promotion.
- [ ] 11.5 Pre-merge as in §9. Then verify through the UI in Chrome, Firefox and Safari: 3313 at default, experiment 1 filtered by wave, and one scan. Record the results, then run `/pr-description` for PR B.

## 12. After merge

- [ ] 12.1 After each PR deploys to staging, repeat its largest export through the deployed Caddy path. Record the job time, zip size and `bloom-web` peak RSS.
- [ ] 12.2 After promotion to main, with the user's go-ahead, rerun 7.1 read-only on production. Open a tuning PR if any p95 is over 4 s.
- [ ] 12.3 Draft these for the user to approve before filing:
  - a follow-up issue for `restart: unless-stopped`, a `mem_limit` and a single-replica comment on `bloom-web` in compose;
  - a follow-up issue for `BLOOM_WEB_BUILD_SHA` (Open Question 1);
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
| Retention | 5.5 |
| A refused start keeps the finished job | 5.5 |
| Deadline | 5.5 |
| Batch size and completion order | 5.1(a) |
| Merged listing equals a single call | 3.1, 1.2a |
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
| No recipes | 6.5 |
| Empty selection | 6.5 |
| Default preselected, Failure shown, Interrupted export, Close cancels | 11.1 |
