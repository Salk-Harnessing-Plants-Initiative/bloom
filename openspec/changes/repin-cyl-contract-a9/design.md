## Context

This is the third re-pin of `insert_cyl_result_envelope`'s accepted `contract_version`. The earlier
ones were a2→a3 (`repin-cyl-contract-a3`, #393/#399) and a3→a7 (`repin-cyl-contract-a7`,
#685/#766). It differs from both precedents in three ways:

1. **The live function is the 2-arg overload.** `20260912110000` dropped `(jsonb)` and created
   `(jsonb, text DEFAULT NULL)`. `20260917140000` then added the bloom#875 no-op fallback. No later
   migration on `staging` (`7f3ff94a`, checked 2026-09-25) redefines it. The a7 migration's 1-arg
   body is two revisions stale.
2. **Rows with the retiring version are expected to be real.** bloom#895 reports `0.1.0a7` rows from
   Bloom-dispatched runs on staging. That was not re-counted here, since no read-only route to the
   staging DB was used. The design below holds whether or not such rows exist.
3. **An a7 producer is deployed.** The cluster's traits template runs `sha-689cffb` (contracts a7,
   `sleap-roots-trait-extractor-template.yaml` on `sleap-roots-pipeline` `origin/main`). The cluster
   is shared by staging- and production-dispatched runs.

### The restamp claim, verified 2026-09-25 against the upstream tags

- `schema/result_envelope.schema.json` differs only in `$id` for a7→a8 and for a8→a9.
- An AST comparison of `models.py` finds `ResultEnvelope`, `Provenance`, `TraitValue`, `BlobRef`,
  `InputRef` and `ModelRef` identical between `v0.1.0a7` and `v0.1.0a9`.
  - The only model changes are a8's **BREAKING** `ModelCard` reshape (flat species, mode and age
    become a `selectors` tuple), its new `Selector`, and two private validators.
  - All of those are predict-side, and Bloom imports none of them.
- `identity.py` and `hashing.py` are unchanged. `contract_version` is not an idempotency-key input,
  but `traits_code_sha` is.
- a9's substantive addition is per-run run-manifest **naming and resolution**: writer-side helpers
  (`run_manifest_name_for_writing`, `pipeline_run_id_from_env`) and the reader `load_run_manifest`.
  It is additive; `RunManifest` and `RUN_MANIFEST_FILENAME` are unchanged.
  - `bloomctl` still writes and reads the legacy `run_manifest.json`.
  - The a9 traits reader accepts the legacy name via `allow_legacy=True` until
    sleap-roots-pipeline#82.
  - "Pure restamp" therefore refers to the RPC and the schema, not to every package consumer.

## Decisions

### Single-literal cutover to a9, option (a)

bloom#895 offered two options:

- **(a)** a cutover window: re-pin Bloom first and bump the pipeline promptly after;
- **(b)** a transitional accepted set `{0.1.0a7, 0.1.0a9}`, narrowed later.

Decided: (a), by the project owner on 2026-09-25, because it is Bloom's established pattern (#766,
then pipeline#52). (b) had a real case this time, since Context items 2 and 3 did not hold for a3 or
a7. It was declined because the window is loud and nothing in it is lost (next section). The single
exact pin keeps its original rationale: each row anchors to exactly one contract-of-origin.

### Rejection window: loud, and recovered by recompute rather than replay

Between this migration applying to the staging Supabase and the traits template bump:

- **An a7 envelope is rejected.** The version check (body step 2) runs before the source gate
  (step 5), so a rejected envelope writes nothing.
- **The rejection is loud.**
  - `write-back` has no `continueOn` (`sleap-roots-pipeline.yaml`), so the Workflow goes red.
  - `bloomctl` names the failure (`bloomcli/src/bloomctl/cyl/ingest.py`, the
    `"contract_version mismatch" in msg` branch).
  - It classifies it `retriable`, so Argo retries and the step then fails.
- **Re-delivery does not recover anything.** Re-running a scan in the window re-delivers the same a7
  envelope, which is rejected again. That includes scans whose a7 data was already ingested, whose
  bloom#875 no-op status path is unreachable because the call raises first.
