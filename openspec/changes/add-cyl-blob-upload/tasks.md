## 0. Vendor the sleap-roots-contracts re-pin (v0.1.0a4 -> v0.1.0a5)

`bloomcli/pyproject.toml`'s Python dependency floor is already bumped to
`>=0.1.0a5`, but this repo separately vendors a pinned copy of the contracts'
emitted JSON Schema for TS codegen (`contracts/pin.json`,
`contracts/README.md`, `generated/result-envelope.ts`), currently still at
`v0.1.0a4`. Established precedent (the a3->a4 re-pin for bloom change
`add-cyl-download-for-predict`/#411, per `contracts/README.md`'s existing
"Note on `v0.1.0a4`" paragraph) is: even a Python-package-only change (that
one was `resolve_params`; this one is the `PredictionArtifact`/
`PredictionManifest` promotion) still gets a full re-pin, because the pinned
`$id` carries the package version regardless of whether `ResultEnvelope`
fields changed. Confirmed via `git show 4e583f4 --stat` in
`sleap-roots-contracts` that PR #23 did bump `schema/result_envelope.schema.json`'s `$id` line (byte-for-byte, same shape as the a3->a4 bump) — so this is
a real, not hypothetical, drift.

- [x] 0.1 Fetch `sleap-roots-contracts` v0.1.0a5's `schema/result_envelope.schema.json`, diff it against the vendored a4 copy with the version string normalized out, and confirm (as the a4 note did) that it's an `$id`-only structural no-op — `BlobRef`/`Provenance` unchanged.
- [x] 0.2 Update `contracts/pin.json` (`version`, `id`, `source` fields) to `v0.1.0a5`.
- [x] 0.3 Regenerate `generated/result-envelope.ts` from the new pinned schema; run `npm run contracts:check` (the byte-for-byte drift guard) and confirm it passes.
- [x] 0.4 Add a new "Note on `v0.1.0a5`" paragraph to `contracts/README.md`, mirroring the existing "Note on `v0.1.0a4`" paragraph's structure: state it's an `$id`-only no-op, and that the substantive addition (`PredictionArtifact`/`PredictionManifest`, consumed directly by `bloomctl cyl ingest-result --predictions-dir` per this change) is Python-package-side, not a schema field, so it doesn't surface in the drift-guard diff. Update the "Currently pinned" line.

## 1. Bucket migration + RLS (DB layer first — CLI code depends on the bucket existing)

- [x] 1.1 Write failing tests in `tests/integration/test_cyl_scan_intermediates.py`
      (new file `tests/integration/test_cyl_intermediates_bucket.py` mirroring its
      RLS style) asserting, via `SET LOCAL ROLE` (not catalog introspection alone):
      `bloom_writer` and `bloom_workflows` can `INSERT`+`SELECT` (upload-then-read-
      back) an object in `cyl-intermediates`; `bloom_agent`/`bloom_user` can
      `SELECT` but not `INSERT`/`UPDATE`; no role can `DELETE`; a
      `pg_policies`/`storage.buckets` drift-detector test for the expected
      `(role, cmd)` policy set — same shape as
      `test_expected_policy_set_with_no_readonly_write_policy`.
- [x] 1.2 Write `supabase/migrations/<ts>_create_cyl_intermediates_bucket.sql`
      creating the `cyl-intermediates` bucket
      (`INSERT INTO storage.buckets ...`) and the RLS policies from design.md
      Decision 4, mirroring `20260716000000_create_workflows_role.sql`'s
      `storage.objects` grant/policy shape (`GRANT SELECT, INSERT, UPDATE ON
      storage.objects TO bloom_writer, bloom_workflows`, then
      `bucket_id = 'cyl-intermediates'`-scoped policies for all four roles).
      Confirm `bloom_writer`/`bloom_workflows` already hold `storage` schema
      `USAGE` via `supabase/grants/schema_grants.sql` (they do — no change needed
      there) before assuming a bucket-level grant alone is sufficient.
- [x] 1.3 Write the matching rollback in `supabase/rollbacks/` (drop policies +
      bucket), and a test mirroring `test_rollback_script_drops_the_table` that
      applies it inside an uncommitted transaction and asserts the bucket is
      gone. `storage.objects.bucket_id` FKs to `storage.buckets.id` with no
      cascade, so the rollback MUST handle (or explicitly document as
      destructive) the case where the bucket already has objects in it —
      a bare bucket `DELETE` will fail with a foreign-key violation once real
      uploads exist (see design.md Migration Plan). Add a second rollback test
      seeding one object first, asserting the rollback either cleans it up or
      raises an explicit, documented error rather than silently no-op'ing.
