## Context

This change extends `bloomctl cyl ingest-result` to actually populate
`cyl_scan_intermediates` blobs, and touches four things at once: a CLI command
(`bloomcli`), a cross-repo data contract (`sleap-roots-predict`'s
`PredictionManifest`), a new Supabase Storage bucket, and its RLS. Per
`openspec/AGENTS.md`'s design.md criteria (cross-cutting change, new data-model
surface, migration complexity), it warrants a design doc.

Five open questions from the original issue (#407) were resolved with the user
before scaffolding this proposal (recorded here so the reasoning survives, not
just the answer):

## Goals / Non-Goals

**Goals:**

- `cyl ingest-result --predictions-dir <dir>` produces a fully-populated
  `blobs` array (with real `s3_location` values) from predict's on-disk output,
  with no sleap-roots-side change required.
- Uploads are safe to retry (idempotent) and fail loudly on integrity mismatch
  rather than silently overwriting.
- The new bucket's RLS is consistent with the access-role precedent already
  established for `bloom_workflows` (the eventual pipeline identity) elsewhere
  in this repo.

**Non-Goals:**

- Box upload (`box_link`) — no existing client to build on; follow-up issue.
- Wiring this command into the Argo DAG as an actual pipeline step — a separate
  future change (tracked in the sleap-roots-pipeline A4 write-back row).
- Changing `insert_cyl_result_envelope` or `cyl_scan_intermediates` themselves —
  the RPC and table are unchanged; this only ever gets to the point of calling
  the RPC with `blobs` populated.

## Decisions

**1. BlobRef construction happens in `bloomctl`, reading predict's manifest
directly — not upstream in sleap-roots, not a separate command.**
`--predictions-dir` points at the same per-scan output directory Argo already
gives the trait-extractor step (`predictions-output-dir` hostPath, confirmed in
`sleap-roots-pipeline.yaml` / `sleap-roots-trait-extractor-template.yaml`),
which already contains `{scan_key}.predictions.json` plus the `.slp` files. This
keeps the change one-repo, one-PR, matches the issue's literal framing
("extend `bloomctl cyl ingest-result`"), and needs no coordinating PR in
sleap-roots. *Alternative considered:* populate `blobs` upstream in
`trait_extractor.envelope.build_envelope` — rejected because it crosses a repo
boundary for no benefit; `bloomctl` already has (or can be given) the same
directory access predict/traits do. *(Superseded in part — see Open Questions
below, now resolved: construction still happens in `bloomctl`, but it obtains
the manifest's shape by importing `PredictionArtifact`/`PredictionManifest`
from `sleap_roots_contracts` (promoted there in v0.1.0a5,
talmolab/sleap-roots-contracts#22/PR #23), not from `sleap-roots-predict` and
not via a coordinating change to `sleap-roots`'s `envelope.py`. That
distinction is why this decision still stands as originally framed.)*

**2. MinIO only via the existing Supabase Storage client; no boto3, no Box.**
`bloomcli` has zero storage client code today beyond `supabase-py`
(`bloomcli/pyproject.toml` — only dependency is `supabase>=2.0.0`).
`download.py:178-202` already proves the pattern
(`client.storage.from_(bucket).download(...)`, signed server-side, "no MinIO
secrets, no legacy Lambda"); `.upload(...)` on the same authenticated client is
the direct mirror. `services/video-worker` uses `boto3` directly against MinIO,
but that is a separate service with its own credential model — not reused here.
No Box client (`boxsdk`, `rclone`) exists anywhere in the monorepo, confirmed by
search — `box_link` is genuinely unbuilt, not just deferred by preference.

**3. Bucket name: `cyl-intermediates`.** Mirrors the `cyl_scan_intermediates`
table name it backs, and leaves room for future intermediate `kind`s beyond
`predictions_slp` (the schema's `kind` enum already anticipates more than one
value even though only one exists today) without renaming the bucket later.

**4. RLS mirrors the `bloom_workflows`/`videos`-bucket precedent, not the
`cyl_scan_intermediates` TABLE's post-lockdown precedent.** This is the
decision most likely to draw review pushback, so it's worth being explicit:
the `cyl-trait-writeback` capability's "Intermediates table role-based access
control" requirement locks the **table** to RPC-only writes (`bloom_writer`'s
direct `INSERT`/`UPDATE` were dropped in Change E — all table writes go through
`insert_cyl_result_envelope`'s `SECURITY DEFINER`). That precedent does **not**
apply here: there is no RPC-mediated path for Supabase Storage byte writes (no
`SECURITY DEFINER` equivalent wraps `storage.objects`), so the credential that
calls `ingest-result` must write bytes directly. This is exactly the shape
`bloom_workflows` already has for the `videos` bucket
(`20260716000000_create_workflows_role.sql`: `GRANT SELECT, INSERT, UPDATE ON
storage.objects TO bloom_workflows`, policies scoped by `bucket_id`) — so the
new bucket's RLS mirrors that pattern: `bloom_admin` `FOR ALL`;
`bloom_agent`/`bloom_user` `SELECT`-only; `bloom_writer` **and**
`bloom_workflows` get `SELECT`+`INSERT`+`UPDATE` (never `DELETE`). Both writer
roles are granted now — `bloom_workflows` mirrors the #470 precedent of
granting the pipeline identity ahead of its credential existing (bloom #398 CLI
auth / pipeline #17 credential provisioning / Argo login wiring are all still
unstarted and out of scope here; granting now avoids a second migration later).
One earlier bug in the `videos`-bucket rollout is instructive: `bloom_workflows`
initially got write access but not read access, and broke because upload-with-
upsert needs to read the object back
(`20260717000000_workflows_read_videos_policy.sql` fixed it). This change
includes `SELECT` for both writer roles from the start to avoid repeating that.
Note: `bloom_admin` (`admin_all_objects`, `FOR ALL USING(true)`),
`bloom_agent` (`agent_read_objects`, `FOR SELECT USING(true)`,
`20260506000001_bloom_role_rls_policies.sql`), **and `bloom_writer`**
(`writer_select_objects`/`writer_insert_objects`/`writer_update_objects`, all
`USING(true)`/`WITH CHECK(true)`, `20260519130000_add_bloom_writer_role.sql`)
already have blanket, bucket-agnostic `storage.objects` policies, so all three
already cover this new bucket with zero new policy needed. Only
`bloom_workflows` (whose existing `storage.objects` policies are bucket-scoped
— `images`/`videos` only, not blanket) and `bloom_user` (whose existing
policies are per-bucket `SELECT`-only, e.g. `user_read_images`,
`user_read_videos` — no blanket coverage and no `INSERT`/`UPDATE` for any
bucket) strictly require new `bucket_id = 'cyl-intermediates'`-scoped
policies. Writing explicit policies for all four roles anyway (as this change
does) is redundant but harmless for admin/agent/writer, and keeps the
migration self-documenting without relying on a reader already knowing about
the blanket policies.

**5. Object path key: `{scan_key}/{idempotency_key}/{kind}.{root_type}.slp`.**
`cyl_scan_intermediates`'s own uniqueness anchor is `(source_id, scan_id, kind,
root_type)` — but `source_id` is assigned by the RPC and unknown until it
responds, so it can't be part of a path computed *before* the RPC call.
`provenance.idempotency_key` is deterministic, known pre-RPC, and already the
system's idempotency anchor (`cyl-trait-writeback`'s "Trait source idempotency
anchor" requirement). Keying the object path on it means: re-ingesting the
*same* envelope naturally reuses the same object path (supports idempotent
skip-if-exists); a genuinely *different* run (different `idempotency_key` —
e.g. a different model/config produced a different `.slp`) gets its own object
instead of silently overwriting a prior run's bytes at the same
`scan_key`+`root_type`, mirroring the table's own "same artifact, different
run, both kept" behavior (`test_same_artifact_from_a_different_run_is_permitted`).
This is an object-storage key, not a filesystem path — it MUST be built with
plain string joins (`"/".join(...)` or an f-string), never `pathlib.Path`,
which would silently emit backslashes on this repo's Windows dev environment
and produce a key that doesn't match what a Linux CI/prod run would derive for
the same inputs.

**6. Pre-upload integrity check.** Recompute each `.slp`'s sha256 from disk and
compare to the manifest's declared `checksum` before uploading. A mismatch is a
hard error (no upload, no RPC call) — this project treats data integrity as a
first-class concern, and predict's manifest is an on-disk artifact that could
in principle drift from the bytes it describes (partial write, manual edit,
disk corruption).

**7. `--predictions-dir` is optional, not required.** Preserves today's "blobs
pass through unchanged" behavior for any envelope that doesn't use it — no
breaking change to the CLI's existing contract, and the existing "Envelope
carrying blobs" scenario keeps working exactly as before when the flag is
omitted. Making it mandatory would be premature until the Argo DAG wiring
(separate future change) actually always supplies it.

**8. A failed or mismatched blob aborts before the RPC call.** Unlike
`download.py`'s per-frame download (many independent frames across many scans,
best-effort with per-frame failure logging), a single `ingest-result` call is
one all-or-nothing write-back for one scan, and the RPC is a single-shot,
first-writer-wins call keyed by `idempotency_key`. Submitting a partially-
populated `blobs` array would lock in an incomplete write-back for that key
permanently (a later retry with the same `idempotency_key` would be a no-op,
not a chance to add the missing blob). So any blob upload failure or checksum
mismatch aborts the whole command before the RPC call; retry is cheap because
already-uploaded blobs are skipped (Decision 5).

**9. Conflicting pre-existing `blobs` entries fail fast, not merge silently.**
`envelope.blobs` is always `[]` today, so in practice this never triggers, but
if a future envelope already carries a `blobs` entry for the same
`(root_type, scan_key)` that `--predictions-dir` would also construct, silently
overwriting or duplicating it would hide a real data-integrity question
("which one is right?"). The command fails fast instead.

## Risks / Trade-offs

- **Predict's per-scan directory layout isn't finalized in the Argo DAG yet**
  (this change only defines the CLI's contract for reading it) — mitigated by
  validating against a real `sleap-roots-predict` output fixture, not just
  hand-built JSON, in the TDD plan (see `tasks.md`).
- **Two Storage-client code paths (download vs. upload) could drift** if not
  disciplined — mitigated by deliberately mirroring `download.py`'s per-file
  try/except + aggregate-result-dataclass shape rather than inventing a new
  shape.
- **Granting `bloom_workflows` storage access now, before it can authenticate,**
  means the grant sits unused until #398/#17/Argo-wiring land — acceptable
  because it's the same trade-off already made (and shipped) for the RPC
  EXECUTE grant in bloom #470.

## Migration Plan

- New migration is purely additive: a new bucket + new policies. No existing
  table, bucket, or row is touched.
- Rollback: drop the policies, then the bucket. `storage.objects.bucket_id`
  has a plain FK to `storage.buckets.id` with no cascade, so once the bucket
  holds even one real object (which this change's own integration test
  creates), a bare bucket `DELETE` fails with a foreign-key violation. The
  rollback script MUST either delete `storage.objects` rows for this bucket
  first (destructive — document that explicitly) or guard with an
  existence-check that raises rather than silently succeeding on a non-empty
  bucket. This is a real, not theoretical, constraint once the feature is
  live — do not assume "nothing's been uploaded yet" holds at rollback time.

## Open Questions

None — the five original decisions (blob-construction location, storage
target, RLS model, writer roles, bucket/path naming) plus the one that
surfaced during review are all resolved:

- **How does `bloomctl` read `PredictionArtifact`/`PredictionManifest`
  without a hard dependency on the PyPI-absent `sleap-roots-predict`?**
  Resolved: the model was promoted into `sleap-roots-contracts` v0.1.0a5
  (talmolab/sleap-roots-contracts#22, PR #23), mirroring the A3-params-oracle
  promotion (`resolve_params` moved from `sleap-roots-predict` into
  `sleap-roots-contracts` v0.1.0a4). `sleap-roots-predict` now consumes it
  from contracts instead of defining it locally
  (talmolab/sleap-roots-predict#30, PR #31). `bloomcli`'s
  `sleap-roots-contracts` floor is bumped to `>=0.1.0a5`
  (`bloomcli/pyproject.toml`, `uv.lock` regenerated), and
  `from sleap_roots_contracts import PredictionArtifact, PredictionManifest`
  is verified importable. Decision 1's framing ("BlobRef construction happens
  in `bloomctl`, reading predict's manifest directly") holds unchanged for
  *where* construction happens; only *how* bloomctl obtains the manifest's
  shape changed, per that decision's note above. `tasks.md` section 2 has been
  rewritten to import from `sleap_roots_contracts`.
