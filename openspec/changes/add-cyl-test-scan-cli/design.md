## Context

Every real cylinder scan reaches Bloom through the same three-step flow, verified against source
in this checkout:

1. `insert_image_v2_0(...)` RPC
   (`supabase/migrations/20250626171608_fixes_to_insert_image_2.0_rpc.sql`) upserts
   experiment/wave/accession/plant/phenotyper/scientist/scan rows as needed and inserts a
   `cyl_images` row with `status = 'PENDING'`, `object_path` left `NULL` (`:114`). It `RETURNS
   bigint` — the image row's id, **or `NULL` if that row is already `'SUCCESS'`** (`:122-129`).
2. The frame bytes are uploaded to the `images` storage bucket.
3. The `cyl_images` row is updated by id: `object_path` set, `status` set to `'SUCCESS'`
   (confirmed live: every completed row sampled across staging, including all of experiment
   `12880747`'s existing rows, carries this exact literal, all-caps).

There is no path convention baked into the schema or the read path — `object_path` is stored and
read back verbatim (`bloomcli/src/bloomctl/cyl/download_for_predict.py`,
`bloomcli/src/bloomctl/cyl/download.py` fetch by `scan_id` with no status filter) — so the upload
path is a free choice for whoever writes it.

The real, production implementation of this flow lives only in TypeScript:
`packages/bloom-js/src/core/supabase/data-store.ts` (`insertImageMetadata`,
`updateImageMetadata`) and `packages/bloom-fs/src/cyl/metadata.ts`'s `uploadImage` (the
orchestration: RPC → upload to `images` bucket at `cyl-images/cyl-image_{id}_{uuid}.png` →
update row). `bloomctl` has never implemented this flow; it has download helpers only
(`_storage.py::download_object`) and one *different*-bucket upload (`cyl/ingest.py::upload_blob`,
targeting `cyl-intermediates`, with checksum-before-overwrite reconciliation logic that does not
apply here since every path this command writes is a freshly generated UUID).

**Every upsert inside `insert_image_v2_0` is `ON CONFLICT ... DO NOTHING` followed by a fallback
`SELECT` of whatever row already exists** — this includes `cyl_plants` (unique on
`(wave_id, qr_code)`), `cyl_scans` (unique on `(plant_id, date_scanned)`), and `cyl_images`
(unique on `(scan_id, frame_number)`). Confirmed directly against
`supabase/migrations/20230724171639_add_uniqueness_contraints.sql`: all three constraints exist
exactly as described (plus `cyl_scanners UNIQUE (name)`, consistent with the RPC's strict
existence check on `device_name` below). This pattern **never raises a uniqueness-violation error**
— an OpenSpec review of this proposal's first draft caught this and correctly flagged the
original design (below, in "Superseded decision") as factually wrong. It matters because it is
also what makes the plant/scan/image lineage safe from cross-attaching to *unrelated* real data:
since `wave_id` is always derived from experiment `12880747` and this tool always mints a
brand-new `qr_code`, the plant/scan chain can never resolve to an existing *real* experiment's
plant or scan. It can, however, resolve two of *our own* concurrent calls onto the *same* row
(see Decisions below).

## Goals / Non-Goals

Goals:

- A single committed, tested way to create a fresh scan with no prior pipeline-run history in
  experiment `12880747`, in either a "poison" (undownloadable) or "good" (real, recompute-worthy
  imagery) shape.
- Make it structurally impossible (not just documented) for this command to touch any experiment
  other than `12880747`, or to attach a synthetic scan's identity fields to a real staff member
  or real accession record.
- Reduce (not eliminate — see Non-Goals) the risk of two concurrent invocations on the same
  machine silently merging into one shared row.

Non-Goals:

- Reusing or rehabilitating any of the 9 existing `TEST-E2E-*` scans — confirmed unusable for
  this purpose (see proposal.md).