- **Already-ingested scans show `failed` too.** The same-key re-delivery that used to be a benign
  no-op now raises, so during the window a run marks every in-scope scan `failed`, including ones
  whose data in Bloom is already correct. That data is untouched; only the run's status is noisy.
  If the a9 extractor itself fails after the bump (it runs with `continueOn: failed`,
  sleap-roots-pipeline#86), write-back re-delivers the stale a7 files and the same noise returns.
  The `contract_version mismatch` hint then points at a pin, while the real cause is the extractor.
- **Recovery is recompute.** The a9 extractor skips a scan only when both the idempotency key and
  the `contract_version` of its existing result match (`trait_extractor/extractor.py`), so the
  version change alone makes it recompute every in-scope scan. The new image's `traits_code_sha`
  also gives a new key. Each recompute delivers a fresh a9 envelope, which becomes the scan's latest
  source (highest `source_id`); the a7 rows stay as superseded history. Because the legacy
  `run_manifest.json` still accumulates keys until the a9 `bloomctl` writer is live and the legacy
  files are deleted (sleap-roots-pipeline roadmap step 6, #88), "in-scope" can be wider
  than the requested scans, so expect a burst of new source and trait rows after the bump.
- **Values from two extractor builds.** Until every scan is recomputed, one experiment can hold
  `sha-689cffb` and `sha-e373b0f` values side by side, and no read path shows which build produced
  a value. Between those sleap-roots commits the `sleap_roots` trait library is unchanged, the only
  dependency change is the contracts pin, and the extractor diff touches no computation path
  (`load_series`, `choose_pipeline`, `compute_scan_traits`, `scan_trait_values`), so recomputed
  values are expected to be identical. Acceptance (tasks §5.3) compares one recomputed scan's a9
  values against its a7 source to confirm it.
  - Scans that were rejected in the window must be re-triggered after the bump. The trigger does not
    re-queue them itself.
  - Nothing already ingested is lost, and nothing is written twice under one key.

Keeping the window short is the mitigation: the pipeline PR is prepared before this merges, and it
is merged only once the apply is verified.

### No cutover guard

The a3 and a7 migrations prepend a `DO` block that raises if any row carries the retiring version.
This change does not carry it forward or redesign it, and makes that a requirement:

- **It would trip on day one if the rows exist.** The a7 guard wedged every staging deploy for six
  days on 10 a3 rows (bloom#685, 2–8 September). That was cleared only by the bloom#787 restamp of
  rows that happened to be disposable fixtures.
- **Restamping is not acceptable.** Rewriting real rows' `metadata.contract_version` falsifies their
  provenance.
- **It guards a non-problem.** Its premise was that an old-version row "becomes non-current". The pin
  gates **new inserts only**.
  - As of `7f3ff94a`, the only SQL mentioning `contract_version` is this RPC's own definitions
    (`20260630180000`, `…a3`, `…a7`, `20260912110000`, `20260917140000`).
  - No view, read RPC or app reader filters on it. bloommcp's `qc_clean` records its own installed
    package version, and `bloomctl` only string-matches the rejection.
  - An a7 row is exactly as current after this migration as before.

The requirement is scoped to "refuse to apply *because* retiring-version rows exist" and to
restamping. A future real contract revision that genuinely needs a data-dependent step keeps the
manual staging and production check. `contract-pinning` and README step 5 are updated so they no
longer present the guard as the pattern.

### Body source: `20260917140000`, verbatim except the literal

The new migration copies everything from `CREATE OR REPLACE FUNCTION` through the final `GRANT` of
`20260917140000` and changes only the `pinned_version` line. That includes the signature,
`SECURITY DEFINER`, `search_path`, owner and grants, which already include `bloom_workflows`. A unit
test asserts the exact one-line difference and the absence of any `DO`, `RAISE`, `UPDATE` or
`DELETE` outside the function body.

### Rollback: restore the `20260917140000` body, and do it durably

`supabase/rollbacks/20260917140000_…_rollback.sql` restores the older `20260912110000` body, without
the fallback, so it is not the template. The new rollback carries the `20260917140000` region
verbatim.

The rollback file is only the emergency hot-apply for staging. Applying it by hand leaves
`20260925120000` recorded as applied, and CI, fresh stacks and the next promotion to production would
still apply a9. A **durable** rollback is, taken together:

1. drain first: no sleap-roots workflow running (the hot-applied a7 RPC rejects in-flight a9
   envelopes, the mirror of the forward window), and a `check_cluster_drift.sh` pre-image;
2. a new forward migration whose body is the rollback file (it restores the body only; the
   `20260925120100` grant fix stays, because `CREATE OR REPLACE` keeps the ACL);
3. `contracts/` re-pinned to `v0.1.0a7` (`pin.json`, schema `$id`) and `PINNED_VERSION` flipped
   back, with the version tests inverted, or the pin/RPC tie and the a9 tests fail;
4. the traits template re-pinned to
   `sha-689cffb@sha256:ab5a1f43a74f2d00e809f2deb0dc886876028cc3028b0fdaf600f408e860f369`, with the
   tag, digest and `SRT_TRAITS_CONTAINER_DIGEST` moving together.

Moving only one side reopens the mismatch. Hot-apply over SSH to the deploy host as `postgres`
(`docker compose … exec -T db-prod psql -U postgres -v ON_ERROR_STOP=1 < <rollback file>`).

**A rollback does not restore a7 values as what readers see.** Every a9 source keeps the highest
`source_id` for its scan, so it stays "latest". The re-pinned a7 extractor recomputes each scan
(its key and version both differ from the a9 result on disk) and overwrites the a9 file, but its
envelope carries the scan's original a7 key, so the a7 RPC treats it as a no-op. Rolling back
stops new a9 writes; it does not remove a9 data. If a9 values themselves were wrong, that needs a
separate, deliberate data fix. Do not `git revert` the squash commit: it would remove an
applied migration from the tree and take the a7 archive with it.

### Archive `repin-cyl-contract-a7` here, superseding PR #779

`repin-cyl-contract-a7` is still in `openspec/changes/`, and the live spec still states `0.1.0a3`.
Both changes MODIFY *Write-back validates the contract version*. Archiving a9 before a7 silently
restores a7 text, which a dry run confirmed, and `--strict` cannot see it.

PR #779 (open since 2026-09-02) already archives a7, but merging it separately leaves the ordering to
chance. The owner decided to archive a7 in this PR as its own first commit and close #779 as
superseded. The archive carries #779's recorded evidence for a7's §2.4 and §4.2: the *Docker Compose
Health Check* job passed (838 passed, 7 skipped) in workflow runs 33524629806 (head `ff4e160e`) and
33566795258 (the merged head `b4282d8b`). Both workflow runs concluded `failure` on an unrelated
image-scan job; the integration job itself was green. The a7 body has been live on staging since #787 closed (2026-09-08).

### Only the staging Supabase is a write-back target today

Every workflow's write-back mounts `genericsecret-bloom-staging-pipeline-credentials` (the only
credential mounted), production-dispatched runs included (#863). The gate for the pipeline bump is
therefore "applied to staging Supabase".

When this reaches `main`, production's RPC also moves to a9. That has no producer impact **only
while #863 stands**. If #863 gives production its own write-back before the pipeline bump,
production-dispatched a7 envelopes would be rejected there too.

### Restrict the RPC's EXECUTE grants (a bug fix restoring the specified behaviour)

`cyl-trait-writeback` already requires that `EXECUTE` on the write-back RPC is granted only to
`bloom_writer`, `service_role`, `bloom_admin` and `bloom_workflows` (*Write-back RPC ingests a
ResultEnvelope*, and its scenario "exactly [those four] hold `EXECUTE`"). The deployed function did
not meet it. Supabase's default privileges grant `EXECUTE` on every new `public` function to `anon`
and `authenticated` directly, and every definition of this RPC revoked only `FROM PUBLIC`, which does
not remove those direct grants. Of the 18 `SECURITY DEFINER` functions in `public`, it was the only
one without the repo's usual `REVOKE … FROM PUBLIC, anon, authenticated`: it predates the team
learning about the default privileges (PR #469, 2026-07-20), and each re-pin copied its grant lines
forward. `test_execute_grants_are_exactly_the_sanctioned_roles` checked only selected roles, so CI
never saw it.

- **Fix:** a separate ACL-only migration, `20260925120100`, revokes `FROM PUBLIC, anon, authenticated`
  and re-asserts the four grants. No body, owner or signature change, so the a9 migration stays the
  newest definition and its one-line-diff tests are untouched.
- **No spec delta.** The requirement already states the intended behaviour, so this restores it
  rather than changing it. MODIFYing that requirement here would also collide with the unarchived
  `fix-cyl-pipeline-run-scan-status`, which MODIFIES the same requirement (the archive-ordering
  hazard this change otherwise avoids).
- **Tests:** the grants test now compares the whole grantee set (`aclexplode`) and the effective
  privilege of `anon`/`authenticated`; a new catalog-wide test fails if any `SECURITY DEFINER`
  function in `public` is executable by `anon`, or by `authenticated` without an allowlisted,
  read-only reason; a unit test pins the migration to ACL-only statements.
- **Future re-pins** that copy this body must carry the full `REVOKE`; the catalog test enforces it.

### Pin-to-RPC tie

Nothing couples `contracts/pin.json` to the RPC literal, which is how the RPC sat at a3 while the pin
read a5 (fixed by a7). A new check in `test_contract_migration_match.py` reads the live literal via
`pg_get_functiondef` and compares it with `pin.json`. The two can no longer drift apart, and a
half-done re-pin fails CI.

## Risks / Trade-offs

- **The rejection window.** Covered above. It is bounded by preparing the pipeline PR in advance.
- **The pipeline bump landing first by mistake.** That produces the same loud, recover-by-recompute
  failure. The bloom#895 gate (verified apply first) prevents it.
- **Staging deploys need approval.** Each staging deploy waits on the `staging` environment's
  required reviewers. Runs 35657797607 and 36196049972 were held and then rejected as superseded;
  `20260921130000` and `20260924120000` were applied on 2026-09-25 by run 36192393915, and run
  36456935639 (2026-09-28) found nothing pending, so a9 should apply alone. In `deploy.yml` the stack and smoke test run
  before migrations, so a smoke failure also stops a9.
- **Out-of-order migration with #910.** #910 (open) adds `20260928120000`. If it merges and deploys
  first, `supabase db push` refuses this PR's older `20260925120000`; the migration lint catches it
  once this branch is updated. Merge this PR first, or re-timestamp it.
- **Blobs uploaded during the window stay unreferenced.** For a scan first computed during the
  window, `bloomctl` uploads its `.slp` under the a7 key before the RPC rejects the envelope. No a9
  delivery references that object. It is harmless clutter; keeping the window short keeps it small.
- **Retrying a window workflow does not recover it.** The window run's `cyl_pipeline_run_scans`
  rows end `failed`, and the RPC's `status != 'failed'` guard keeps them there, so `argo retry`
  cannot fix its bookkeeping (and re-runs the stored a7 template). `argo resubmit` gets a new
  workflow name that Bloom has no rows for. Recovery is a **new** Bloom trigger
  (`POST /workflows/pipeline`), whose own rows start `queued` and are matched normally.
- **Drain before the template bump.** The traits directory is a shared hostPath. A window workflow
  still retrying with the stored a7 template would recompute over a fresh a9 result (different key)
  and write an a7 envelope back. Update the template only when no sleap-roots workflow is running.
- **The migration-isolation lint (warning mode) flags `contracts/pin.json` and the vendored
  schema.** The coupling is intended: the new pin/RPC tie makes them one unit. The PR body explains
  this. A cosmetic `bloomcli/tests` edit was dropped in review to keep the exception to that pair.
- **Exact-pin churn** continues. Each restamp needs a migration. This is accepted, as before.

## Migration Plan

1. Archive a7, then commit the proposal.
2. Add the explicit-a7 precursor for three tests.
3. Re-pin the vendored contract and README.
4. Add the migration, rollback and tests (RED then GREEN, locally).
5. Merge, clear the staging deploy approval, then verify the apply by the live literal (tasks §5.1),
   not by step colour.
6. Bump the pipeline and check acceptance.
