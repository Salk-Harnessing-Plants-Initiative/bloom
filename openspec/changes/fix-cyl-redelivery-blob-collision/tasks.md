## 1. Red — tests first, against a fake that can actually observe the check

Written and run before section 2. Do **not** create a standalone red commit: no workflow in this
repo triggers on a feature-branch push and the squash erases intermediate trees, so an isolated
red commit proves nothing and risks leaving a failing tree at the branch tip. Capture the red
output to the scratchpad and paste it into the PR body instead.

- [x] 1.1 Replace the `object()` client in `bloomcli/tests/test_cyl_ingest.py`'s `_patch_authed`
      (`:311`) with a recording fake whose `cyl_trait_sources` lookup returns `[]` by default,
      keeping an `object()`-based variant for the fail-open test. Without this every existing
      `--predictions-dir` test silently exercises the fail-open branch (`object().table(...)`
      raises `AttributeError`, which the fallback swallows), and section 3's mutants become
      undetectable.
- [x] 1.2 Add a direct unit test for `source_already_ingested` against that recording fake.
      Assert the recorded call is exactly
      `table("cyl_trait_sources").select("id").eq("idempotency_key", <key>).limit(1)`, that a
      one-row response returns `True`, and — separately — that an **empty-list** response returns
      `False`. Mirror `bloomctl/cyl/datasets.py:222-228`'s `… .execute().data or []` idiom. This
      is the only assertion standing between a typo'd table or column name and a fail-open that
      silently restores the bug in production.
- [x] 1.3 `ingest_one_envelope` with `--predictions-dir`, key already present, local `.slp` bytes
      differing from the stored object. Assert `ScanResult.status == "skipped"`; that
      `upload_pending_blobs` was never called; that the storage bucket recorded zero `download()`
      and zero `upload()` calls; that `call_insert_envelope` was called exactly once; and that
      the envelope handed to the RPC carries its **original** `blobs` array, unmerged. Use the
      real `upload_pending_blobs` — monkeypatching it is what let this bug ship.
- [x] 1.4 Add a multi-path fake storage client that pre-seeds divergent bytes at **both** paths
      `blob_object_path` produces for the fixture (the existing `_ExistingBucket` at `:761` holds
      only one), records every `download()`/`upload()`, and raises `StorageApiError(status=404)`
      elsewhere. Run 1.3 against it and confirm it fails with
      `object already exists … refusing to overwrite` — not a fixture error.
- [x] 1.5 Same for the single-envelope `ingest_result` path, asserting the CLI surface: exit 0,
      `summarize_result`'s already-ingested line, and `--json` emitting `was_noop: true`.
- [x] 1.6 Assert the manifest **is** still read on the skip path, and that a missing manifest for
      an already-ingested envelope still fails — the check follows construction deliberately.
      This pins the placement decision; a mutant that moves the check earlier must go red here.
- [x] 1.7 Batch: one already-ingested envelope with divergent bytes plus one genuinely new
      envelope. Assert `skipped` + `ok`, exit zero, and that the new envelope's blobs were still
      uploaded.
- [x] 1.8 Fail-open test, parametrized over `postgrest.APIError` (42501 — the production
      trigger), `RuntimeError`, `AttributeError` and `ConnectionError` (a non-APIError transport
      fault, which is what justifies the broad `except`). Assert the command proceeds to upload exactly as
      without the check, is **not** reported failed on account of the check, **and** emits a
      warning naming the degraded check. (A 42501 arrives _as_ `APIError`, so an APIError-only
      test cannot justify the broad `except` — the transport cases are what justify it.)
- [x] 1.9 Assert `source_already_ingested` is never invoked when `provenance.idempotency_key` is
      empty or absent (existing empty-key tests still fail; recording fake shows zero queries),
      and never invoked when `--predictions-dir` is omitted.
- [x] 1.10 Batch mixed-failure case: envelope A's key present (skipped, no upload), envelope B's
      check raises (fails open, blobs uploaded, `ok`, warning on its result), envelope C absent
      key (`ok`). Assert exit zero and that B's blobs really were uploaded.

## 2. Green — the check

