## 1. Pre-implementation verification

Tasks 1.1 and 1.3 must be completed, with results recorded in `design.md`, before any test in
section 2 is written — section 2's tests assert against their output. Task 1.2 is informational
and non-blocking; it may happen at any time. The lock's `staleness_seconds` value and the
pre-upload frame-count query's exact shape are already fixed in `design.md`'s Decisions section
(no separate investigation task needed for either).

- [x] 1.1 Query one existing `TEST-E2E-*` scan's full metadata (species, wave_number, germ_day,
      germ_day_color, plant_age_days, date_scanned, device_name) via a throwaway read-only script
      (deleted after use) to source the exact default values `create-test-scan` will pass to
      `insert_image_v2_0` for the wave/plant-batch parameters. Done: source scan `12894745`
      (`TEST-E2E-001`), queried via `cyl_scans_extended` under the `staging-writer` profile (not
      `pipeline-staging` — see `design.md`'s Profile note; `pipeline-staging` cannot read
      `cyl_scanners`, which this same lookup revealed the RPC needs). Values recorded in
      `design.md`: `species_common_name="Canola"`, `wave_number=9999`, `germ_day=1`,
      `germ_day_color="TestGray"`, `plant_age_days=2`, `date_scanned_="2026-08-24"`,
      `device_name="FastScanner"`. Also discovered `device_name` is NOT one of the sentinel
      identity fields (the RPC requires it to already exist in `cyl_scanners`, unlike
      phenotyper/scientist/accession) — `design.md` and `specs/cyl-test-scan-cli/spec.md` were
      updated accordingly (a new requirement covers it separately from the sentinel-identity
      one). Identity fields (`phenotyper_name`/`email`, `scientist_name`/`email`,
      `accession_name`) are NOT sourced this way — they use the fixed synthetic sentinel values
      from `design.md`, regardless of what any existing scan carries.
- [x] 1.2 (Informational, non-blocking) Check whether `cyl_plants` / `cyl_scans` carry the
      unique constraints `design.md`'s Context section describes
      (`(wave_id, qr_code)` / `(plant_id, date_scanned)`). Done: confirmed directly against
      `supabase/migrations/20230724171639_add_uniqueness_contraints.sql` — all three constraints
      (plus `cyl_scanners UNIQUE (name)`) exist exactly as described.
- [x] 1.3 Confirm the image-file extension allowlist `--good` will use to distinguish frame files
      from non-image files in `--frames-dir`. Done: `.png`, `.jpg`, `.jpeg`, `.tif`, `.tiff`
      (case-insensitive), matching `cyl/download.py::image_dest`'s convention. Document this in
      the command's `--help` text during implementation (task 3.2/3.6).

## 2. Red — tests first (`bloomcli/tests/test_cyl_create_test_scan.py`)

Follow this codebase's existing convention: no `unittest.mock`; small hand-written fake
`_Client`/`_RPC`/storage-bucket classes (see `tests/test_cyl_ingest.py`), plus a
`_patch_authed`-style monkeypatch of `_authed_client` for command-level tests. Pin exact query
shapes (`.table()`/`.select()`/`.eq()`) for the experiment guard and QR-suffix lookup, matching
`test_cyl_datasets.py`'s convention (e.g. `test_fetch_datasets_builds_joined_query`), not just
outcomes.

- [x] 2.1 Experiment guard: a fake client returning a non-matching (or missing) experiment
      `12880747` causes the command to exit non-zero and make zero RPC/storage/lock calls. A
      fake client returning a matching experiment (name starting with `A4-PIPELINE-E2E-TEST`)
      allows the command to proceed. Pin the exact guard query shape.
- [x] 2.2 Lock contention: with `acquire_lock` monkeypatched to raise `LockContendedError`, the
      command exits non-zero immediately with a message identifying the lock as held, and makes
      zero RPC calls. Confirm, via a recording fake, that the lock is acquired before any
      QR-suffix query with the exact path `~/.bloom/.locks/cyl-create-test-scan-12880747.lock`
      and `staleness_seconds = DEFAULT_LOCK_STALENESS_SECONDS` (imported from `_locks`, not
      re-literaled as `900`).
- [x] 2.3 QR-code auto-increment: a fake client whose experiment query returns existing suffixes
      up to `009` results in the RPC being called with `plant_qr_code = 'TEST-E2E-010'`. Pin the
      exact suffix-lookup query shape.
