## 1. Red — the regression test that does not exist today

- [ ] 1.1 Add a unit test to `bloomcli/tests/test_cyl_ingest.py`: `ingest_one_envelope`
      with `--predictions-dir`, where the source key is already present and the local
      `.slp` bytes differ from the stored object. Assert exit/`ScanResult` is
      `"skipped"`, the RPC was called once, and the storage client saw **zero** upload
      calls. Run it and confirm it fails with the current
      `object already exists … refusing to overwrite` error — not a fixture error.
- [ ] 1.2 Same shape for the single-envelope `ingest_result` command path, which has the
      same upload-before-RPC ordering (`ingest.py:820-835`).
- [ ] 1.3 Add a batch test: one already-ingested envelope with divergent bytes alongside
      one genuinely new envelope. Assert `skipped` + `ok`, exit zero, and that the new
      envelope's blobs were still uploaded.
- [ ] 1.4 Add a test for the missing-manifest-on-an-already-ingested-scan case: today it
      reports `failed`, after the change it reports `skipped`. Written now so the
      behaviour change is deliberate and visible in review, not incidental.

## 2. Green — the check

- [ ] 2.1 Add `source_already_ingested(client, idempotency_key) -> bool` to
      `bloomcli/src/bloomctl/cyl/ingest.py`: one
      `select("id").eq("idempotency_key", …).limit(1)`. Catch every exception and return
      `False` (fail open), per design.md.
- [ ] 2.2 Call it in `ingest_one_envelope` immediately after the `idempotency_key` is
      read and validated, and before `load_predictions_manifest`. On `True`, leave
      `pending` empty and do not merge blobs into the envelope.
- [ ] 2.3 Do the same in `ingest_result`.
- [ ] 2.4 Run tasks 1.1–1.4; confirm green.

## 3. Verify the test actually guards the behaviour

- [ ] 3.1 Mutant check: stub `source_already_ingested` to always return `False` and
      confirm 1.1 and 1.3 go red. A test that passes under that mutant is not testing
      the ordering — this is the failure mode that got through review in
      sleap-roots-pipeline#60.
- [ ] 3.2 Mutant check: make the check return `True` unconditionally and confirm a
      first-delivery test goes red (blobs must still upload for a new envelope).
- [ ] 3.3 Confirm the fail-open test (1.x) fails if the `except` is narrowed to only
      `APIError` — a bare permission error must not escape.

## 4. Migration

- [ ] 4.1 Add `supabase/migrations/<ts>_grant_workflows_read_cyl_trait_source_idem.sql`
      with a single `GRANT SELECT (idempotency_key) ON public.cyl_trait_sources TO
      bloom_workflows;` and a header recording why this does not widen the
      least-privilege posture `20260720000000` documents.
- [ ] 4.2 Confirm the `database-role-grants` CI guard still passes (it targets
      `ON SCHEMA (auth|storage)`; this is a table-column grant, precedent at
      `20260730120000:171-172`).
- [ ] 4.3 Run `bash scripts/lint_migrations.sh`. It shallow-fetches the repo — run
      `git fetch --unshallow origin` afterwards before any rebase.

## 5. Integration test against a real DB

- [ ] 5.1 Add an integration test beside
      `test_ingest_uploads_blobs_idempotently_and_rejects_checksum_mismatch` that
      re-delivers with **different** bytes at the same key. Assert exit zero,
      `was_noop=true`, and — the load-bearing assertion — that downloading the object
      still returns run A's bytes.
- [ ] 5.2 Correct the stale comment at
      `test_cyl_ingest_integration.py:156-158`, which asserts the upload step "would
      skip re-uploading … even if the RPC weren't a no-op". That is true only for
      identical bytes and is precisely the blind spot that hid this bug.
- [ ] 5.3 Grep for the same claim elsewhere before considering 5.2 done — correct the
      claim, not the file.

## 6. Documentation and follow-ups

- [ ] 6.1 Update `ingest_one_envelope`'s and `ingest-result`'s docstrings so the stated
      ordering matches the code.
- [ ] 6.2 Extend the collision error text to name the manual recovery (delete the
      conflicting object), since that path remains reachable for orphaned blobs.
- [ ] 6.3 File the orphan-blob-wedge issue on `bloom`, noting it is blocked on comparing
      the two `.slp` files' actual predictions, and cross-reference
      sleap-roots-pipeline#76.
- [ ] 6.4 File the blob-re-healing issue (recorded `s3_location` that 404s), referencing
      the known staging storage gap.
- [ ] 6.5 Check the interaction with bloom#859 (a manifest `scan_key` with no result is
      reported as a retriable batch failure) — task 1.4 changes one of its inputs.
      Record the finding on #859 rather than changing its behaviour here.

## 7. Pre-merge

- [ ] 7.1 `openspec validate fix-cyl-redelivery-blob-collision --strict`.
- [ ] 7.2 `/run-ci-locally` (lint + the full `bloomcli` suite).
- [ ] 7.3 `/review-pr` before requesting review.
- [ ] 7.4 Open the PR against `staging`, not `main`.

## 8. Post-merge — the deployment tail (NOT done at merge)

- [ ] 8.1 Verify the migration actually applied on staging by querying
      `information_schema.column_privileges`. Do not rely on the merge: CI's database is
      always empty, which is the root cause behind bloom#780's recurring
      auto-close-before-verify pattern.
- [ ] 8.2 Build and publish the bloomctl image; record the tag.
- [ ] 8.3 Run `bash scripts/check_cluster_drift.sh` in `sleap-roots-pipeline` and record
      the before state.
- [ ] 8.4 Bump the image pin in `sleap-roots-write-back-template.yaml` **and**
      `sleap-roots-images-downloader-template.yaml`.
- [ ] 8.5 `argo template update` in `runai-busch-lab` — shared by staging **and**
      production; confirm the blast radius is understood before applying.
- [ ] 8.6 Re-run `check_cluster_drift.sh` and confirm it reports in sync.
- [ ] 8.7 Reproduce the original failure scenario against staging and confirm it now
      exits zero. Only then close sleap-roots-pipeline#76.
- [ ] 8.8 Do not edit `sleap-roots-pipeline`'s `docs/bloom-integration/roadmap.md` or
      `openspec/changes/add-partial-success-exit-gate/tasks.md` while its PR #75 is
      open — keep the write-up in salk-bloom until that merges.
