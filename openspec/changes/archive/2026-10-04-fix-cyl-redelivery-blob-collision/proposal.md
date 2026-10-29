## Why

`bloomctl cyl ingest-result`/`batch-ingest-result` uploads an envelope's `.slp` blobs before
calling `insert_cyl_result_envelope`, so the upload's strict refuse-to-overwrite check runs
ahead of the RPC's lenient `ON CONFLICT (idempotency_key) DO NOTHING` gate and execution never
reaches it. Re-delivering an envelope whose producer recomputed its artifacts therefore fails
hard — measured live on 2026-09-16 as `Ingested 0/2 envelopes (2 failed)`, with nothing reaching
the RPC ([sleap-roots-pipeline#76](https://github.com/talmolab/sleap-roots-pipeline/issues/76)).

See `design.md` for the three interacting behaviours, the live-run evidence, and why a pin bump
is not the cause.

## What Changes

- Check the idempotency gate before uploading: when `cyl_trait_sources` already holds the
  envelope's `provenance.idempotency_key`, skip the upload and the `blobs` merge, and go
  straight to the RPC.
- Place that check immediately before `upload_pending_blobs` — **after** the manifest read and
  blob construction, not before them. Every existing fail-fast guarantee (missing or malformed
  manifest, missing `.slp` on disk, `slp_path` traversal, conflicting pre-existing `blobs`
  entry) therefore still runs unchanged, and `ingest_result`'s deliberate
  construct-before-authenticate ordering (`ingest.py:893-901`) is untouched.
- Keep calling the RPC unconditionally — the gate's read is advisory and non-transactional, and
  the RPC is the only authority on whether a delivery writes anything. (It does **not** rescue a
  scan stranded at `queued`; an earlier draft said so and was wrong — see `design.md`.)
- Degrade loudly, not silently: any failure of the check itself is treated as "not already
  ingested" and falls through to today's behaviour, **and** emits a warning naming the likely
  missing grant. A silent swallow of exactly this failure shape already cost this project
  production data (`tests/unit/test_cyl_scan_videos_grants.py:1-11`: a 42501 swallowed by
  `_record_video`, "production came to hold zero rows against 84,748 stored videos").
- Add one forward-only migration granting `SELECT (idempotency_key)` on
  `public.cyl_trait_sources` to `bloom_workflows`, plus its `supabase/rollbacks/` partner.
- Add the regression test that does not exist today, and repair two existing integration tests
  that currently assert the behaviour this change removes.

Not included, filed separately: the orphan-blob wedge (bytes uploaded, delivery died before the
RPC, recomputed later — no source row exists, so the check correctly reports "not ingested" and
the collision still hard-fails); blob re-healing when a recorded `s3_location` 404s; `predict`
byte-determinism (different repo, and a GPU-cost event to schedule deliberately).

## Impact

- **Affected specs:** `cyl-ingest-cli`, `cyl-batch-ingest-result`, `cyl-trait-writeback`
- **Affected code:** `bloomcli/src/bloomctl/cyl/ingest.py` (`source_already_ingested` +
  guards in `ingest_one_envelope` and `ingest_result`; docstrings, help text on both commands, the construct-before-authenticate comment, and
  the collision error text; plus `ScanResult.warning` in `_batch.py`)
- **Affected tests:** `bloomcli/tests/test_cyl_ingest.py`,
  `bloomcli/tests/test_cyl_ingest_integration.py` (two tests invert and must be re-aimed at the
  orphan path), new `tests/integration/` grant test, new `tests/unit/` static grant guard
- **Affected migrations:** one new `supabase/migrations/` file (timestamp must exceed
  `20260915120000`, which an in-flight sibling branch already claims) + one
  `supabase/rollbacks/` partner
- **Affected docs:** `bloomcli/README.md:510-519` (states the invalidated ordering and an
  unconditional fail-fast absolute), `_WIKI/SUPABASE/README.md:114` (enumerates the exact column
  grants on `cyl_trait_sources`), `bloomcli/CHANGELOG.md` `[Unreleased]`
- **Privilege delta is near-zero:** `SELECT (id, metadata)` plus the
  `workflows_read_cyl_trait_sources` policy are already granted
  (`20260730120000_create_cyl_pipeline_runs.sql:167-169` and `:172`), and `metadata` is the
  provenance object, which already contains `idempotency_key`. The grant adds an indexed access
  path to a value the role can already read.
- **Not done at merge, and production is not covered by it.** This PR targets `staging`, so the
  grant reaches the staging DB only; `origin/main` is 22 migrations behind (241 commits). Production
  write-back pods will run the new image *without* the grant until a staging→main promotion
  lands, where the check fails open and behaves exactly as today. `tasks.md` §9 tracks the
  staging verification, the Argo pin (in `runai-busch-lab`, shared by staging **and**
  production), and the separate production verification.
