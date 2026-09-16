## Context

Write-back delivers a per-scan result envelope to Bloom in two steps: upload the prediction
`.slp` artifacts to the `cyl-intermediates` bucket, then call `insert_cyl_result_envelope` with
the envelope plus the resulting `s3_location`s.

Three behaviours, each correct in isolation, combine into a failure:

1. `predict`'s `.slp` serialisation is not byte-reproducible. Two runs over the same scan,
   images, models, params and code SHAs produce the **same** `idempotency_key` and **different**
   bytes at identical file sizes (measured: `8776cdd8…` vs `e8535461…` for the same lateral
   artifact).
2. The object path embeds the key — `{scan_key}/{idempotency_key}/{kind}.{root_type}.slp`
   (`ingest.py:281-296`) — so recomputed bytes target an already-occupied address.
3. `upload_blob` refuses to overwrite an object whose checksum differs (`ingest.py:403-415`).
   This is right on its own: silently replacing bytes that other rows reference would be worse
   than failing.

The RPC already implements the lenient counterpart — `ON CONFLICT (idempotency_key) DO NOTHING`,
then an early return with `was_noop: true` that writes nothing to the trait or blob tables
(`20260912110000_add_cyl_writeback_run_scan_status.sql:149-194`). It is not a *total* no-op: on
that same branch, and only when `p_argo_workflow_name` is non-null, it also touches
`cyl_pipeline_run_scans` (`:179-186`) — though see the Decisions section, since that UPDATE can
only re-touch an already-written row. The defect is purely that the strict step is sequenced
ahead of the lenient one.

A pin bump cannot cause this: when the key changes, the address changes with it. The trigger is
specifically a recompute at an **unchanged** key, which requires predict's local artifacts to be
absent or unreadable while Bloom still holds blobs for that key. Demonstrated across three live
runs on 2026-09-16 — `srp-t76-zero-shared-hrrkz` (recomputed post-bump, new key, new address,
succeeded), `srp-t74a-poison-gfzp6` (recomputed from a fresh dir, same key, collided),
`srp-t77-redeliver-t82vr` (skipped, succeeded).

## Goals / Non-Goals

Goals:

- Re-delivering an already-ingested envelope is a benign no-op regardless of whether the
  producer regenerated its artifacts.
- No regression in the first-delivery path, no loss of any existing fail-fast guarantee, and no
  new failure mode if the migration and the bloomctl image land out of order.

Non-Goals:

- Making `predict` byte-deterministic. Different repo; it would change `predict_code_sha`, which
  is an input to `compute_idempotency_key`, invalidating every accumulated key and forcing one
  more full recompute cycle across the GPU cluster.
- Repairing blobs whose recorded `s3_location` no longer resolves.
- Rescuing orphaned blobs uploaded by a delivery that died before the RPC.

## Decisions

**Decision: check the gate before uploading, rather than tolerating the collision.**
A collision-tolerant upload would have to decide what to record when stored bytes and declared
bytes disagree, and on a genuine (non-no-op) delivery it would pair this envelope's traits with
another run's `.slp`. Issue #76 established only that the *bytes* differ, never that the
*predictions inside them* are identical — nobody has diffed the point arrays — so that pairing is
a provenance claim with no evidence behind it. Checking first avoids needing the claim at all.

**Decision: place the check immediately before `upload_pending_blobs`, not before the manifest
read.** This is the decision that keeps the change small. An earlier placement would bypass four
separate enforcement points that are each the sole implementation of a currently-specced
requirement — missing/malformed manifest, missing `.slp` on disk, the `slp_path` traversal guard
(`ingest.py:236-241`), and the conflicting pre-existing `blobs` entry check
(`ingest.py:227-233`) — and would additionally require hoisting `_authed_client(profile)` above
blob construction in `ingest_result`, inverting the deliberate discipline recorded at
`ingest.py:893-901` ("Blob construction is pure … so it stays before authentication"). At the
late position the client already exists (`ingest.py:928`), nothing reorders, and only the upload
and the `blobs` merge are skipped. Cost: on the skip path the manifest is read and blobs are
constructed for nothing. That is local disk I/O against a GPU pipeline — the right trade.

