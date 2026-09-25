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
- **Recovery is recompute.** The first run after the bump uses a new image with a new
  `traits_code_sha`, which gives a new idempotency key. Skip-if-done therefore recomputes every
  in-scope scan (per sleap-roots#269) and delivers a fresh a9 envelope.
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
still apply a9. A **durable** rollback is three steps, taken together:

1. a new forward migration whose body is the rollback file;
2. `PINNED_VERSION` flipped back;
3. the traits template re-pinned to
   `sha-689cffb@sha256:ab5a1f43a74f2d00e809f2deb0dc886876028cc3028b0fdaf600f408e860f369`, with the
   tag, digest and `SRT_TRAITS_CONTAINER_DIGEST` moving together.

Moving only one side reopens the mismatch. Do not `git revert` the squash commit: it would remove an
applied migration from the tree and take the a7 archive with it.

### Archive `repin-cyl-contract-a7` here, superseding PR #779

`repin-cyl-contract-a7` is still in `openspec/changes/`, and the live spec still states `0.1.0a3`.
Both changes MODIFY *Write-back validates the contract version*. Archiving a9 before a7 silently
restores a7 text, which a dry run confirmed, and `--strict` cannot see it.

PR #779 (open since 2026-09-02) already archives a7, but merging it separately leaves the ordering to
chance. The owner decided to archive a7 in this PR as its own first commit and close #779 as
superseded. The archive carries #779's recorded evidence for a7's §2.4 and §4.2: *Docker Compose
Health Check* runs 33524629806 and 33566795258, 838 passed and 7 skipped, on the head merged
2026-09-02. The a7 body has been live on staging since #787 closed (2026-09-08).

### Only the staging Supabase is a write-back target today

Every workflow's write-back mounts `genericsecret-bloom-staging-pipeline-credentials` (the only
credential mounted), production-dispatched runs included (#863). The gate for the pipeline bump is
therefore "applied to staging Supabase".

When this reaches `main`, production's RPC also moves to a9. That has no producer impact **only
while #863 stands**. If #863 gives production its own write-back before the pipeline bump,
production-dispatched a7 envelopes would be rejected there too.

### Pin-to-RPC tie

Nothing couples `contracts/pin.json` to the RPC literal, which is how the RPC sat at a3 while the pin
read a5 (fixed by a7). A new check in `test_contract_migration_match.py` reads the live literal via
`pg_get_functiondef` and compares it with `pin.json`. The two can no longer drift apart, and a
half-done re-pin fails CI.

## Risks / Trade-offs

- **The rejection window.** Covered above. It is bounded by preparing the pipeline PR in advance.
- **The pipeline bump landing first by mistake.** That produces the same loud, recover-by-recompute
  failure. The bloom#895 gate (verified apply first) prevents it.
- **Stuck staging deploys.** Run 35657797607 (2026-09-21) awaits environment approval, and every
  later deploy was cancelled. This PR's migration applies only after that queue is cleared, and it
  may apply in one `db push` batch with `20260921130000` and any other pending file. A failure in an
  earlier file stops a9.
- **The migration-isolation lint (warning mode) flags `contracts/` and `bloomcli/tests/`.** The
  coupling is intended: the new pin/RPC tie makes them one unit. The PR body explains this.
- **Exact-pin churn** continues. Each restamp needs a migration. This is accepted, as before.

## Migration Plan

1. Archive a7, then commit the proposal.
2. Add the explicit-a7 precursor for three tests.
3. Re-pin the vendored contract and README.
4. Add the migration, rollback and tests (RED then GREEN, locally).
5. Merge, clear the staging deploy approval, then verify the apply by the live literal (tasks §5.1),
   not by step colour.
6. Bump the pipeline and check acceptance.