- Guaranteeing safety against concurrent invocations from **different machines**, or from any
  caller that bypasses this command's lock file. True mutual exclusion would require either a
  schema change (a dedicated claim table or a wrapping RPC that holds a lock across the
  suffix-selection and insert steps) or a persistent database session held open across two
  HTTP calls (not available through this project's PostgREST-fronted Supabase client). Out of
  scope for this change; flagged as a follow-up if this tool starts running unattended or from
  multiple hosts.
- Fixing sleap-roots-pipeline#71 (manifest union/prune). Scans created by this tool will still
  hit that bug once run through the pipeline; noted here as a known confound for anyone using
  this tool to verify #76/#78/#56, not addressed by this change.
- A Node/TypeScript implementation, or any change to `bloom-fs`/`bloom-js`.
- General-purpose synthetic-data tooling for experiments other than `12880747`.
- Validating that `--good` frames are scientifically "real" root imagery in any strong sense —
  only a cheap, mechanical guard against the *specific* failure mode already observed twice (see
  Decisions).

## Decisions

- **Decision: implement in `bloomctl` (Python), not a Node runner against `bloom-fs`/`bloom-js`.**
  `bloomctl` already owns the credentials/profile plumbing
  (`~/.bloom/credentials.<profile>.txt` via `src/bloomctl/credentials.py`, `_authed_client` in
  `cli.py` — the `pipeline-staging` profile already works, zero code changes needed), an
  authenticated Supabase client factory, and the RPC-call/error-mapping conventions
  (`cyl/ingest.py`, `cyl/datasets.py`). It is committed and released by construction, exactly
  like every other `bloomctl` command.
  - Alternative considered: a Node script importing `@salk-hpi/bloom-js` + `bloom-fs` to reuse
    the real TypeScript call site exactly (avoiding a second implementation of the RPC-call
    shape). Rejected: neither package exposes a `bin`, nothing in this repo currently depends on
    `bloom-fs`, and this would require new build/wiring in a repo that doesn't consume it today.
  - Accepted trade-off: this duplicates the RPC-call and upload-orchestration shape that already
    exists in TypeScript (`data-store.ts:33-109`, `metadata.ts:392-431`) — the two can drift.
    `bloomctl` already accepts this same trade-off for its existing download path
    (`cyl/download.py` duplicates read-side logic bloom-js also has), so this is consistent with
    established practice, not a new risk class.

- **Decision: object path mirrors `bloom-fs`'s real convention exactly** —
  `cyl-images/cyl-image_{cyl_images.id}_{uuid4()}.png` — rather than the alternate
  `a4-pipeline-e2e-test/<QR_CODE>/<NN>.png` convention that `TEST-E2E-001` through `003` happen
  to use. This is the most faithful match to what a real Bloom Desktop upload actually produces
  in storage, which matters for the recompute verifications this tool exists to unblock.

- **Decision: no checksum/overwrite reconciliation in the new upload helper**, but it must
  retry once on a transient (429/5xx) storage error and raise immediately on anything else,
  matching `_storage.py::download_object`'s `is_retryable`/`describe_storage_error`
  conventions. Unlike `cyl/ingest.py::upload_blob` (which must handle re-delivery at a stable
  idempotency-keyed path), `create-test-scan` always generates a fresh `uuid4()` per call, so a
  pre-existing-object check is unnecessary.

- **Decision (RPC returns `NULL`): treat it as a hard, loud failure, never a silent proceed.**
  The RPC returns `NULL` only when the resolved `cyl_images` row is already `'SUCCESS'`. For
  this command, that can only mean either a genuine bug in QR-suffix selection (we picked an
  already-used suffix) or a race that the lock (below) failed to prevent. On `NULL`, the command
  SHALL abort before any storage call with a message naming the QR code that produced it, for
  both `--poison` and `--good`, and for every frame in a multi-frame `--good` scan.

- **Decision: same-machine concurrent invocations are serialized with a file lock; this is a
  mitigation, not a guarantee.** *Superseded decision, kept here for the record*: the original
  draft of this design claimed a concurrent QR-suffix collision would "fail loudly" on a
  database uniqueness violation. That is false — see Context above; the RPC's `ON CONFLICT ...
  DO NOTHING` + fallback `SELECT` pattern never raises, it silently returns the *other* caller's
  row id, and both callers would then upload different bytes to different storage paths but
  `UPDATE` the *same* `cyl_images` row, with the loser's bytes becoming an orphaned, unreferenced
  object and its own command falsely reporting success.
  The fix: wrap "resolve next QR suffix → call `insert_image_v2_0`" in
  `bloomctl.cyl._locks.acquire_lock`, the existing file-based lock primitive already used by
  `download_for_predict.py`, keyed on a fixed, well-known path (e.g.
  `~/.bloom/.locks/cyl-create-test-scan-12880747.lock`) shared by every invocation of this
  command on the same machine. A second invocation contending for the lock raises
  `LockContendedError` immediately (no blocking/retry loop, matching the primitive's existing
  fail-fast design) with a message telling the caller to retry once the first invocation
  finishes. This directly addresses the actual, previously observed risk in this project
  (multiple concurrent agent sessions on the same development machine each trying to create a
  test scan — this is not hypothetical; it is why this exact tooling was rewritten from scratch
  three times). It does **not** protect against a caller on a different machine, or any caller
  that constructs the RPC call without going through this command — see Non-Goals.
  As defense in depth against the residual window (e.g. a stale lock reclaimed while a peer is
  still slow, or a non-`bloomctl` caller), the command also verifies the RPC's returned image id
  is genuinely new via the `NULL`-return check above, and — for `--good` — the scan's frame
  count is confirmed before uploading, aborting loudly on a mismatch rather than silently
  overwriting/attaching to an unexpected existing scan. **This is not a complete guarantee even
  within its intended residual window**: two racers colliding on the exact same *first* frame of
  a QR code both get the same `image_id` back (`insert_image_v2_0`'s row is `PENDING`, not
  `SUCCESS`, so the `NULL`-check doesn't fire either) and both see the same count — the
  frame-count check can only detect a race once one side has progressed to a later frame before
  the other checks, which is timing-dependent, not assured. Caught by an OpenSpec review;
  recorded here rather than implied as closed. This is bounded by the staleness threshold below,
  not absolute: while a live invocation's actual runtime stays under it, a second invocation is
  refused outright; if a live invocation somehow runs longer than the threshold, its lock becomes
  reclaimable and a peer could proceed
  concurrently, which is exactly the residual window the frame-count check exists to catch.

  **Concrete lock parameters** (an OpenSpec review of an earlier draft found this decision named
  no actual value, which is required — `acquire_lock`'s `staleness_seconds` has no default and
  rejects non-positive/NaN values): use `bloomctl.cyl._locks.DEFAULT_LOCK_STALENESS_SECONDS`
  (900 seconds), imported rather than re-literaled, at lock path
  `~/.bloom/.locks/cyl-create-test-scan-12880747.lock`. This command's workload (one scan,
  sequential frames, no concurrent workers) is strictly lighter than
  `download_for_predict.py`'s batch-download workload that constant was already sized for, so it
  comfortably covers even a slow multi-frame `--good` invocation with retries, while still
  bounding a genuinely crashed/killed process to 15 minutes before a peer can reclaim. No CLI
  override is exposed (unlike `download_for_predict --lock-staleness-seconds`) — this command has
  no legitimate reason to run long enough to need one.

  **Concrete frame-count-check mechanism**: `insert_image_v2_0` returns only the new/existing
  `cyl_images.id`, not its `scan_id` — so after a non-`NULL` RPC result, the command SHALL first
  resolve `scan_id` with `client.table("cyl_images").select("scan_id").eq("id", image_id)
  .single().execute()`, then count sibling rows with `client.table("cyl_images")
  .select("id", count="exact").eq("scan_id", scan_id).execute()`. The expected count for the Nth
  frame processed in this invocation (1-indexed) is exactly `N`; any other observed count aborts
  the command before the upload call for that frame.

  The entire critical section — QR-suffix resolution through every frame's RPC call, frame-count
  check, upload, and row update — SHALL be wrapped in one single `with acquire_lock(...)` block
  for the whole invocation. Every abort inside that section (`NULL`-return, frame-count mismatch,
  upload failure, update failure, size-floor rejection for frame 2+) SHALL raise from inside the
  block, never via a caller that has already exited it, so the lock is always released via the
  primitive's own `try/finally`.

- **Decision: identity fields (`phenotyper_name`/`email`, `scientist_name`/`email`,
  `accession_name`) use fixed, dedicated synthetic sentinel values, never copied from an
  existing `TEST-E2E-*` row.** `phenotypers`, `cyl_scientists`, and `accessions` are upserted by
  the RPC on a *global* natural key (`email`, `email`, `name` respectively) with no experiment
  scoping at all — unlike the plant/scan chain, these are not protected by experiment
  `12880747`'s isolation. An OpenSpec review flagged that copying these fields forward from an
  existing `TEST-E2E-*` scan (as originally planned) risks silently attaching a synthetic image
  to a *real* staff member's or *real* accession's row, if whichever engineer hand-created the
  first test scan used their own real bloomctl login. Fixed values used instead:
  `phenotyper_name = "Synthetic Test Phenotyper"`,
  `phenotyper_email = "synthetic-test-phenotyper@bloom.invalid"`,
  `scientist_name = "Synthetic Test Scientist"`,
  `scientist_email = "synthetic-test-scientist@bloom.invalid"`,
  `accession_name = "SYNTHETIC-TEST-ACCESSION"`
  (`.invalid` is the reserved TLD for exactly this purpose, per RFC 2606, so it can never
  collide with a real address).

  **`device_name` is NOT one of these sentinel fields — verified during implementation
  (task 1.1) that treating it as one would break the command.** Unlike phenotyper/scientist/
  accession, `device_name` is never upserted: the RPC does
  `SELECT id INTO scanner_id_var FROM cyl_scanners WHERE name = device_name` and
  `RAISE EXCEPTION 'Scanner % does not exist', device_name` if no row matches (`:41-43`). A
  made-up sentinel scanner name would make every single invocation fail. `cyl_scanners` holds
  hardware scanner identifiers, not personal/accession data, so there is no cross-attachment
  risk in reusing a real one — this field moves to the "sourced from an existing scan" bucket
  below. Confirmed value: `device_name = "FastScanner"` (id `1`, the scanner every existing
  `TEST-E2E-*` scan already uses).

  Fields that describe the *wave/plant batch itself* rather than a person, accession, or piece
  of hardware — `species_common_name`, `wave_number`, `germ_day`, `germ_day_color`,
  `plant_age_days`, `date_scanned_`, and now `device_name` — are sourced from an existing
  `TEST-E2E-*` scan's real values (task 1.1), since those legitimately describe the shared
  experiment/wave context and have no cross-attachment risk (they're plain columns or a shared
  hardware reference, not natural-key lookups into a global table of people/organizations).
  Confirmed values, read from scan `12894745` (`TEST-E2E-001`) via the `cyl_scans_extended`
  view under the `staging-writer` profile on 2026-09-21: `species_common_name = "Canola"`,
  `wave_number = 9999`, `germ_day = 1`, `germ_day_color = "TestGray"`, `plant_age_days = 2`,
  `date_scanned_ = "2026-08-24"` (reused verbatim — `cyl_scans`'s uniqueness is on
  `(plant_id, date_scanned)`, and `plant_id` is always new per this tool's fresh `qr_code`, so
  reusing the same date creates no collision), `device_name = "FastScanner"`.

  **Profile note**: this lookup also revealed that `pipeline-staging` (the profile named in the
  original handoff) cannot itself read `cyl_scanners` — it returns `permission denied for table
  cyl_scanners` (`42501`), because `insert_image_v2_0` has no `SECURITY DEFINER` clause and so
  runs with the *caller's* privileges, and `pipeline-staging`'s role lacks the table-level grant
  `bloom_user` has. (The earlier successful read via the `cyl_scans_extended` *view* is not
  evidence otherwise — that view has no `WITH (security_invoker = true)` clause, so it silently
  runs as the view owner and bypasses the caller's own grants entirely.) This matches how the
  original synthetic scans were actually created: a prior task note (PR #774) describes them as
  made "via `insert_image_v2_0` under the `staging-writer` profile." **This command must be
  exercised with `-p staging-writer` (or an equivalently write-capable profile), not
  `-p pipeline-staging`** — noted here and in the command's `--help` text so a future caller
  doesn't waste time on a permission error that looks like a bug in this tool.

- **Decision: QR-code suffix is auto-incremented.** The command queries experiment `12880747`
  for the current highest `TEST-E2E-NNN` suffix and uses the next integer, zero-padded to 3
  digits to match the existing style, computed and used while holding the lock above.

- **Decision: on any frame's failure, `--good` aborts immediately rather than continuing with
  remaining frames.** If a frame is below the size floor, its RPC call returns `NULL`, its
  upload raises, or its row update raises, the command stops processing further frames in that
  invocation and exits non-zero, naming the failing frame. This is fail-fast, consistent with
  holding the lock for the shortest necessary time, and avoids reasoning about a scan left in a
  mixed "some frames uploaded, some not" state as a supported outcome.

- **Decision: `--good` sources frames from a local directory only** (`--frames-dir <path>`), with
  no built-in "pull frames from another scan" convenience. Keeps this command's responsibility
  narrow (create a scan from given bytes) and composable with the existing `bloomctl cyl
  download` command, which callers use first to populate that directory from a known-good source
  scan (e.g. `12894745` / `TEST-E2E-001`). Image-file allowlist: `.png`, `.jpg`, `.jpeg`, `.tif`,
  `.tiff` (case-insensitive) — matches what `cyl/download.py::image_dest` produces (it preserves
  the source object's real suffix, defaulting to `.png`), so a directory populated by
  `bloomctl cyl download` is recognized with no extra steps.

- **Decision: guard against the two already-observed ways `--good` can be misused into
  producing scans that don't actually exercise a real recompute.** The original scope note
  ("frames come from a local directory") does not by itself prevent a caller from pointing
  `--frames-dir` at blank, single-color, or otherwise trivial placeholder images — which an
  OpenSpec review flagged as directly defeating this tool's stated purpose for sleap-roots-
  pipeline#76 (already happened twice on 2026-09-17 per the handoff this proposal is based on:
  two live runs re-delivered byte-identical output because `predict` skipped non-substantive
  input). Two guards, both cheap and mechanical rather than attempting real image understanding:
  1. Each frame file must exceed a minimum size floor (1 KiB). A blank or single-color PNG
     compresses to a few hundred bytes; a real root-scan photograph does not. This is a
     necessary-not-sufficient check, not a content classifier.
  2. The command's `--help` text and a stderr warning explicitly state that frames must be
     copied from a real prior scan's imagery (e.g. via `bloomctl cyl download`), name the
     already-known-good source (`12894745` / `TEST-E2E-001`), and state plainly that
     synthetic/blank frames will not exercise a real recompute.
  This does not eliminate caller error, but it converts "silently produces a scan that looks
  successful but structurally can't test what it's for" into either a hard failure (size floor)
  or an impossible-to-miss warning.

- **Decision: the experiment guard is a live query, not a hardcoded id check alone.** Before any
  mutation, the command looks up experiment `12880747` and confirms its name starts with
  `A4-PIPELINE-E2E-TEST` (a prefix match, not exact equality, since the live name carries a
  descriptive suffix: `'A4-PIPELINE-E2E-TEST (synthetic -- safe to break/delete)'`). If the
  experiment doesn't exist or the name doesn't match, the command raises `click.ClickException`
  and performs no writes.

## Which verification each created scan is for

This tool does not produce a single batch that simultaneously satisfies all three blocked
verifications:

- **sleap-roots-pipeline#76 / #78**: each needs its own fresh `--good` scan run through the
  pipeline individually — reusing one scan across both consumes its "no prior envelope" property
  for the second use. Create a separate scan per verification.
- **sleap-roots-pipeline#56 task 7.4b**: needs a *pipeline run* (not just scans sitting in the
  experiment) that ends with `done_count=2, failed_count=1` — i.e. 2 successful and 1 failed
  scan **submitted together as one run**. Creating "3 good + 1 poison" scans with this tool does
  not by itself produce that split; the caller must separately dispatch a run containing exactly
  2 of the good scans plus the poison scan (an out-of-scope, downstream step — this tool only
  creates the input rows).
- Suggested allocation, to avoid needing to re-derive this accounting later: create one `--good`
  scan for #76, one `--good` scan for #78, and a separate `--good`×2 + `--poison`×1 set
  dedicated to 7.4b's run. Record each created scan's id and its intended verification in the
  PR description (task 4.3).
- Invocations must be run **serially** (one at a time), even across the different scans above —
  the lock in the Decisions section serializes same-machine calls but a caller manually running
  several terminals/agents in parallel to "go faster" defeats its own purpose.

## Risks / Trade-offs

- **Concurrent invocations on the same machine** → mitigated by the file lock (fail-fast, not
  blocking); **not** mitigated across machines or non-`bloomctl` callers (see Non-Goals). Defense
  in depth via the `NULL`-return check and a pre-upload frame-count confirmation — though that
  confirmation has its own blind spot for a same-first-frame collision, see the Decisions
  section's "same-machine concurrent invocations" note.
- **A `--good` scan whose Nth frame fails leaves frames 1..N-1 as ordinary `SUCCESS` rows on a
  real `cyl_scans`/`cyl_plants` chain, with nothing marking the scan itself as incomplete.**
  A later query against `cyl_images` for that `scan_id` cannot distinguish an aborted
  multi-frame attempt from an intentionally-shorter scan — the failure is only visible to
  whoever ran the CLI at that moment (stderr + non-zero exit), not persisted anywhere. Accepted
  as the direct consequence of the fail-fast decision above (Risk, not a defect): anyone later
  querying experiment `12880747`'s data should be aware a partial scan is indistinguishable from
  a deliberate one at the row level.
- **Duplication with `bloom-fs`/`bloom-js`'s TypeScript implementation of the same flow** → same
  trade-off already accepted elsewhere in `bloomctl`; mitigated by keeping the new code small and
  by this design doc recording the mirrored convention so future drift is detectable by diffing
  against `metadata.ts`.
- **sleap-roots-pipeline#71 (manifest union/prune) inflates envelope counts** for any scan this
  tool creates, once that scan is actually run through the pipeline. Not this change's bug to
  fix; verification plans built on top of this tool should account for it separately.
- **The frame-size floor is a heuristic, not a guarantee of real imagery** — a caller could still
  defeat it with a large-but-still-trivial file. Accepted: the goal is raising the bar against
  the *specific* accidental failure already observed twice, not building a content classifier.
- **Every scan this tool ever creates shares one fixed synthetic accession/phenotyper/scientist
  identity.** Confirmed (OpenSpec review) this has no cross-experiment blast radius — Bloom's
  heritability/cross-experiment tooling scopes its queries to one named experiment, so a global
  shared identity can't leak into unrelated real experiments' analyses. The one real effect is
  local to experiment `12880747` itself: running a genotype/accession-based tool (e.g.
  `heritability_analysis`) against `12880747` would see all synthetic scans collapse into one
  genotype bucket with zero within-group variance. `12880747` is explicitly "synthetic -- safe to
  break/delete" and not used for real accession-based science, so this is accepted as-is — never
  point genotype/heritability tooling at experiment `12880747`.
- **Every RPC call burns one identity-sequence value per upserted table** (`phenotypers.id`,
  `cyl_scientists.id`, `accessions.id`, and similarly for the plant/scan/image chain), even on a
  call whose `ON CONFLICT DO NOTHING` branch fires, because Postgres advances
  `GENERATED BY DEFAULT AS IDENTITY` sequences during `VALUES` evaluation before the conflict is
  checked. Harmless at `bigint` range for a tool run at most a few dozen times; noted so a future
  reader isn't surprised the persisted row's id is higher than the number of scans actually
  created.
