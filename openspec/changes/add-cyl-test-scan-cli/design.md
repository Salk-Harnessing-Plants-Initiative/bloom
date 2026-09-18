## Context

Every real cylinder scan reaches Bloom through the same three-step flow, verified against source
in this checkout:

1. `insert_image_v2_0(...)` RPC
   (`supabase/migrations/20250626171608_fixes_to_insert_image_2.0_rpc.sql`) upserts
   experiment/wave/accession/plant/phenotyper/scientist/scan rows as needed and inserts a
   `cyl_images` row with `status = 'PENDING'`, `object_path` left `NULL` (`:114`). It `RETURNS
   bigint` — the image row's id, or `NULL` if that row is already `'SUCCESS'`.
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

## Goals / Non-Goals

Goals:

- A single committed, tested way to create a fresh scan with no prior pipeline-run history in
  experiment `12880747`, in either a "poison" (undownloadable) or "good" (real, recompute-worthy
  imagery) shape.
- Make it structurally impossible (not just documented) for this command to touch any experiment
  other than `12880747`.

Non-Goals:

- Reusing or rehabilitating any of the 9 existing `TEST-E2E-*` scans — confirmed unusable for
  this purpose (see proposal.md).
- Fixing sleap-roots-pipeline#71 (manifest union/prune). Scans created by this tool will still
  hit that bug once run through the pipeline; noted here as a known confound for anyone using
  this tool to verify #76/#78/#56, not addressed by this change.
- A Node/TypeScript implementation, or any change to `bloom-fs`/`bloom-js`.
- General-purpose synthetic-data tooling for experiments other than `12880747`.

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
  in storage, which matters for the recompute verifications this tool exists to unblock (they
  are checking pipeline behavior against realistic inputs, not against a hand-picked test-only
  layout). It is also what `TEST-E2E-004` through `009` already used.

- **Decision: no checksum/overwrite reconciliation in the new upload helper.** Unlike
  `cyl/ingest.py::upload_blob` (which must handle re-delivery at a stable idempotency-keyed
  path), `create-test-scan` always generates a fresh `uuid4()` per call, so the object path it
  writes has never existed before. A collision would only be possible via an actual UUID
  collision, which is not worth guarding against. The new helper is a plain upload with no
  pre-existence check.

- **Decision: QR-code suffix is auto-incremented, with a documented (not silently ignored) race
  window.** The command queries experiment `12880747` for the current highest `TEST-E2E-NNN`
  suffix and uses the next integer, zero-padded to 3 digits to match the existing style. Because
  `plant_qr_code` most likely carries a uniqueness constraint at the database level (to be
  confirmed against the `cyl_scans`/plant table during implementation), two concurrent
  invocations racing for the same next number will have one succeed and one fail outright on
  insert conflict — not silently overwrite or corrupt state. The command surfaces that failure
  as a clear, non-zero-exit error (not a retry loop) and its `--help`/error text will tell the
  caller to re-run. A retry-with-recompute-next-number loop was considered and rejected as
  unnecessary complexity for a tool that runs rarely and by hand; if this changes (e.g. it starts
  running unattended/concurrently), revisit.

- **Decision: `--good` sources frames from a local directory only** (`--frames-dir <path>`), with
  no built-in "pull frames from another scan" convenience. Keeps this command's responsibility
  narrow (create a scan from given bytes) and composable with the existing `bloomctl cyl
  download` command, which callers use first to populate that directory from a known-good source
  scan (e.g. `12894745` / `TEST-E2E-001`).

- **Decision: the experiment guard is a live query, not a hardcoded id check alone.** Before any
  mutation, the command looks up experiment `12880747` and confirms its name matches
  `'A4-PIPELINE-E2E-TEST'` (allowing for the descriptive suffix already observed:
  `'A4-PIPELINE-E2E-TEST (synthetic -- safe to break/delete)'` — match on a stable prefix, not
  exact string equality, since the descriptive suffix is not part of the contract). If the
  experiment doesn't exist or the name doesn't match, the command raises `click.ClickException`
  and performs no writes. This is deliberately redundant with hardcoding `12880747` as the only
  accepted value — a wrong profile pointed at a different database, or a renamed/deleted test
  experiment, must fail loudly rather than silently create scans somewhere unintended.

## Risks / Trade-offs

- **Concurrent invocations racing for the same QR-code suffix** → mitigated by relying on a
  database-level uniqueness constraint to fail one of the two outright, plus a clear error
  message; not eliminated, but confirmed non-corrupting. (Implementation must confirm the
  constraint actually exists — if it doesn't, this decision is revisited before merge.)
- **Duplication with `bloom-fs`/`bloom-js`'s TypeScript implementation of the same flow** → same
  trade-off already accepted elsewhere in `bloomctl`; mitigated by keeping the new code small and
  by this design doc recording the mirrored convention so future drift is detectable by diffing
  against `metadata.ts`.
- **sleap-roots-pipeline#71 (manifest union/prune) inflates envelope counts** for any scan this
  tool creates, once that scan is actually run through the pipeline. Not this change's bug to
  fix; verification plans built on top of this tool should account for it separately.
