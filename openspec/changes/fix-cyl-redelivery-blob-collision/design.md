## Context

Write-back delivers a per-scan result envelope to Bloom in two steps: upload the
prediction `.slp` artifacts to the `cyl-intermediates` bucket, then call
`insert_cyl_result_envelope` with the envelope plus the resulting `s3_location`s.

Three behaviours, each correct in isolation, combine into a failure:

1. `predict`'s `.slp` serialisation is not byte-reproducible. Two runs over the same
   scan, images, models, params and code SHAs produce the **same** `idempotency_key`
   and **different** bytes at identical file sizes (measured: `8776cdd8…` vs
   `e8535461…` for the same lateral artifact).
2. The object path embeds the key —
   `{scan_key}/{idempotency_key}/{kind}.{root_type}.slp` (`ingest.py:274`) — so
   recomputed bytes target an already-occupied address.
3. `upload_blob` refuses to overwrite an object whose checksum differs
   (`ingest.py:320-324`). This is right on its own: silently replacing bytes that other
   rows reference would be worse than failing.

The RPC already implements the lenient counterpart — `ON CONFLICT (idempotency_key)
DO NOTHING`, then an early return with `was_noop: true` that writes nothing
(`20260912110000_add_cyl_writeback_run_scan_status.sql:139-193`). The defect is purely
that the strict step is sequenced ahead of the lenient one.

A pin bump cannot cause this: when the key changes, the address changes with it. The
trigger is specifically a recompute at an **unchanged** key, which requires predict's
local artifacts to be absent or unreadable while Bloom still holds blobs for that key.
Demonstrated across three live runs on 2026-09-16 — `srp-t76-zero-shared-hrrkz`
(recomputed post-bump, new key, new address, succeeded), `srp-t74a-poison-gfzp6`
(recomputed from a fresh dir, same key, collided), `srp-t77-redeliver-t82vr` (skipped,
succeeded).

## Goals / Non-Goals

Goals:

- Re-delivering an already-ingested envelope is a benign no-op regardless of whether the
  producer regenerated its artifacts — the guarantee `cyl-ingest-cli`'s "Re-ingest is a
  benign, distinctly-reported no-op" requirement already states.
- No regression in the first-delivery path, and no new failure mode if the migration and
  the bloomctl image land out of order.

Non-Goals:

- Making `predict` byte-deterministic. Different repo; it would change
  `predict_code_sha`, which is an input to `compute_idempotency_key`, invalidating every
  accumulated key and forcing one more full recompute cycle across the GPU cluster.
- Repairing blobs whose recorded `s3_location` no longer resolves.
- Rescuing orphaned blobs uploaded by a delivery that died before the RPC.

## Decisions

**Decision: check the gate before uploading, rather than tolerating the collision.**
A collision-tolerant upload would have to decide what to record when stored bytes and
declared bytes disagree, and on a genuine (non-no-op) delivery it would pair this
envelope's traits with another run's `.slp`. Issue #76 established only that the *bytes*
differ, never that the *predictions inside them* are identical — nobody has diffed the
point arrays — so that pairing is a provenance claim with no evidence behind it.
Checking first avoids needing the claim at all.

**Decision: read `cyl_trait_sources.idempotency_key` directly; no new RPC.**
`bloom_workflows` already holds `SELECT (id, metadata)` plus a permissive RLS policy on
that table (`20260730120000_create_cyl_pipeline_runs.sql:168-172`), and the RPC stores
`metadata = prov` where `v_idem := prov ->> 'idempotency_key'`. The key is therefore
already readable via `metadata->>'idempotency_key'` with zero migration — only
unindexed. One column grant converts that into a lookup on the existing
`cyl_trait_sources_idempotency_key_key` UNIQUE index.

Alternatives considered:

- *A `SECURITY DEFINER` probe function.* More conservative on privilege in principle,
  but it exposes no value the role cannot already read, adds a function and a `PGRST202`
  deploy-ordering window, and is more surface than the problem warrants.
- *No migration at all, filtering on `metadata->>'idempotency_key'`.* Works today, but
  sequentially scans `cyl_trait_sources` once per envelope.
- *Content-addressed object paths.* Collisions become impossible, and the "orphans
  existing blobs" objection in #76 is overstated — `blob_object_path` has exactly one
  caller and readers use the stored `s3_location`, so existing rows keep resolving. The
  real cost is different: on a re-delivery the RPC discards the blobs array, so the
  freshly uploaded object ends up referenced by nothing. It leaks an orphan on every
  recompute.

**Decision: still call the RPC on the skip path.** The `was_noop` branch updates
`cyl_pipeline_run_scans` to `'written'` by joining on `source_id`
(`20260912110000:179-186`). Skipping the call to save a round-trip would silently leave
dispatched scans at `queued` and desynchronise `done_count`.

**Decision: the check fails open.** Any error — `APIError`, permission denied on a
not-yet-granted column, a transient network fault — is treated as "not already
ingested", falling through to the existing upload-then-RPC path. This keeps the
migration a performance and robustness improvement rather than a correctness
dependency, which matters because the image and the migration deploy independently;
bloom#685 and bloom#780 are both instances of that ordering going wrong. The change is
monotonic: it either helps or it does nothing.

**Decision: no TOCTOU re-check after a collision.** Two concurrent deliveries at the
same key would have to read *different* predictions directories to produce divergent
bytes; an Argo retry reads the same directory and so produces matching checksums, which
`upload_blob` already skips. Dropped as YAGNI, recorded here so review can overturn it.

## Risks / Trade-offs

- **The orphan-blob wedge stays open.** A delivery that uploads and then dies before the
  RPC leaves bytes with no source row; a later recompute checks correctly ("not
  ingested") and still collides, permanently, until someone deletes the object by hand.
  bloomctl cannot distinguish an orphan from a referenced blob — `bloom_workflows` has
  neither a grant nor a policy on `cyl_scan_intermediates`. → Mitigation: the collision
  error names the manual recovery; filed as a follow-up issue, blocked on diffing the
  two `.slp` files' actual predictions.
- **One extra round-trip per envelope on the first-delivery path.** Negligible against
  the existing per-blob `bucket.download()`, which fetches whole objects to compare
  checksums where a HEAD would do — noted, not fixed here.
- **Widening a role a second service shares.** `bloom_workflows` also backs the cyl-scan
  video service, so any grant widens that credential's blast radius too — the tradeoff
  @blm3886 accepted in PR #470. → Mitigation: the value is already reachable through the
  `metadata` column this role holds, so the grant adds an access path, not access.

## Migration Plan

One forward-only migration containing a single `GRANT SELECT (idempotency_key)`. No
function is redefined, so there is no `PGRST202` window. It is not a schema grant, so
the `database-role-grants` CI guard (which targets
`GRANT/REVOKE … ON SCHEMA (auth|storage)`) does not apply;
`20260730120000_create_cyl_pipeline_runs.sql:171-172` is the precedent for a
table-column grant living in a migration.

Rollback is a `REVOKE`; because the code path fails open, revoking degrades performance
and restores today's behaviour rather than breaking anything.

Deploy order is unconstrained, by construction. Post-merge the tail still runs: build a
bloomctl image, bump the pin in `sleap-roots-write-back-template.yaml` *and*
`sleap-roots-images-downloader-template.yaml`, `argo template update` in
`runai-busch-lab` (shared by staging **and** production), and
`scripts/check_cluster_drift.sh` before and after.

## Open Questions

- Are the two non-reproducible `.slp` files the same *predictions* serialised
  differently, or genuinely different predictions? Not needed for this change, but it
  decides whether the orphan-wedge follow-up can be closed the easy way.
