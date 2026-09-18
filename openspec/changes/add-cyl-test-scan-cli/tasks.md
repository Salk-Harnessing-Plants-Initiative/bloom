## 1. Pre-implementation verification

- [ ] 1.1 Query the `cyl_scans`/plant schema (via a throwaway read-only script under the
      `pipeline-staging` profile, deleted after use) to confirm whether `plant_qr_code` carries a
      database-level uniqueness constraint. This determines whether the race-window decision in
      `design.md` ("fails loudly on conflict") is actually backed by the database, or needs an
      explicit application-level check instead. Record the finding in `design.md` if it changes
      the decision.
- [ ] 1.2 Query one existing `TEST-E2E-*` scan's full metadata (species, wave_number, germ_day,
      germ_day_color, plant_age_days, date_scanned, device_name, accession_name, phenotyper
      name/email, scientist name/email) to source the exact default values `create-test-scan`
      will pass to `insert_image_v2_0` for every parameter other than `plant_qr_code` and
      `frame_number_`.
- [ ] 1.3 Confirm the exact frame-file ordering `--good` should use when a directory has more
      than one file (e.g. sorted by filename) and document it in the command's `--help` text.

## 2. Red — tests first (`bloomcli/tests/test_cyl_create_test_scan.py`)

Follow this codebase's existing convention: no `unittest.mock`; small hand-written fake
`_Client`/`_RPC`/storage-bucket classes (see `tests/test_cyl_ingest.py`), plus a
`_patch_authed`-style monkeypatch of `_authed_client` for command-level tests.

- [ ] 2.1 Experiment guard: a fake client returning a non-matching (or missing) experiment `12880747`
      causes the command to exit non-zero and make zero RPC/storage calls of any kind. A fake
      client returning a matching experiment allows the command to proceed.
- [ ] 2.2 `--poison`: assert `insert_image_v2_0` is called exactly once with `frame_number_ = 1`
      and the expected metadata defaults (from 1.2); assert zero storage calls (`upload`,
      `download`) occur; assert no `cyl_images` update call occurs.
- [ ] 2.3 `--good` with one frame file: assert the RPC call, then exactly one `upload()` call
      against the `images` bucket at `cyl-images/cyl-image_{id}_{uuid}.png` (assert the prefix
      and suffix shape with a regex, not a fixed UUID), then exactly one update call setting
      `object_path` to that same path and `status` to `'SUCCESS'`.
- [ ] 2.4 `--good` with multiple frame files: assert one RPC call and one upload+update pair per
      file, each with a distinct `frame_number_` and a distinct generated path.
- [ ] 2.5 `--good` with a missing or empty `--frames-dir`: assert non-zero exit and zero RPC
      calls.
- [ ] 2.6 Mutual exclusivity: both `--poison` and `--good` given → non-zero exit, zero RPC calls.
      Neither given → non-zero exit, zero RPC calls.
- [ ] 2.7 QR-code auto-increment: a fake client whose experiment query returns existing suffixes
      up to `009` results in the RPC being called with `plant_qr_code = 'TEST-E2E-010'`.
- [ ] 2.8 QR-code conflict: a fake client whose insert raises a uniqueness-violation error (shape
      confirmed in 1.1) on the chosen QR code results in a non-zero exit with a readable error
      identifying the conflict, and no retry/loop.
- [ ] 2.9 `--json` / stdout-stderr convention: on success, stdout parses as exactly one JSON
      object containing the scan id and `plant_qr_code`; any progress text appears only on
      stderr — mirror `test_cyl_datasets.py::test_list_experiment_menu_stderr_clean_stdout_json`.
- [ ] 2.10 Confirm all of 2.1-2.9 fail for the expected reason (missing implementation) before
      writing any implementation code. Capture the failing output to the scratchpad for the PR
      description; do not create an isolated "red" commit (no CI runs on feature-branch pushes in
      this repo, and squash merges erase intermediate trees — see prior art in
      `fix-cyl-redelivery-blob-collision/tasks.md`).

## 3. Green — implementation

- [ ] 3.1 Add a generic upload-object helper (new function in `src/bloomctl/_storage.py` or a
      new sibling module) mirroring `_storage.py::download_object`'s error-shaping conventions
      (`is_retryable`, `describe_storage_error`) but for `bucket.upload(...)`, with no
      pre-existence/checksum check (see `design.md`).
- [ ] 3.2 Add `src/bloomctl/cyl/create_test_scan.py`: pure helper functions
      (`resolve_next_qr_code`, `call_insert_image`, the per-frame good-mode orchestration) kept
      separate from the `@click.command`, matching this codebase's "unit-testable without a live
      server" convention (`cyl/download.py`'s module docstring).
- [ ] 3.3 Implement the experiment guard as the first live call the command makes.
- [ ] 3.4 Implement `--poison` and `--good --frames-dir` per the spec deltas.
- [ ] 3.5 Implement `--json`/stdout-stderr output per the spec deltas.
- [ ] 3.6 Register the command in `src/bloomctl/cyl/__init__.py`.
- [ ] 3.7 Run the full test file; confirm every test from section 2 now passes for the right
      reason (not vacuously).

## 4. Validation

- [ ] 4.1 `openspec validate add-cyl-test-scan-cli --strict` passes.
- [ ] 4.2 Run `/pre-merge` (lint, full `bloomcli` test suite, self-review, OpenSpec validation);
      fix anything flagged.
- [ ] 4.3 Manually exercise both modes once against the real `pipeline-staging` profile (not in
      CI): create one poison scan and one good scan (frames sourced via `bloomctl cyl download`
      against scan `12894745`), and confirm via a read-only query that the resulting rows match
      the spec (poison: `object_path IS NULL`; good: `status = 'SUCCESS'` with a resolvable
      object). Record the created scan ids in the PR description so reviewers/verifiers know
      which `TEST-E2E-NNN` scans exist for #76/#78/#56 to use.
- [ ] 4.4 Open the PR (proposal + implementation bundled, per this project's convention) against
      `staging`.