- [x] 2.4 Sentinel identity values: assert every `insert_image_v2_0` call (poison and good) uses
      the fixed synthetic `phenotyper_name`/`email`, `scientist_name`/`email`, `accession_name`
      — each re-typed directly from `design.md`'s Decisions section into the test (not
      copy-pasted from `create_test_scan.py`), so a shared transcription typo between the
      implementation and this test cannot pass silently. Assert none of these three are ever
      read from any fake "existing scan" response. Separately assert `device_name` equals the
      sourced real value (`"FastScanner"`) from task 1.1, NOT a sentinel — this is the opposite
      assertion from the other three fields and is easy to get backwards.
- [x] 2.5 `--poison`: assert `insert_image_v2_0` is called exactly once with `frame_number_ = 1`
      and the expected metadata defaults (from 1.1); assert zero storage calls (`upload`,
      `download`) occur; assert no `cyl_images` update call occurs.
- [x] 2.6 `insert_image_v2_0` returns `NULL` (poison mode): assert the command exits non-zero and
      names both the QR code and the frame number (`1`) in its error, and makes zero storage
      calls.
- [x] 2.7 `--good` with one frame file (>= 1 KiB): assert the RPC call, then the exact pre-upload
      frame-count query pair from `design.md` (`.table("cyl_images").select("scan_id")
      .eq("id", image_id).single()`, then `.select("id", count="exact").eq("scan_id", scan_id)`,
      expecting count `1`), then exactly one `upload()` call against the `images` bucket at
      `cyl-images/cyl-image_{id}_{uuid}.png` (assert the prefix and suffix shape with a regex,
      not a fixed UUID), then exactly one update call setting `object_path` to that same path and
      `status` to `'SUCCESS'`.
- [x] 2.8 `--good` with multiple frame files: assert one RPC call and one upload+update pair per
      file, with `frame_number_` assigned `1, 2, 3, ...` in ascending filename order, each with a
      distinct generated path.
- [x] 2.9 `--good`, `insert_image_v2_0` returns `NULL` on the second of two frames: assert
      non-zero exit, an error naming that frame, exactly one successful upload+update for the
      first frame, and no RPC/storage call for any frame after the failing one (fail-fast, no
      partial-then-continue behavior).
- [x] 2.10 `--good`, upload succeeds but the row-update call raises: assert non-zero exit and a
      message identifying the frame/row left inconsistent (uploaded object, unset `object_path`).
- [x] 2.11 `--good`, pre-upload frame-count confirmation (the exact query pair from 2.7) returns
      a count other than the expected `N` for the Nth frame (simulating a residual race past the
      lock): assert non-zero exit before any upload call, with a message describing the
      mismatch.
- [x] 2.12 `--good` with a frame file smaller than 1 KiB: assert non-zero exit naming the file and
      the size floor, and zero `insert_image_v2_0` calls.
- [x] 2.13 `--good` with a missing or empty `--frames-dir`, or one containing only non-image
      files: assert non-zero exit and zero RPC calls.
- [x] 2.14 `--good` with a mix of image and non-image files in `--frames-dir`: assert only the
      image files are processed, in ascending filename order, and non-image files are neither
      uploaded nor counted.
- [x] 2.15 Mutual exclusivity: `--poison` + `--good` together → non-zero exit, zero RPC calls.
      Neither given → non-zero exit, zero RPC calls. `--poison --frames-dir <dir>` → non-zero
      exit, zero RPC calls.
- [x] 2.16 New upload helper (module added in 3.1): retries once on a transient (429/5xx) storage
      error and succeeds on the second attempt; does not retry a non-transient error (e.g. 403)
      and raises immediately.
- [x] 2.17 Default `-p/--profile` matches other `cyl` commands and is forwarded to
      `_authed_client`.
- [x] 2.18 CLI registration: `create-test-scan` appears under `bloomctl cyl --help`, matching
      `test_cyl_ingest.py`'s `test_cli_registration_in_help` pattern.