**Decision: read `cyl_trait_sources.idempotency_key` directly; no new RPC.**
`bloom_workflows` already holds `SELECT (id, metadata)` plus the permissive
`workflows_read_cyl_trait_sources` policy on that table
(`20260730120000_create_cyl_pipeline_runs.sql:167-169`, `:172`), and the RPC stores
`metadata = prov` where `v_idem := prov ->> 'idempotency_key'`. The key is therefore already
readable via `metadata->>'idempotency_key'` with zero migration — only unindexed. One column
grant converts that into a lookup on the existing `cyl_trait_sources_idempotency_key_key` UNIQUE
index. Postgres requires `SELECT` on every referenced column including those in `WHERE`, so the
query needs exactly `SELECT(id)` (held) plus `SELECT(idempotency_key)` (the new grant); column
grants are additive via `pg_attribute.attacl`, so the existing grant is untouched.

Alternatives considered:

- *A `SECURITY DEFINER` probe function.* More conservative on privilege in principle, but it
  exposes no value the role cannot already read, adds a function and a `PGRST202`
  deploy-ordering window, and is more surface than the problem warrants.
- *No migration at all, filtering on `metadata->>'idempotency_key'`.* Works today, but
  sequentially scans `cyl_trait_sources` once per envelope.
- *Content-addressed object paths.* Collisions become impossible, and #76's "orphans existing
  blobs" objection is overstated — `blob_object_path` has one production caller (`ingest.py:476`,
  plus three direct unit tests), and no code in this repo reads `cyl_scan_intermediates` blobs at
  all today, so existing rows keep resolving. The real cost is different: on a re-delivery the
  RPC discards the blobs array, so the freshly uploaded object ends up referenced by nothing. It
  leaks an orphan on every recompute.

**Decision: still call the RPC on the skip path.** The gate's read is non-transactional and
advisory; the RPC is the only component that can detect a same-key-different-scan delivery and
the only authority on whether anything is written.

An earlier draft justified this differently — that the `was_noop` branch rescues a
`cyl_pipeline_run_scans` row from `queued`. That is structurally impossible and the draft was
wrong: `source_id` is written only by the non-no-op path (`20260912110000:289-296`), in the same
statement that sets `status = 'written'`, so `source_id IS NOT NULL` implies the row is already
written. The no-op branch's `source_id`-keyed UPDATE can only ever re-touch a row that needs no
rescue; the rows actually stranded at `queued` it cannot match. See the Risks entry on the
re-delivery exit code.

**Decision: the check fails open, and says so out loud.** Any error — `APIError` (which is how a
42501 column-permission denial arrives through PostgREST), `httpx.ConnectError`/`ReadTimeout`, or
a `KeyError`/`TypeError` on an unexpected body — is treated as "not already ingested", falling
through to the existing upload-then-RPC path. This keeps the migration a performance and
robustness improvement rather than a correctness dependency, which matters because the image and
the migration deploy independently; bloom#685 and bloom#780 are both instances of that ordering
going wrong.

Failing open **silently** is not acceptable, and this codebase already paid for that lesson:
`tests/unit/test_cyl_scan_videos_grants.py:1-11` records a column-grant/PostgREST mismatch whose
42501 was swallowed by `_record_video`, and "production came to hold zero rows against 84,748
stored videos". `upload_blob` itself re-raises any non-404 `StorageApiError` three hundred lines
away for exactly this reason (`ingest.py:311-316`). So the fallback emits a
`logger.warning` naming the likely-missing grant, and the batch path surfaces the degradation in
its per-scan result rather than only in a log — bloomctl configures no logging handler at all
(nothing in `bloomcli/src/bloomctl/` calls `basicConfig`/`addHandler`), so only `WARNING`+ reaches
`logging.lastResort`, and `pods/log` is not readable with the `argo-user` ServiceAccount anyway.

**Decision: no TOCTOU re-check after a collision.** Two concurrent deliveries at the same key
would have to read *different* predictions directories to produce divergent bytes. That is not
impossible — `srp-t74a-poison-gfzp6` was exactly a fresh-directory recompute — but the two would
have to overlap in time, and the loser's outcome is an unreferenced orphan object rather than
corruption: the RPC's own `ON CONFLICT` still serialises the source insert. Dropped as YAGNI on
the strength of that outcome, not on the claim that it cannot happen. Recorded here so review can
overturn it.

## Risks / Trade-offs