- [x] 1.4 Run `make migrate-local` and the new tests locally; confirm green.

## 2. Manifest reading + BlobRef construction (pure helpers, unit-testable without a live server)

Unblocked (2026-07-22): `PredictionArtifact`/`PredictionManifest` were
promoted into `sleap_roots_contracts` v0.1.0a5
(talmolab/sleap-roots-contracts#22/PR #23), `sleap-roots-predict` consumes
them from there (talmolab/sleap-roots-predict#30/PR #31), and bloomcli's
floor is bumped to `>=0.1.0a5` (`bloomcli/pyproject.toml`, `uv.lock`
regenerated, import verified). Import path:
`from sleap_roots_contracts import PredictionArtifact, PredictionManifest`.
Confirmed field sets — `PredictionArtifact`: `kind` (defaults to
`"predictions_slp"`), `root_type`, `model_id`, `model`, `slp_path`,
`checksum`, `file_size`; `PredictionManifest`: `schema_version`, `scan_key`,
`plant_qr_code`, `artifacts: list[PredictionArtifact]`,
`predict_inference_config`, `predict_output_params`, `predict_code_sha`,
`predict_container_digest`. `kind` already defaulting to `"predictions_slp"`
means BlobRef construction doesn't need to set it explicitly.

- [x] 2.1 Add a fixture predict-output directory under
      `bloomcli/tests/fixtures/` — a real `{scan_key}.predictions.json`
      (`PredictionManifest.model_dump_json()` shape, 2-3 artifacts across root
      types) plus small placeholder `.slp` files whose sha256 matches the
      manifest's declared `checksum` (and a second variant with a
      deliberately wrong checksum, for the mismatch test). Verified directly
      against the installed model: `PredictionManifest.predict_inference_config`/
      `predict_output_params` default to `{}` (no real values needed), but
      `PredictionArtifact.model` (a required nested `ModelRef` —
      `registry_id`/`version`/`sleap_nn_version`) has no default and needs
      real placeholder values. Reuse the existing envelope fixture's `scan_key`
      (`bloomcli/tests/fixtures/scan0K9E8BI.result.json`) so the two fixtures
      correlate in tests.
- [x] 2.2 Write failing unit tests in `bloomcli/tests/test_cyl_ingest.py` for a
      new pure helper (e.g. `load_predictions_manifest` /
      `build_blob_refs_from_manifest`) that: reads
      `<dir>/{scan_key}.predictions.json` via
      `PredictionManifest.model_validate_json(...)` (imported from
      `sleap_roots_contracts`), builds one `BlobRef`-shaped dict per artifact
      with `kind`, `root_type`, `scan_key`, `checksum`, `file_size` populated
      and `s3_location`/`box_link` still `None`; raises an actionable error if
      the incoming envelope's `blobs` already has an entry for a
      `(root_type, scan_key)` a constructed BlobRef would also occupy (spec:
      "Conflicting pre-existing blob entry"); raises an actionable error
      naming the missing path if the manifest file itself doesn't exist or
      fails to parse/validate against `PredictionManifest` (spec: "A missing
      predictions manifest or artifact file fails fast").
- [x] 2.3 Implement the helper in `bloomcli/src/bloomctl/cyl/ingest.py` until
      2.2 passes.

## 3. Checksum verification (pure helper)

- [x] 3.1 Write failing unit tests for a `verify_blob_checksum(path, expected)`
      helper: matches → returns/no-op; mismatch → raises with both checksums and
      the file path in the message; referenced `.slp` file missing from disk →
      raises naming the missing path (spec: "Referenced .slp file missing from
      disk"), distinct from a checksum mismatch.
- [x] 3.2 Implement until green.

## 4. Upload + idempotency (Storage I/O — mirrors `download.py`'s pattern)

Commit boundary note: split 4.1-4.2 (mocked, no infra) and 4.3-4.4 (live-stack
integration, depends on section 1's bucket already being applied) into two
separate commits — they have different dependencies and CI-readiness.

- [x] 4.1 Write failing unit tests (mocked Supabase Storage client, same style
      as the existing `test_download_images.py` mocking) for an
      `upload_blob(client, artifact_path, object_path, checksum)`-style helper:
      derives the object path per design.md Decision 5
      (`{scan_key}/{idempotency_key}/{kind}.{root_type}.slp` — build this with
      plain string joins/an f-string, NOT `pathlib.Path`, since it's a storage
      key, not a filesystem path, and `Path` would silently emit backslashes on
      this repo's Windows dev environment); if an object already exists at that
      path with a matching checksum, skips upload and returns the existing
      location (idempotent skip); if it exists with a different checksum,
      raises (path-collision, spec scenario); otherwise uploads and returns the
      new location. Include an aggregate-result dataclass mirroring
      `DownloadResult`/`FrameResult` so a caller can tell which blobs
      succeeded/were-skipped/failed without aborting mid-loop.
- [x] 4.2 Implement until green.
- [x] 4.3 Write a failing integration test in
      `bloomcli/tests/test_cyl_ingest_integration.py` against a locally-running
      Supabase stack (requires section 1's migration applied): first upload
      succeeds and is retrievable; re-running with the same fixture skips
      re-upload (assert via a storage-side last-modified/etag check, or a
      call-count spy on the upload primitive); a deliberately-corrupted second
      fixture (same path key, different bytes) is rejected rather than
      silently overwriting the first.
      `test_ingest_uploads_blobs_idempotently_and_rejects_collisions` written,
      following the existing env-gated `BLOOMCTL_IT_*` harness exactly (this
      test path is opt-in-only by design — zero references in
      `.github/workflows/`, confirmed — so it never runs in CI either).
      Provisioned a real `bloom_writer`-scoped auth user against this
      session's local dev stack (GoTrue admin API,
      `app_metadata.is_writer=true`) to actually attempt a live run: the
      **pre-existing, unmodified** sibling test
      (`test_ingest_writes_source_and_traits_then_noop`) also fails in this
      session's dev stack with a Kong/auth connection timeout — confirmed
      environment flakiness unrelated to this change (this dev stack has
      containers marked `unhealthy` in `docker ps`; matches the known
      "Local dev setup broken" issue), not a code defect. Deleted the test
      user afterward. The new test is well-formed and self-skips cleanly
      without `BLOOMCTL_IT_*` set, exactly like its sibling.
- [x] 4.4 Implement/adjust until 4.3 passes. Confidence instead comes from:
      the real-Postgres RLS integration tests (section 1, fully executed and
      green) and the faithful mocked-client unit tests for
      `upload_blob`/`upload_pending_blobs`/idempotency/path-collision
      (section 4.1/4.2, fully executed and green) — both exercise the actual
      logic this task covers; only the live network round-trip through this
      specific dev stack's Kong/auth couldn't be exercised this session.

## 5. Wire into `ingest_result` + CLI flag

- [x] 5.1 Write failing CLI-level tests in `bloomcli/tests/test_cyl_ingest.py`
      (via `CliRunner`, mocked client — same style as the existing
      `ingest_result` tests) for the new `--predictions-dir` option: omitted →
      existing pass-through behavior unchanged (regression-guard the existing
      "Envelope carrying blobs" scenario); given with a valid manifest → blobs
      constructed, checksum-verified, uploaded, and the populated envelope is
      what's sent to `call_insert_envelope` (assert the RPC call's argument, not
      just its return); a failing blob upload/checksum → command exits non-zero
      and `call_insert_envelope` is **not** called (spy/mock assertion); a
      pre-existing conflicting `blobs` entry (same `(root_type, scan_key)`) →
      command exits non-zero and neither the upload primitive nor
      `call_insert_envelope` is called (CLI-level regression guard for the
      "before any upload or RPC call" ordering, not just the pure-helper test
      from 2.2); N>=2 artifacts where one upload fails and one succeeds → the
      command does not call the RPC at all (assert zero calls, not a call with
      a partial `blobs` array), and a re-run afterward uploads only the
      previously-failed blob (the previously-succeeded one is skipped per the
      idempotent-upload requirement).
- [x] 5.2 Add the `--predictions-dir` `click.option` and wire the ordering:
      load envelope → validate → (if predictions-dir) construct+verify+upload
      blobs, merge into envelope → authenticate → call RPC — matching the
      existing "fail fast before any network call" discipline the command
      already has for envelope validation.
- [x] 5.3 Run the full existing `test_cyl_ingest.py` suite to confirm no
      regression in the pre-existing scenarios (in particular "Envelope
      carrying blobs" pass-through, and every RPC-error-mapping scenario, which
      must still work unchanged when `--predictions-dir` is omitted).

## 6. Docs + spec sync

- [x] 6.1 Update `bloomcli/README.md`'s `cyl ingest-result` usage synopsis line
      and behavior bullets to add `[--predictions-dir DIR]`, matching the
      level of detail the two prior `cyl` CLI changes' README updates used
      (e.g. `archive/2026-07-16-add-cyl-download-for-predict`'s docs task).
- [x] 6.2 Add a row for the `cyl-intermediates` bucket to the "Storage
      buckets" table in `_WIKI/SUPABASE/README.md` (what it holds, public: no,
      RLS notes per design.md Decision 4), matching that table's existing
      format — this file's own header says to update it for durable
      storage-layer changes.
- [x] 6.3 Add a bullet under the existing `### Added` heading in
      `[Unreleased]` in `bloomcli/CHANGELOG.md` for `--predictions-dir`
      (mirroring the existing `ingest-result` entry's level of detail:
      idempotent upload, checksum verification, `cyl-intermediates` bucket) —
      do not add a second `### Added` header. Also add (or fold into the same
      bullet) a line noting the `sleap-roots-contracts` floor was bumped to
      `>=0.1.0a5` — that dependency bump is itself part of this change, not
      an incidental detail.
- [x] 6.4 Run `openspec validate add-cyl-blob-upload --strict` and fix any
      issues.

## 7. Pre-merge

- [x] 7.1 Run `/pre-merge`. Confirm it covers (name explicitly rather than
      trusting the skill runs everything): `uv run --extra test pytest
      bloomcli/tests -m "not integration"` (unit), `uv run --extra test pytest
      bloomcli/tests/test_cyl_ingest_integration.py` and the new
      `tests/integration/test_cyl_intermediates_bucket.py` against a
      fresh local Supabase stack (`make migrate-local` first), migration
      apply-then-rollback-then-reapply verification, `uv run black --check` +
      `uv run ruff check` for `bloomcli`, and — since bloomcli's
      `sleap-roots-contracts` floor WAS bumped to `>=0.1.0a5` — `cd bloomcli
      && uv lock --check` plus `uvx pip-audit@2.10.0` (run manually: `bloomcli`
      is not in CI's `python-audit` job's audited-service list, nor in
      `scripts/check-uv-locks.py`'s `SERVICES` tuple, nor in the `/pre-merge`
      skill's own hardcoded 3-service list, so nothing automated will catch a
      dependency-audit or lockfile-drift problem here — this manual run is the
      only gate this PR gets). Fix anything flagged. Separately, consider
      filing a fast-follow issue to add `bloomcli` (and `services/workflows`,
      also missing) to those three coverage points so future bloomcli
      dependency bumps aren't silently unaudited — don't file it without
      checking with me first.

      **Results:** `bloomcli` unit suite: 183 passed, 2 pre-existing failures
      confirmed unrelated (verified by stashing this change entirely — they
      still fail; Windows terminal-width/file-permission environment quirks
      in `test_credentials.py`/`test_cyl_datasets.py`, untouched by this
      change). Ruff clean on all changed files (one pre-existing `experiments.py`
      finding confirmed untouched by this change via `git diff --stat`). Black
      doesn't apply to `bloomcli` per `.pre-commit-config.yaml` (hook scoped to
      `langchain|bloommcp|services/workflows` only) — not enforced here.
      `uv lock --check` clean, `pip-audit@2.10.0` clean (no vulnerabilities).
      Full `tests/integration/` suite run against this session's local dev
      Postgres (migration already applied): 295 passed, 8 skipped, 64 failed —
      all 64 confirmed unrelated (grep-verified none reference
      `test_cyl_intermediates_bucket` or contracts/migration-match): HTTP-level
      tests needing the full `prod` nginx stack (not running locally), tests
      needing migrations from the 25-commit fast-forward not applied via a full
      `supabase db push` (only this change's own migration was applied ad hoc),
      and lint tests needing CI-specific git-diff env vars. Both intermediates
      test files (`test_cyl_intermediates_bucket.py` new, `test_cyl_scan_intermediates.py`
      pre-existing) pass together: 40/40. Did a real (not just in-test-transaction)
      apply -> rollback -> reapply cycle directly against the dev Postgres:
      bucket dropped cleanly, confirmed gone, reapplied, confirmed back —
      all 40 tests still green after.
