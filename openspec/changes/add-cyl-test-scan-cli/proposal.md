## Why

Three separate verifications are blocked on the same missing thing: a cylinder scan in the
staging test experiment `A4-PIPELINE-E2E-TEST` (`experiment_id 12880747`) with no prior
`cyl_pipeline_run_scans` row.

- **sleap-roots-pipeline#76** needs a genuine recompute (different `.slp` bytes at an unchanged
  idempotency key) to check whether bloom#871's blob-collision fix (commit `28034f6d`) actually
  prevents the collision in a live run, not just in unit tests.
- **sleap-roots-pipeline#78** needs a recompute to check whether container digests reach
  `result.json` — a skipped scan keeps empty digests by design, so it can't exercise this. This
  proposal supplies the input precondition only (a fresh scan); producing the digest evidence
  itself still requires a separate, out-of-scope pipeline run.
- **sleap-roots-pipeline#56 task 7.4b** needs scans with no prior envelope to produce a clean
  `done_count=2/failed_count=1`. bloom#875/PR#880 (merged, applied at `ab779039`) does not
  rescue this: its fallback (`supabase/migrations/20260917140000_fix_cyl_redelivery_status_fallback.sql:163-192`)
  is a `SELECT` against `cyl_pipeline_run_scans.source_id`, which is only ever stamped by the
  normal dispatch/enqueue path's `UPDATE`. Sources 83-90 (and every scan in this experiment) were
  first delivered by hand-submitted `argo submit` runs, which never create a
  `cyl_pipeline_run_scans` row at all — there is nothing for the fallback to find.

A direct staging query (2026-09-18) confirms all 9 existing scans in this experiment
(`TEST-E2E-001` through `TEST-E2E-009`) already carry `cyl_pipeline_run_scans` rows across 9
historical runs (`run_id` 1-9), including the poison scan `TEST-E2E-007` (whose `object_path` is
the deliberately fake `cyl-images/POISON-NEVER-UPLOADED-12894822.png`, despite
`cyl_images.status = 'SUCCESS'`). None of the 9 qualify as "no prior envelope" — fresh scans are
required.

This exact tooling has been written from scratch and thrown away at least three times in two
days (roadmap 2026-09-01 notes `TEST-E2E-004/005/006` were created via an uncommitted ad hoc
script; `TEST-E2E-008/009` are further undocumented instances found only by this proposal's DB
query). Nothing has been committed. This change commits it once, as a `bloomctl` subcommand.

## What Changes

- Add `bloomctl cyl create-test-scan`, a new subcommand hardcoded to operate only against
  experiment `12880747` ("A4-PIPELINE-E2E-TEST"), with two mutually exclusive modes:
  - `--poison`: calls `insert_image_v2_0` only. One frame. `object_path` is left `NULL` and
    `status` stays `'PENDING'` (the RPC's own default) — reproducing the same
    "1 of 1 frames failed to download" failure already seen on scan `12894751`.
  - `--good --frames-dir <path>`: calls `insert_image_v2_0`, uploads each frame file in
    `<path>` to the `images` bucket at `cyl-images/cyl-image_{cyl_images.id}_{uuid4()}.png`
    (the same convention `packages/bloom-fs`'s real `uploadImage` uses for every actual Bloom
    Desktop upload), then updates each `cyl_images` row's `object_path` and sets
    `status = 'SUCCESS'`.
- One scan created per invocation, run **serially**. See `design.md`'s "Which verification each
  created scan is for" for how many scans and which allocation each of #76/#78/#56-7.4b actually
  needs — it is not one shared batch.
- The scan's `plant_qr_code` (`TEST-E2E-NNN`) is chosen automatically: the command queries
  experiment `12880747` for the current highest suffix and uses the next one, while holding a
  same-machine file lock (see below) across the suffix selection and the RPC call.
- A live guard verifies the target experiment is actually `12880747` /
  `'A4-PIPELINE-E2E-TEST'` before any mutation, and refuses (no partial writes) if that lookup
  doesn't match expectations.
- Concurrent invocations on the same machine are serialized via `bloomctl.cyl._locks.acquire_lock`
  (the existing file-lock primitive), failing fast rather than silently merging two scans into
  one row — see `design.md` for why the RPC's own `ON CONFLICT ... DO NOTHING` upserts cannot be
  relied on to reject a race.
- An `insert_image_v2_0` result of `NULL` (the row already exists as `SUCCESS`) is treated as a
  hard failure, never a silent proceed, for every frame in every mode.
- Identity fields (`phenotyper_name`/`email`, `scientist_name`/`email`, `accession_name`,
  `device_name`) use fixed synthetic sentinel values, never values copied from an existing scan
  — those tables are upserted globally with no experiment scoping, so copying forward risks
  attaching a synthetic scan to a real staff member's or real accession's row.
- `--good` frame files below 1 KiB are rejected before any RPC call, as a cheap guard against the
  already-observed failure mode of accidentally using blank/placeholder imagery, which would
  defeat sleap-roots-pipeline#76's entire purpose.
- New unit tests following this codebase's existing hand-written-fake convention (no
  `unittest.mock`), covering both modes, the experiment guard, the lock, the `NULL`-return case,
  the sentinel identity values, the size floor, and the QR-code auto-increment.

**Not changed**: no schema/migration changes; no changes to `packages/bloom-fs` or
`packages/bloom-js`; no changes to any experiment other than `12880747`; sleap-roots-pipeline#71
(manifest union/prune) is not addressed here — it will inflate envelope counts for any scan this
tool creates once run through the pipeline, and is noted in `design.md` as a known confound, not
fixed by this change.

## Impact

- Affected specs: `cyl-test-scan-cli` (new capability)
- Affected code:
  - New: `bloomcli/src/bloomctl/cyl/create_test_scan.py`, `bloomcli/tests/test_cyl_create_test_scan.py`
  - Modified: `bloomcli/src/bloomctl/cyl/__init__.py` (register the new command)
  - Modified: `bloomcli/src/bloomctl/_storage.py` (or a new sibling module) to add a generic
    upload-object helper — bloomctl currently has download-only storage helpers
  - Reused, unmodified: `bloomcli/src/bloomctl/cyl/_locks.py::acquire_lock` (existing file-lock
    primitive, reused to serialize same-machine invocations)
