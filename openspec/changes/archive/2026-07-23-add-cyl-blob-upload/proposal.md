## Status: unblocked (2026-07-22)

This proposal originally assumed `bloomctl` could read predict's per-scan
manifest (`PredictionArtifact`/`PredictionManifest`) directly — but that model
lived only in `sleap-roots-predict`, which isn't published to PyPI, so review
paused this change pending a prerequisite. That prerequisite has landed:
`PredictionArtifact`/`PredictionManifest` were promoted into
`sleap-roots-contracts` v0.1.0a5 (talmolab/sleap-roots-contracts#22, PR #23,
published to PyPI), and `sleap-roots-predict` now consumes them from there
(talmolab/sleap-roots-predict#30, PR #31). `bloomcli`'s
`sleap-roots-contracts` floor has been bumped to `>=0.1.0a5` and `uv.lock`
regenerated; the import (`from sleap_roots_contracts import
PredictionArtifact, PredictionManifest`) is verified working. `tasks.md`
section 2 and `design.md` Decision 1 below have been rewritten accordingly.

## Why

`bloomctl cyl ingest-result` (bloom #397/#408) forwards the envelope's `blobs` array
unchanged and never uploads bytes — by design, per the `cyl-ingest-cli` spec's
"Blobs pass through without upload" requirement, which explicitly calls blob-byte
upload a tracked follow-up. That follow-up is real: sleap-roots's trait extractor
always emits `blobs: []` (confirmed at `origin/main` `a98918d`,
`trait_extractor/envelope.py` `build_envelope`, unchanged since the file was added).
So today, even though `cyl_scan_intermediates` (Change C), the write-back RPC, and
its RLS lockdown (Change D/E) are all live in `staging`, no `.slp` byte or
`s3_location` value has ever actually reached them — the write-back path for
intermediates is wired end-to-end but structurally empty. Closes bloom #407.

## What Changes

- `cyl ingest-result` gains an optional `--predictions-dir <dir>` argument. When
  given, it reads that scan's `{scan_key}.predictions.json`
  (`PredictionManifest`, from `sleap_roots_contracts`, v0.1.0a5+) and, for each
  `PredictionArtifact`, constructs a `BlobRef` (`kind` already defaults to
  `"predictions_slp"` on the artifact, `root_type`, `scan_key`, `checksum`,
  `file_size` — all already computed by predict), verifies the on-disk `.slp`'s
  sha256 against the manifest's declared
  checksum, uploads the bytes to a new storage bucket via the existing
  authenticated Supabase Storage client, and populates `s3_location` — before
  merging the result into `envelope.blobs` and calling `insert_cyl_result_envelope`.
  When `--predictions-dir` is omitted, behavior is unchanged (blobs pass through
  as-is, no upload attempted).
- Upload is idempotent: re-running the same envelope+predictions-dir skips any
  blob whose target object already exists with a matching checksum, so a retry
  after a partial failure (upload succeeded, RPC call failed for an unrelated
  reason) never re-uploads bytes already in place.
- A failed blob upload or checksum mismatch aborts before the RPC call — the
  command never submits a partial `blobs` array for a single-shot RPC call.
- New Supabase migration creates a `cyl-intermediates` storage bucket with
  per-role RLS: `bloom_admin` `FOR ALL`; `bloom_agent`/`bloom_user` `SELECT`-only;
  `bloom_writer` and `bloom_workflows` `SELECT`+`INSERT`+`UPDATE` (no `DELETE`),
  scoped to `bucket_id = 'cyl-intermediates'` — mirroring the existing
  `bloom_workflows`/`videos`-bucket precedent
  (`20260716000000_create_workflows_role.sql`), not the legacy blanket-
  `authenticated` `images`-bucket pattern.
- `box_link` is out of scope for this change — no Box API client (`boxsdk`,
  `rclone` wrapper) exists anywhere in the monorepo today. Deferred to a
  follow-up issue.

## Impact

- Affected specs: `cyl-ingest-cli` (MODIFIED + ADDED), `cyl-trait-writeback` (ADDED)
- Affected code: `bloomcli/src/bloomctl/cyl/ingest.py`;
  `bloomcli/pyproject.toml` (`sleap-roots-contracts` floor bumped to
  `>=0.1.0a5` — done, see Status above; `bloomcli/uv.lock` is regenerated
  locally too, but it's gitignored — `bloomcli` isn't in
  `scripts/check-uv-locks.py`'s tracked-service list, so its lockfile has no
  git history and won't appear in the PR diff); `contracts/pin.json`,
  `generated/result-envelope.ts`, `contracts/README.md` (re-pin to v0.1.0a5 —
  see tasks.md section 0); new
  `supabase/migrations/<ts>_create_cyl_intermediates_bucket.sql` +
  matching `supabase/rollbacks/` script; `bloomcli/tests/test_cyl_ingest.py`,
  `test_cyl_ingest_integration.py`, new
  `tests/integration/test_cyl_intermediates_bucket.py`;
  `bloomcli/README.md`, `_WIKI/SUPABASE/README.md`, `bloomcli/CHANGELOG.md`
- Not touched: `bloomcli/src/bloomctl/cyl/download.py` (pattern reference only)
- Out of scope: Box upload, wiring this into the Argo DAG (separate future
  change, tracked by the sleap-roots-pipeline A4 write-back row), bloom #398
  (non-interactive auth), bloom #404's queue-tables remainder (active,
  unresolved thread — not touched here)
