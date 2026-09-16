## Why

`bloomctl cyl ingest-result`/`batch-ingest-result` uploads an envelope's `.slp` blobs
*before* calling `insert_cyl_result_envelope`. The RPC is deliberately lenient — its
`ON CONFLICT (idempotency_key) DO NOTHING` gate makes a re-delivery a no-op — but the
upload is deliberately strict, refusing to overwrite an object whose checksum differs.
The strict step runs first, so execution never reaches the lenient one.

That breaks a guarantee both the command's own docstring and
`cyl-ingest-cli`'s "Re-ingest is a benign, distinctly-reported no-op" requirement
already promise. Measured live on 2026-09-16
([sleap-roots-pipeline#76](https://github.com/talmolab/sleap-roots-pipeline/issues/76),
workflow `srp-t74a-poison-gfzp6`): `Ingested 0/2 envelopes (2 failed)`, nothing
reached the RPC.

The trigger is a **recompute at an unchanged idempotency key**. `predict`'s `.slp`
output is not byte-reproducible — identical inputs, models and code SHAs yield the same
`idempotency_key` but different bytes at identical file sizes — while the object path
embeds that key, so recomputed bytes land at an address that is already occupied.
Nothing detects it: the failure surfaces only as write-back exit 1, and `pods/log` is
not granted to the `argo-user` ServiceAccount, so the cause is invisible from the
cluster.

## What Changes

- Add an idempotency-gate check to both commands, **before** any manifest read,
  checksum verification, or upload: if `cyl_trait_sources` already holds the envelope's
  `provenance.idempotency_key`, skip blob work entirely and go straight to the RPC.
- Keep calling the RPC unconditionally. Its `was_noop` branch performs the
  `cyl_pipeline_run_scans` status update, so short-circuiting the call would strand
  dispatched scans at `queued`.
- Degrade, never worsen: any failure of the check itself is treated as "not already
  ingested" and falls through to today's behaviour, so a bloomctl image that reaches the
  cluster ahead of the migration behaves exactly as it does now.
- Add one forward-only migration: `GRANT SELECT (idempotency_key) ON
  public.cyl_trait_sources TO bloom_workflows`, so the check uses the existing
  `cyl_trait_sources_idempotency_key_key` UNIQUE index.
- **Behaviour change beyond the bug:** an envelope whose key is already ingested but
  whose `{scan_key}.predictions.json` is missing is today reported `failed`; after this
  change it is reported `skipped`, because the skip precedes the manifest lookup. This
  is deliberate and touches one of bloom#859's inputs.
- Add the regression test that does not exist today: re-deliver an already-ingested
  envelope whose `.slp` bytes **differ**, and assert exit 0, `was_noop=true`, and that
  the stored bytes are unchanged.

Not included, filed separately: the orphan-blob wedge (bytes uploaded, pod died before
the RPC, recomputed later — no source row exists, so the check correctly says "not
ingested" and the collision still hard-fails); blob re-healing when a recorded
`s3_location` 404s; `predict` byte-determinism (different repo, and a GPU-cost event to
schedule deliberately).

## Impact

- Affected specs: `cyl-ingest-cli`, `cyl-batch-ingest-result`, `cyl-trait-writeback`
- Affected code: `bloomcli/src/bloomctl/cyl/ingest.py` (`ingest_one_envelope`,
  `ingest_result`), `bloomcli/tests/test_cyl_ingest.py`,
  `bloomcli/tests/test_cyl_ingest_integration.py`, one new `supabase/migrations/` file
- Privilege delta is near-zero: `SELECT (id, metadata)` on `cyl_trait_sources` is
  already granted to `bloom_workflows` with an RLS policy
  (`20260730120000_create_cyl_pipeline_runs.sql:168-172`), and `metadata` *is* the
  provenance blob, which already contains `idempotency_key`. The grant adds an indexed
  route to a value the role can already read, not new information.
- Deployment is not complete at merge: a new bloomctl image, a pin bump in
  `sleap-roots-pipeline`'s write-back **and** images-downloader templates, an
  `argo template update` in the `runai-busch-lab` namespace shared by staging and
  production, and a verified staging apply of the migration.