- [x] 2.19 `--json` / stdout-stderr convention: on success, stdout parses as exactly one JSON
      object containing the scan id and `plant_qr_code`; any progress text appears only on
      stderr. (Command-level tests monkeypatch `_authed_client` to return the fake client
      directly, never constructing a real `Credentials` object, so there is no secret value in
      scope to leak here — credential non-leak is instead guaranteed structurally by
      `Credentials.anon_key`/`password` being `field(repr=False)`.) Mirrors
      `test_cyl_datasets.py::test_list_experiment_menu_stderr_clean_stdout_json`.
- [x] 2.20 Without `--json`: stdout contains a human-readable line naming the created scan's id
      and `plant_qr_code`.
- [x] 2.21 Confirmed all of 2.1-2.20 failed for the expected reason before writing any
      implementation code: `ModuleNotFoundError: No module named 'bloomctl.cyl.create_test_scan'`
      at collection time. No isolated "red" commit was created (no CI runs on feature-branch
      pushes in this repo, and squash merges erase intermediate trees — see prior art in
      `fix-cyl-redelivery-blob-collision/tasks.md`).

## 3. Green — implementation

- [x] 3.1 Add a generic upload-object helper (new function in `src/bloomctl/_storage.py` or a
      new sibling module) mirroring `_storage.py::download_object`'s error-shaping conventions
      (`is_retryable`, `describe_storage_error`) but for `bucket.upload(...)`: retry once on a
      transient error, raise immediately otherwise, no pre-existence/checksum check.
- [x] 3.2 Add `src/bloomctl/cyl/create_test_scan.py`: pure helper functions (experiment guard
      query, `resolve_next_qr_code`, `call_insert_image`, frame-file discovery/ordering/size-floor
      filtering, the per-frame good-mode orchestration with fail-fast on any frame's failure)
      kept separate from the `@click.command`, matching this codebase's "unit-testable without a
      live server" convention (`cyl/download.py`'s module docstring). Define the sentinel
      identity constants here.
- [x] 3.3 Implement the experiment guard as the first live call the command makes.
- [x] 3.4 Wrap the entire `--good`/`--poison` critical section — QR-suffix resolution through
      every frame's RPC call, frame-count check, upload, and row update — in one single
      `with bloomctl.cyl._locks.acquire_lock(path, staleness_seconds=DEFAULT_LOCK_STALENESS_SECONDS)`
      block for the whole invocation, at the fixed lock path from `design.md`. Every abort inside
      this section must raise from inside the block (never via a caller that has already exited
      it), so the lock is always released via the primitive's own `try`/`finally`.
- [x] 3.5 Implement `--poison` and `--good --frames-dir` per the spec deltas, including the
      `NULL`-return check and the pre-upload frame-count confirmation.
- [x] 3.6 Implement `--json`/stdout-stderr output per the spec deltas.
- [x] 3.7 Register the command in `src/bloomctl/cyl/__init__.py`.
- [x] 3.8 Run the full test file; confirm every test from section 2 now passes for the right
      reason (not vacuously). Done: 29/29 new tests pass; full `bloomcli` suite is 938 passed
      (909 baseline + 29 new), 13 failed (pre-existing, Windows-platform-specific — file
      permission bits, symlinks, console-script subprocess spawning; unrelated to this change),
      7 skipped — identical failure set to the pre-change baseline. `ruff check` (pinned v0.9.9,
      matching `.pre-commit-config.yaml`) passes on all changed files.

## 4. Validation

- [ ] 4.1 `openspec validate add-cyl-test-scan-cli --strict` passes.
- [ ] 4.2 Run `/pre-merge` (lint, full `bloomcli` test suite, self-review, OpenSpec validation);
      fix anything flagged.
- [ ] 4.3 Manually exercise the command once against the real `staging-writer` profile (not
      `pipeline-staging` — see `design.md`'s Profile note; not in CI), after 4.2 is clean: create
      the scans actually needed per `design.md`'s "Which
      verification each created scan is for" (one `--good` scan for #76, one `--good` scan for
      #78, and a `--good`×2 + `--poison`×1 set for the 7.4b run), invoked **serially**. Confirm
      via a read-only query that each resulting row matches the spec (poison: `object_path IS
      NULL`; good: `status = 'SUCCESS'` with a resolvable object). Record each created scan's id,
      its intended verification, and the commit SHA the scans were created against in the PR
      description. If the command's write shape changes after this point due to review feedback,
      re-verify (not necessarily recreate) these rows before merge.
- [ ] 4.4 Open the PR (proposal + implementation bundled, per this project's convention) against
      `staging`.