- [x] 2.1 Add `source_already_ingested(client, idempotency_key) -> bool` to
      `bloomcli/src/bloomctl/cyl/ingest.py`: one
      `select("id").eq("idempotency_key", …).limit(1)`, reading `.execute().data or []`. Catch
      every exception, emit
      `logger.warning("idempotency-gate check failed (%s); falling back to upload-then-RPC — the cyl_trait_sources.idempotency_key grant may be missing on this deployment", exc)`,
      and return `False`. `warning`, not `debug`/`info`: bloomctl configures no logging handler,
      so only WARNING+ reaches `logging.lastResort`.
- [x] 2.2 Guard the upload in `ingest_one_envelope`: insert the call immediately before
      `upload_pending_blobs` (`ingest.py:655`), inside the existing
      `if predictions_dir is not None:` block and after `build_pending_blobs`. On `True`, skip
      both the upload and the `data["blobs"]` merge at `:666`.
- [x] 2.3 Guard the upload in `ingest_result`: insert the call after
      `client = _authed_client(profile)` (`ingest.py:816`) and before `upload_pending_blobs`
      (`:819`), skipping the merge at `:828`. **Do not move `_authed_client`** — the late
      placement exists precisely so the construct-before-authenticate discipline recorded at
      `:787-791` stays intact.
- [x] 2.4 Surface the fail-open degradation on the batch path's per-scan result, not only in the
      log — `pods/log` is unreadable with the `argo-user` ServiceAccount.
- [x] 2.5 Run sections 1.1–1.10; confirm green.

## 3. Verify the tests actually guard the behaviour

Run these only **after** section 2 is committed, so `git checkout -- bloomcli/src/` in this
worktree alone is a one-command undo.

- [x] 3.1 Mutant: edit the source so `source_already_ingested` returns `False` unconditionally.
      1.3 and 1.7 must go red.
- [x] 3.2 Mutant: return `True` unconditionally. A first-delivery test must go red —
      `test_ingest_one_envelope_predictions_dir_uploads_blobs` asserts
      `len(env["blobs"]) == 2`, which drops to 0.