- **A re-delivery from a NEW pipeline run is still reported `failed`, not `skipped`.** This is
  the biggest remaining gap and it is not closed here. `ingest_one_envelope` checks
  `status_update_matched is False` *before* it checks `was_noop`, and the RPC's no-op branch
  keys its `cyl_pipeline_run_scans` UPDATE on `(argo_workflow_name, source_id)` — but a new
  workflow's dispatched rows have `source_id IS NULL`, so the UPDATE matches zero rows and the
  envelope is reported failed with `retriable=False`. Because `retriable=False` suppresses the
  non-zero exit, the batch prints `0/N … N failed` while the Argo Workflow reports **Succeeded**
  — quieter than the pre-fix behaviour, which at least went red. The scan then stays `queued`
  and end-of-batch reconciliation closes it `failed`, so `failed_count` counts a scan whose
  traits and blobs are complete.

  Not fixed in this change because the fix is not ours alone to make: `fix-cyl-pipeline-run-scan-status`
  legislates a non-zero exit on `status_update_matched=false`, this change legislates zero on
  `was_noop=true`, and **neither spec addresses the intersection**. Reordering the two checks
  here would silently override that sibling's requirement. The durable fix is RPC-side — fall
  back to a `scan_id`-scoped UPDATE within `argo_workflow_name` when the `source_id` join matches
  nothing — which belongs with the capability that owns the status contract. Filed as
  bloom#875; until then the spec claim of "exits zero" holds for the manual shape and for
  intra-workflow Argo retries, not for a re-run from a fresh pipeline run.

- **The orphan-blob wedge stays open.** A delivery that uploads and then dies before the RPC
  leaves bytes with no source row; a later recompute checks correctly ("not ingested") and still
  collides, permanently. bloomctl cannot distinguish an orphan from a referenced blob —
  `bloom_workflows` has neither a grant nor a policy on `cyl_scan_intermediates`. → Mitigation:
  the collision error names the recovery **and who can perform it**. Note the write-back identity
  cannot: `20260722000200_create_cyl_intermediates_bucket.sql` gives `bloom_workflows`
  SELECT/INSERT/UPDATE on that bucket and no DELETE, so the error must direct the operator to
  `bloom_admin`/`service_role` or Studio rather than implying a self-service fix. Filed as a
  follow-up, blocked on diffing the two `.slp` files' actual predictions.
- **One extra round-trip per envelope on the first-delivery path.** Negligible against the
  existing per-blob `bucket.download()`, which fetches whole objects to compare checksums where a
  HEAD would do — noted, not fixed here.
- **Wasted local work on the skip path.** The manifest is read and blobs constructed before the
  check discards them. Accepted deliberately; see the placement decision.
- **Widening a role a second service shares.** `bloom_workflows` also backs the cyl-scan video
  service, so any grant widens that credential's blast radius too — the tradeoff @blm3886
  accepted in PR #470. → Mitigation: the value is already reachable through the `metadata` column
  this role holds, so the grant adds an access path, not access.
- **Production runs the fix without the grant until promotion.** Merging to `staging` grants the
  column on staging only, while §8's Argo pin applies to a namespace shared with production.
  There, the check fails open and behaves as today — monotonic, but the bug is not fixed on
  production until a staging→main promotion lands and is verified.

## Migration Plan

One forward-only migration containing a single `GRANT SELECT (idempotency_key)`, plus its
`supabase/rollbacks/` partner (a 1:1 convention held by every migration since `20260730120000`).
No function is redefined, so there is no `PGRST202` window. It is not a schema grant, so
`tests/unit/test_schema_usage_grants.py::test_no_raw_schema_grants_in_migrations` — whose regex
is `\b(?:GRANT|REVOKE)\b[^;]*?\bON\s+SCHEMA\s+(?:auth|storage)\b` — does not match it;
`20260730120000_create_cyl_pipeline_runs.sql:172` is the precedent for a table-column grant
living in a migration, and `supabase/grants/schema_grants.sql` is reserved for schema-USAGE
grants only.

The timestamp must exceed `20260915120000`, already claimed by an in-flight sibling branch;
`scripts/lint_migrations.sh` compares against `origin/staging` and so cannot catch a collision
with an unmerged sibling.

Rollback is the paired `REVOKE SELECT (idempotency_key)` — scoped to that one column, never a
bare `REVOKE SELECT`, which would strip the pre-existing `(id, metadata)` grant the
dedup-preview route depends on. **The rollback is only safe because `source_already_ingested`
catches every exception:** after the revoke the check degrades to "not ingested" and the command
falls back to upload-then-RPC. A future cleanup narrowing that `except` to `APIError` would
silently convert the documented rollback from "degrade" into "every re-delivery raises", so the
dependency is recorded here and repeated in the rollback file's own header.

Deploy order is unconstrained, by construction. Post-merge the tail still runs: `tasks.md` §9.

## Open Questions

- Are the two non-reproducible `.slp` files the same *predictions* serialised differently, or
  genuinely different predictions? Not needed for this change, but it decides whether the
  orphan-wedge follow-up can be closed the easy way.