- [x] 3.3 Mutant: `client.table("cyl_trait_source")` (typo'd table) and, separately,
      `.eq("id", key)`. Both must go red on 1.2. Without 1.2 both are invisible in production.
- [x] 3.4 Mutant: `return res.data is not None` instead of `or []`. Must go red on 1.2's
      empty-list case — this mutant would otherwise make **every first delivery** a silent no-op
      that exits zero while writing nothing.
- [x] 3.5 Mutant: narrow the `except` to `postgrest.APIError`. 1.8's non-APIError cases must go
      red (a 42501 arrives _as_ `APIError`, so only the transport/programming-error cases can
      distinguish the two catches).
- [x] 3.6 Mutant: move the check above `load_predictions_manifest`. 1.6 must go red.
- [x] 3.7 Confirm `git diff --stat bloomcli/` is empty before continuing. Never `git add -A`
      while a mutant is in the tree.

## 4. Migration

- [x] 4.1 Add `supabase/migrations/<ts>_grant_workflows_read_cyl_trait_source_idem.sql` with a
      single `GRANT SELECT (idempotency_key) ON public.cyl_trait_sources TO bloom_workflows;`,
      plus `NOTIFY pgrst, 'reload schema';` (precedent: `20240904033106…:52`) — `deploy.yml`
      restarts caddy and kong by name but never the `rest` container, and this repo defines no
      `pgrst_ddl_watch` trigger, so the reload otherwise rests on the base image alone. Header
      records why this does not widen the least-privilege posture `20260720000000` documents.
- [x] 4.2 **Timestamp must exceed `20260915120000`**, already claimed by the in-flight
      `feat/cyl-trait-count-pgcron` branch. `scripts/lint_migrations.sh` compares against
      `origin/staging` only, so it cannot catch a collision with an unmerged sibling — re-check
      immediately before requesting review and again before merge.
- [x] 4.3 Add the paired
      `supabase/rollbacks/<ts>_grant_workflows_read_cyl_trait_source_idem_rollback.sql` with
      `REVOKE SELECT (idempotency_key) …` **only** — never a bare `REVOKE SELECT`, which would
      strip the pre-existing `(id, metadata)` grant. Header notes that the rollback's safety
      depends on `source_already_ingested`'s broad `except`. Every migration since
      `20260730120000` has a 1:1 rollback partner.
- [x] 4.4 Run `uv run --extra test pytest tests/unit/test_schema_usage_grants.py -v` — the real
      guard (regex `\b(?:GRANT|REVOKE)\b[^;]*?\bON\s+SCHEMA\s+(?:auth|storage)\b`), not a
      "`database-role-grants` CI guard", which is a spec, not a job.
- [x] 4.5 Run `./scripts/lint_migrations.sh origin/staging` — pass `origin/staging`, since that
      is the PR base and CI passes `origin/$GITHUB_BASE_REF`. No `--unshallow` step is needed;
      the script stopped shallow-fetching in #818.
- [x] 4.6 Add `tests/unit/test_cyl_trait_sources_grants.py` (static, over migration text,
      following `tests/unit/test_cyl_scan_videos_grants.py`): no migration grants
      `INSERT|UPDATE|DELETE|ALL` on `public.cyl_trait_sources` to `bloom_workflows`, and no grant
      on that table is column-less.

## 5. Integration tests

- [x] 5.1 Add a `tests/integration/` test for the grant, following
      `tests/integration/test_gravi_workflows_read.py`'s `SET LOCAL ROLE bloom_workflows`
      pattern: the filtered select succeeds for a seeded key and returns zero rows for an absent
      one; `SELECT name` raises 42501; `INSERT`/`UPDATE`/`DELETE` still raise. Write it red
      before 4.1. This runs in CI (`pr-checks.yml` `compose-health-check` applies migrations then
      runs `tests/integration/`), unlike section 5.2 — and it is the only thing that proves the
      grant works _as the role_ rather than merely existing as an ACL row.
- [x] 5.2 Do **not** assert an `EXPLAIN` plan for index usage: CI's table is empty and the
      planner will seq-scan regardless. Assert the query succeeds; keep the index as a design
      note.
- [x] 5.3 Add the divergent-bytes regression to `bloomcli/tests/test_cyl_ingest_integration.py`,
      reusing the existing `collision_dir` recipe (bytes changed _and_ checksums recomputed).
      Assert exit zero, `was_noop=true`, and — the load-bearing assertion — that downloading the
      object still returns run A's bytes.
- [x] 5.4 **Rewrite `test_ingest_rejects_a_genuine_storage_path_collision` (`:185-238`), which
      inverts under this change.** It currently ingests (writing the source row) and then asserts
      the re-delivery fails. Re-aim it at the orphan path, which is the only route to
      `upload_blob`'s collision branch after this change: monkeypatch `call_insert_envelope` to
      raise after `upload_pending_blobs` returns, so bytes land with no source row, then deliver
      `collision_dir`'s divergent bytes and assert exit != 0. Restate its docstring. `cleanup()`
      already deletes storage objects by the idem prefix, so teardown needs no change.
- [x] 5.5 **Restructure `test_ingest_uploads_blobs_idempotently_and_rejects_checksum_mismatch`.**
      The r3 (corrupt-fixture) leg moved onto a second, never-ingested envelope — via a second
      `envelope_for` call, since the contract model _derives_ the key and rejects a hand-edited
      one. The r2 (same-bytes re-delivery) leg was **left in place and is now vacuous**: the gate
      skips the upload, so `upload_blob`'s same-checksum skip is no longer exercised end to end.
      Unit coverage survives (`test_upload_blob_skips_when_existing_checksum_matches`). Recorded
      rather than silently dropped; restoring it is tracked as bloom#876.
- [x] 5.6 Delete the stale comment at `test_cyl_ingest_integration.py:150-153` claiming the
      upload step "would skip re-uploading … even if the RPC weren't a no-op" — true only for
      identical bytes, and exactly the blind spot that hid this bug.
- [x] 5.7 Grep the repo for that claim's siblings before considering 5.6 done — correct the
      claim, not the file.
- [x] 5.8 Recorded in the PR body: `bloomcli/tests/test_cyl_ingest_integration.py` is
      `-m "not integration"`-excluded and needs six `BLOOMCTL_IT_*` env vars, so 5.3-5.5 are
      **unexecuted**. Attempted locally and blocked: the compose dev stack has zero
      `auth.users` rows, no make target or seed script creates one, and the `dev1`/`dev2`
      profiles point at a Supabase-CLI instance on `127.0.0.1:54321` that is not this stack.
      The grant half (5.1) is covered by CI instead. The missing dev-user path is why this file
      has not run since 2026-07-22 — see 6.10.

## 6. Documentation

- [x] 6.1 `ingest_one_envelope` (`:593`) and `ingest_result` (`:772`) docstrings: state the real
      ordering.
- [x] 6.2 `upload_blob`'s docstring (`:293`): the parenthetical "a path collision between two
      different runs' bytes" becomes wrong — after this change the only reachable cause is bytes
      belonging to no ingested source.
- [x] 6.3 The comment at `:787-791` — confirm it still holds (it does, given 2.3) and leave it;
      note in the PR body that the late placement was chosen to preserve it.
- [x] 6.4 `--predictions-dir` help text at `:761-763` ("merges them into the envelope's `blobs`
      before ingesting") and `batch_ingest_result`'s at `:904`. Both user-facing.
- [x] 6.5 Collision error text: name the recovery **and who can perform it**.
      `20260722000200_create_cyl_intermediates_bucket.sql` gives `bloom_workflows`
      SELECT/INSERT/UPDATE and **no DELETE**, so "delete the object" is not self-service —
      direct the operator to `bloom_admin`/`service_role` or Studio. Extend the assertions at
      `test_cyl_ingest_integration.py:227` and the `upload_blob` unit test to pin the new
      wording.
- [x] 6.6 `bloomcli/README.md:510-519`: states both the invalidated ordering and an unconditional
      "fails fast — before any upload or RPC call — on … a checksum mismatch". `:506-507` and
      `:566-567` state the idempotency guarantee and become _more_ true — leave them.
- [x] 6.7 `_WIKI/SUPABASE/README.md:114`: update the enumerated column grants on
      `cyl_trait_sources`, which the spec delta now asserts as normative. Leaving it stale is
      exactly the two-files-one-claim failure this program has been bitten by.
- [x] 6.8 `bloomcli/CHANGELOG.md` `[Unreleased]` → `### Fixed`. `RELEASE_PROCESS.md:50-59` makes
      a changelog entry a hard release gate. Do **not** edit the released entry at `:397`, which
      accurately records `0.1.0a3` behaviour.
- [x] 6.9 Grep for any operator runbook covering a blob-collision failure. There is none today;
      note that in the follow-up issue rather than inventing one here.
- [x] 6.10 Filed bloom#877 — the dev-user gap: there is no supported way to
      obtain
      `BLOOMCTL_IT_EMAIL`/`PASSWORD` for the compose dev stack, so the env-gated bloomctl
      integration suite is unrunnable without hand-building an auth user. Four of the six vars
      are already a `~/.bloom/credentials.<name>.txt` profile; a `make bloomctl-it` target that
      reads a profile and seeds a writer would make the suite routinely runnable.

- [x] 6.11 Filed bloom#875 for the `was_noop` / `status_update_matched` intersection that
      makes a fresh-workflow re-delivery report `failed` while the Workflow goes green
      (design.md Risks). Needs a decision spanning this change and
      `fix-cyl-pipeline-run-scan-status`, so it is deliberately not fixed here.
- [x] 6.12 Filed bloom#876 for the lost end-to-end coverage of `upload_blob`'s same-checksum
      skip (see 5.5). The behaviour is unchanged and still reachable via the orphan path;
      `test_upload_blob_skips_when_existing_checksum_matches` still covers the function in
      isolation, so it is the chain that lost coverage, not the logic.

## 7. Follow-up issues — needs explicit authorization before posting

- [x] 7.1 Ask for authorization before any GitHub write. Filing issues and commenting on #859
      are all posts.
- [x] 7.2 Filed bloom#868 — the orphan-blob-wedge issue: bytes with no source row, permanently wedged, and
      recovery needs an identity write-back does not have. Blocked on diffing the two `.slp`
      files' actual predictions. Cross-reference sleap-roots-pipeline#76.
- [x] 7.3 Filed bloom#869 — the blob-re-healing issue (a recorded `s3_location` that 404s), referencing the
      known staging storage gap.
- [x] 7.4 Filed bloom#870 — the `upload_blob`-downloads-whole-objects-to-compare-checksums
      inefficiency. Two further issues came out of review: bloom#875 (the was_noop /
      status_update_matched intersection) and bloom#876 (lost same-checksum-skip coverage).
- [x] 7.5 Posted on bloom#859: this change does **not**
      alter the missing-manifest outcome
      (the late placement preserves it) — recording the non-interaction, since an earlier draft
      of this change would have altered it.
- [x] 7.6 File the issues before writing the PR body, so its "filed separately" paragraph carries
      real numbers.

## 8. Pre-merge

- [x] 8.1 `openspec validate fix-cyl-redelivery-blob-collision --strict`.
- [x] 8.2 `cd bloomcli && uv run --extra test pytest tests/ -m "not integration" -v`.
- [ ] 8.3 DONE: `tests/unit/` (1111 passed; the 49 failures are pre-existing Windows/POSIX
      shell-shape tests in files this branch does not touch). NOT DONE, needs a live stack:
      `make prod-up` +
      `uv run --extra test pytest tests/integration/ -v`.
- [ ] 8.4 DONE: `uvx ruff@0.9.9 check` clean on every changed file. `pre-commit run
    --all-files` still to confirm. Do **not** run
      `ruff-format` on `bloomcli/` — `.pre-commit-config.yaml` excludes it there.
- [x] 8.5 `python scripts/check-uv-locks.py`. `bloomcli/uv.lock` must not change; no dependency
      is added.
- [x] 8.6 Open the PR against `staging`. Body must contain: the literal line
      `No schema changes.` (`lint_migration_pr_body.py` requires it or an erDiagram for a
      GRANT-only migration); a justification for the `lint-migration-isolation` warning
      (`bloomcli/**` sits outside the migration surface — the grant is meaningless without its
      caller, and the fail-open makes them independently revertible); the captured red-test
      output from section 1; and an explicit "not verified by CI" note for 5.3.
- [x] 8.7 **No closing keywords** in the PR title, body, or any commit subject.
      `.github/workflows/auto-close-issues-on-staging.yml` closes same-repo issues on merge to
      `staging`, which would fire before any of section 9. Use `Refs #NNN` /
      `Addresses talmolab/sleap-roots-pipeline#76`, and the `fix(#NNN):` parenthesised subject
      form this team already uses.
- [x] 8.8 `/review-pr` after the PR exists.

## 9. Post-merge — the deployment tail (NOT done at merge)

- [ ] 9.1 **PARTIALLY CONFIRMED 2026-09-21 — behaviourally, not by direct query.** The served-request
      half is satisfied in the strongest available form: across six captured write-back logs spanning
      five workflows (`7wxm2`, `bxpmt`, `fkfkz`, `9s92h`, `p6lz2`, `hpdpf`), covering dozens of
      deliveries including genuine no-op re-deliveries where the gate must have been consulted,
      there are **zero `WARNING` lines**. That is dispositive because of how the gate reports:
      `source_already_ingested`'s `degraded_reason` is assigned to `gate_warning`
      (`ingest.py:761`) and attached to **every** `ScanResult` (`:832-833`), and `_batch.py:103`
      prints it as `WARNING {scan_key}:` — surfaced in both the summary and the JSON report
      precisely "because the log sink it would otherwise go to is unreadable in the Argo
      deployment" (`_batch.py:46-54`). A missing grant makes the gate **fail open** and emit that
      warning on every delivery. Zero warnings ⇒ the gate was answerable every time ⇒ the read
      grant is present in staging.
      **Still open:** the ACL half — `information_schema.column_privileges` / `has_table_privilege`
      was never queried directly. This task deliberately asks for *both* because "the ACL row and
      the served request are different claims"; only one has been established. Left unticked for
      that reason rather than rounded up.
      Original task: Verify the grant on **staging**, two ways: query `information_schema.column_privileges`
      _and_ issue a real `GET /rest/v1/cyl_trait_sources?select=id&idempotency_key=eq.<known>&limit=1`
      with a `bloom_workflows` token. The ACL row and the served request are different claims, and
      the gap between them is what cost 84,748 video rows. CI's DB is always empty, so the merge
      proves nothing — bloom#780's root cause.
- [ ] 9.2 Confirm the migration filename that landed is the one staging applied
      (`supabase migration list`); the retimestamp churn in 4.2 means it may have been renamed.
- [x] 9.3 **DONE** — the image built and is live: the cluster runs `bloomctl:sha-28034f6` (= merge commit `28034f6d`); previous tag for rollback was `sha-0614889`. Original: Wait for `docker-build-bloomcli` on the merge commit (it fires automatically on the
      `staging` push, path filter `bloomcli/**`) and record the immutable `sha-…` tag **and the
      previous one**, for rollback. Nothing is built by hand.
- [ ] 9.4 `bash scripts/check_cluster_drift.sh` in `sleap-roots-pipeline`; record the before
      state.
- [x] 9.5 **DONE via sleap-roots-pipeline PR #79** ("bump bloomctl to sha-28034f6 across all three templates", merged 2026-09-17T18:18:10Z) — immutable `sha-` tag, all three sites. Original: Bump the pin in **all three** templates that carry it —
      `sleap-roots-write-back-template.yaml`, `sleap-roots-images-downloader-template.yaml`
      **and `sleap-roots-exit-gate-template.yaml`** (added by sleap-roots-pipeline PR #75,
      whose own comment says "bump all three together") — to the immutable `sha-…` tag, never
      the mutable `staging` tag — `runai-busch-lab` is shared with production, so a mutable tag there
      means the next unrelated bloomcli merge silently redeploys production.
- [x] 9.6 **DONE** — verified independently 2026-09-21 via `scripts/check_registered_templates.py`: all five registered templates report IN SYNC with the pin, exit 0, four days after the bump. Original: `argo template update` in `runai-busch-lab`; re-run `check_cluster_drift.sh`.
- [ ] 9.7 Record explicitly that production now runs the new image **without** the grant (this PR
      targets `staging`; `origin/main` is 22 migrations / 241 commits behind), so production behaviour is
      today's behaviour, not the fix.
- [x] 9.8 **DONE — reproduced twice, independently, outside Argo and through the deployed path.**
      1. **Original repro, 2026-09-17 17:34Z** (Elizabeth): `bloomctl` directly against the same
         `a4_scratch_74` directories and the same two scans that produced srp#76.
         Before: `Ingested 0/2 (2 failed)` with "refusing to overwrite".
         After: **`Ingested 0/2 (2 skipped)`, EXIT=0.** Recorded in srp#76's closing comment; this
         is the closest match to the original failure and the cleanest evidence for this task.
      2. **In-cluster equivalent, 2026-09-21**, through the deployed WorkflowTemplate and image:
         on a fresh synthetic scan, only its prediction artifacts were deleted (checksums
         recorded), then re-run at an **unchanged** `predict_code_sha` — so predict genuinely
         rewrote different bytes at a fixed key (`lateral fc69ed2f…→6f33e9be…`,
         `primary f4a06804…→18366b91…`, `idempotency_key 4e17ca1c…` unchanged). Workflow
         `sleap-roots-pipeline-hpdpf`, `Succeeded`, write-back exit 0, write-back succeeded
         (`source_id=133`), **zero** occurrences of "refusing to overwrite" or any blob error.
      The second is the stronger form: it exercises the recompute-at-unchanged-key condition that
      is the actual trigger, through the path production will use.
- [ ] 9.9 After the staging→main promotion merges, re-run 9.1 against the production DB and 9.8
      against production.
- [ ] 9.10 Only once 9.1–9.9 are ticked: close sleap-roots-pipeline#76, then open the
      `chore(openspec): archive fix-cyl-redelivery-blob-collision` PR. Do not archive earlier —
      bloom#708 and bloom#806 both still carry unfinished deploy-verification tails.
- [x] 9.11 **CONFIRMED** — PR #75 merged 2026-09-16T18:50:18Z (`561d0571`); its change is now archived upstream as `2026-09-21-add-partial-success-exit-gate`. sleap-roots-pipeline PR #75 has **merged**, so the earlier hold on editing its
      `docs/bloom-integration/roadmap.md` and `add-partial-success-exit-gate/tasks.md` is
      lifted. That merge is what introduced the third pin site in 9.5 — re-read it before
      bumping.
