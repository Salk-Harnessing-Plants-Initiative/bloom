## Context

`insert_cyl_result_envelope`'s per-scan status update — added by `fix-cyl-pipeline-run-scan-status`
(its migration, `20260912110000`, is headed "Change: fix-cyl-pipeline-run-scan-status"; that
change's own spec delta undersells what it shipped — see the Risks section below) — has two
branches in the migration currently at
`supabase/migrations/20260912110000_add_cyl_writeback_run_scan_status.sql`:

- **Step 5 (no-op branch, `:164-193`)** — reached when the source insert's
  `ON CONFLICT (idempotency_key) DO NOTHING` returns no row (the run is already ingested). It
  updates `cyl_pipeline_run_scans` by joining on `argo_workflow_name = p_argo_workflow_name AND
  source_id = v_source_id`.
- **Step 9 (non-no-op branch, `:289-296`)** — reached on a genuine first delivery. It updates by
  joining on `argo_workflow_name = p_argo_workflow_name AND scan_id = v_scan_id` (the scan
  resolved from this delivery's own `image_ids` in step 6), and is the *only* statement that ever
  writes `source_id` onto a `cyl_pipeline_run_scans` row — it sets `source_id` in the same
  statement as `status = 'written'`.

Both branches share the guard `AND status != 'failed'`, so a late/out-of-order delivery can never
resurrect a scan already closed out by `fail_cyl_pipeline_run_scans_without_result`.

`cyl_pipeline_run_scans` rows are **inserted** by `services/workflows/pipeline.py`'s dispatch
path (`client.table("cyl_pipeline_run_scans").insert(scan_rows)`, `pipeline.py:322`) at run
*creation* time, with `argo_workflow_name` and `source_id` both `NULL` — the insert dict carries
only `(run_id, scan_id, batch_index, status)`. `argo_workflow_name` is filled in **later**, once
Argo actually claims and runs the batch, by `complete_cyl_pipeline_batch`'s `UPDATE ... SET
argo_workflow_name = p_argo_workflow_name ... WHERE argo_workflow_name IS NULL`
(`20260817120000_add_cyl_pipeline_dispatch_functions.sql:194-207`) — that function never inserts
a row, only ever updates one already inserted by `pipeline.py`. `source_id` is never touched by
either; the `bloom_workflows` `INSERT` grant on `cyl_pipeline_run_scans` is scoped to
`(run_id, scan_id, batch_index, status)` and deliberately excludes `source_id`
(`20260730120000_create_cyl_pipeline_runs.sql:131-136`). Combined with the point above
(`source_id IS NOT NULL` implies `status = 'written'`), this means: **a freshly dispatched row
under a new `argo_workflow_name` always has `source_id IS NULL`, and step 5's join can never match
it.** `idempotency_key` is deliberately run-independent — the sole business identity is
`(images_checksum, models, param_hash, predict_code_sha, sleap_roots_predict_code_sha)`, not the
Argo workflow — so *any* pipeline re-run over already-ingested scans (a manual re-run, a nightly
sweep, a re-triggered batch after fixing an unrelated poison scan) reaches this case, not only an
adversarial or exotic retry — **provided** the scan's *original* delivery was itself dispatched
through this same `pipeline.py` path (see the Verification section's scope-limit note: a source
whose original delivery was a hand-submitted `argo submit`, bypassing `pipeline.py` entirely,
never gets a row inserted at all, so no row anywhere is ever available for this change's fallback
to resolve `scan_id` from).

Confirmed live on staging, 2026-09-17 (bloom#875): a 3-scan batch —
`sleap-roots-pipeline-fkfkz`, `pipeline_run_id=9` — where 2 scans were genuine re-deliveries
(their envelopes' idempotency keys already existed) and 1 was a real prediction failure (the
"poison" scan) produced `done_count=0, failed_count=3` on a Workflow that reported `Succeeded`.
Both re-delivered scans' `cyl_scan_traits`/`cyl_scan_intermediates` rows were present and correct
under their original `source_id`s the whole time. **This measurement demonstrates the symptom
bloom#875 is about, not the exact case this change's fallback closes**: per the issue's own
comment, "the two good scans had been ingested earlier the same day by two hand-submitted runs"
— i.e. their *original* delivery never went through `pipeline.py`, so no `cyl_pipeline_run_scans`
row anywhere ever had their `source_id` stamped, and this change's fallback (which requires an
existing stamped row to resolve `scan_id` from) has nothing to find. Re-running that exact
scenario after this change merges still produces `failed_count=3` — verified against the RPC
directly, not asserted; see the Verification section. Independently caught and verified during
`/review-pr` (PR #880 review comments): the fix is still correct and still has real value for the
case it does cover (below), but the live measurement is evidence of the general symptom's
severity, not evidence that this specific case is now fixed.

## Goals / Non-Goals

Goals:

- A re-delivery under a **new** `ARGO_WORKFLOW_NAME` of an already-ingested scan reports
  `status_update_matched: true` and leaves that workflow's `cyl_pipeline_run_scans` row
  `'written'` with the correct `source_id`, exactly as an intra-workflow retry already does.
- Preserve every existing guarantee `fix-cyl-pipeline-run-scan-status` and
  `fix-cyl-redelivery-blob-collision` established: the late-delivery-after-`'failed'`
  resurrection guard, the "same key, different scan" short-circuit, and `was_noop`'s own
  idempotency semantics are all unchanged.
- No `bloomctl` change. `ingest_one_envelope`'s `status_update_matched is False → failed` check
  (`ingest.py:816`) and its message are correct as written; the RPC has been handing them a false
  negative.

Non-Goals:

- Threading `pipeline_run_id` through as a Workflow parameter so write-back no longer depends on
  the TTL'd, per-batch `argo_workflow_name` as its join key. Real fix for the class of problem
  this patches around, but a cross-repo contract change (`sleap-roots-pipeline` would need to
  inject it, Bloom would need a column to receive it). Scope as a follow-up; do not smuggle it in
  here.
- Rescuing a `cyl_pipeline_run_scans` row whose original delivery never supplied
  `ARGO_WORKFLOW_NAME` at all (a manual `cyl ingest-result` run with no pipeline-run context).
  No `cyl_pipeline_run_scans` row is ever stamped with that source's `source_id` in that case, so
  there is nothing for the fallback to look up. This is not a regression — today's behavior for
  that case is identically `status_update_matched: false`.
- **Correction to an earlier draft of this section, caught during `/review-pr`: rescuing a source
  whose original delivery WAS dispatched via Argo but never through `pipeline.py`.** A
  hand-submitted `argo submit` run has `ARGO_WORKFLOW_NAME` set (Argo injects it into every pod
  regardless of how the Workflow was submitted), but its scan was never inserted into
  `cyl_pipeline_run_scans` at all — that only happens in `pipeline.py`'s own dispatch path, which
  a bare `argo submit` bypasses entirely. So the RPC's step 9 (on that original, non-no-op
  delivery) finds no row to `UPDATE`, `source_id` is never stamped anywhere for that source, and
  this change's fallback — which only ever reads an *existing* stamped row — has nothing to
  resolve `scan_id` from on any later re-delivery. **This earlier draft claimed this was "not the
  shape bloom#875 measured." That was wrong: it is exactly the shape bloom#875's own live
  reproduction measured** — both re-delivered scans there were originally ingested by
  hand-submitted runs. The fix therefore does not close the live-measured case; it closes the
  case reachable without that precondition (an ordinary re-run of a previously Bloom-dispatched
  batch under a fresh `pipeline_run_id`), which the issue's own text separately confirms is
  reachable and is the production-relevant path (retries, batch re-runs). See Context and
  Verification for the corrected framing.
- Fixing `sleap-roots-pipeline`#56's exit gate or #71's manifest-union behavior. Orthogonal;
  referenced only for context.

## Decisions

**Decision: resolve the fallback `scan_id` from `cyl_pipeline_run_scans` itself, keyed on
`source_id` — never from this delivery's own `image_ids`.**
The obvious-looking alternative — re-run step 6's `image_ids` → `scan_id` resolution inside the
no-op branch and use that — is exactly the shape the "same key, different scan" short-circuit
rule (`test_same_key_different_scan_short_circuits`) exists to forbid: a no-op must be governed
by the run of record's own scan, not by whatever scan this particular re-delivery claims. A
`cyl_pipeline_run_scans` row with `source_id = v_source_id` was written *only* by a prior
successful (non-no-op or already-rescued no-op) delivery of this exact source, in the same
statement that resolved and pinned its `scan_id` — so it is already the authoritative answer,
requires no new resolution logic, and cannot diverge from the run of record by construction.

**Decision: the fallback carries the identical `AND status != 'failed'` guard as the primary
UPDATE.** Otherwise a re-delivery under a new workflow name could resurrect a scan a *different*
workflow's reconciliation had already closed out `'failed'` under the run-of-record's own
`source_id`-bearing row — the same resurrection the existing guard exists to prevent, just
reached through a different join. This preserves `test_status_update_matched_false_on_noop_redelivery_after_already_failed`
verbatim: that test's row is looked up by the fallback's `SELECT ... WHERE source_id = v_source_id`
too (it is, after all, the row that matches), and the fallback `UPDATE`'s own `status != 'failed'`
guard still excludes it.

**Decision: only fall back when the primary UPDATE's row count is zero, not always.** Running
the fallback unconditionally would be redundant (the primary join already covers the
intra-workflow-retry case, which is the common one) and would risk a second `UPDATE` touching
zero rows silently masking a real primary-path bug. Checking `GET DIAGNOSTICS` first and only
falling back on `0` keeps the change to the exact gap bloom#875 identifies.

**Decision: same-scan-different-source collisions are not this change's problem.** If two
distinct `source_id`s both ever got attached to `cyl_pipeline_run_scans` rows for the same
`scan_id` (which would require two independent pipeline runs producing two different sources for
one scan — a real, if unusual, occurrence: re-processing a scan with different params), the
fallback's `SELECT scan_id FROM cyl_pipeline_run_scans WHERE source_id = v_source_id LIMIT 1` is
keyed on `source_id`, not `scan_id`, so it is unaffected — it only ever returns the one `scan_id`
that this specific `source_id` is bound to, which is single-valued by the source/scan pairing
invariant `insert_cyl_result_envelope` already maintains (a `source_id` is stamped onto
`cyl_pipeline_run_scans` only by a delivery that resolved exactly one `scan_id` for it).

## Risks / Trade-offs

- **Archive-ordering hazard with two unarchived siblings.** `fix-cyl-pipeline-run-scan-status`
  (64/72 tasks — remaining tasks are roadmap/issue-closing housekeeping, task 15.1-15.3) and
  `fix-cyl-redelivery-blob-collision` (60/73 tasks, bloom#871 — remaining tasks are the staging
  grant/Argo-pin verification in its own tasks.md §9) are both merged to staging but deliberately
  **not archived yet**, blocked on work outside this change's scope. All three changes carry
  deltas on `cyl-ingest-cli`:

  | Change | `cyl-ingest-cli` requirement touched |
  |---|---|
  | `fix-cyl-pipeline-run-scan-status` | MODIFIED "Cyl ingest command reads an envelope from a path or stdin" |
  | `fix-cyl-redelivery-blob-collision` | ADDED "An already-ingested envelope skips blob upload"; MODIFIED "Blob handling defaults to pass-through…", "Blob checksum integrity is verified before upload", "Blob upload is idempotent", "A failed blob upload aborts before the RPC call", "Re-ingest is a benign, distinctly-reported no-op" |
  | **This change** | MODIFIED "Re-ingest is a benign, distinctly-reported no-op" |

  This change and `fix-cyl-redelivery-blob-collision` **both** modify "Re-ingest is a benign,
  distinctly-reported no-op" — the only textual collision among the three (confirmed
  clause-by-clause, whitespace-normalised, against the current spec and both siblings' delta
  files: `fix-cyl-pipeline-run-scan-status` never touches this requirement, and none of this
  change's touched requirements overlap `fix-cyl-redelivery-blob-collision`'s other five). Because
  `openspec archive` replaces a `MODIFIED` requirement's full text wholesale, whichever of these
  two archives *second* would silently discard the other's edit to this one requirement if its
  delta were authored blind.

  **Verified low-risk in practice:** the two deltas' shared scenario ("Re-delivery whose producer
  recomputed its artifacts") is textually identical in both versions and carries no bloom#875
  language in either, so this change's delta for this one requirement genuinely is a strict
  superset of the sibling's (its body plus this change's own new scenario) — whichever of the two
  archives last, applying this change's version loses nothing. **Correction to an earlier draft of
  this section:** the bloom#875 carve-out sentence itself does **not** live in this requirement.
  It lives in `fix-cyl-redelivery-blob-collision`'s separate, **ADDED** requirement "An
  already-ingested envelope skips blob upload" (`specs/cyl-ingest-cli/spec.md:53`, inside the
  "Re-delivery after a recompute that produced different bytes" scenario), which this change does
  not touch at all — caught by `/review-openspec`'s spec-quality pass, which diffed the two files
  directly rather than trusting this document's first draft. See the next bullet for the
  consequence.

- **A sibling's stale time-bound language survives this change's merge, and this change does not
  edit it.** `fix-cyl-redelivery-blob-collision`'s own files describe the bloom#875 gap as
  *currently* open, in language that stops being true once this change deploys but stays written
  as-is for as long as that change remains unarchived (§6.3: indefinitely, pending unrelated
  infra work):
  - `specs/cyl-ingest-cli/spec.md:49-53`: "...which a re-delivery dispatched under a *different*
    `ARGO_WORKFLOW_NAME` **currently always does**... reconciling the two contracts is tracked as
    bloom#875"
  - `design.md:153-155`: "Filed as bloom#875; **until then** the spec claim of 'exits zero' holds
    ... **not for a re-run from a fresh pipeline run**."

  This change deliberately does not rewrite either sentence: both live inside a requirement this
  change does not own (the first is under an ADDED requirement; the second is in a different
  change's own design doc), and editing another unarchived change's normative delta from within
  this one — especially a requirement not yet in the current `openspec/specs` baseline — is
  exactly the kind of blind cross-change edit the archive-ordering hazard above warns against.
  Instead, `tasks.md` §6 adds a **post-deploy annotation** task: once this change's migration is
  verified live on staging, add a short "RESOLVED — see fix-cyl-redelivery-status-fallback"
  pointer next to both sentences, without altering their normative text (that text is superseded
  properly whenever `fix-cyl-redelivery-blob-collision` itself is next revisited or archived).

- **The `cyl-trait-writeback` requirement this change modifies ("Write-back RPC ingests a
  ResultEnvelope") is the same requirement `fix-cyl-pipeline-run-scan-status` modifies, and that
  sibling's own delta text does not describe step 5's no-op-branch `source_id`-keyed UPDATE at
  all** — it describes only a single scan_id-keyed update applying uniformly to both branches.
  The migration that actually shipped (headed "Change: fix-cyl-pipeline-run-scan-status" — this
  is that same change's own code, refined during its later review rounds, not something a
  different sibling added afterward) already implements the two-branch split in Context above;
  its delta file is simply stale relative to its own migration. This change's delta is written
  against the *migration's actual current behavior*, not against that stale delta text, and is a
  strict superset of it plus the fallback. Same archive-last-wins caution applies if
  `fix-cyl-pipeline-run-scan-status` archives after this change.

- **The fallback's `LIMIT 1` assumes one `scan_id` per `source_id` among `cyl_pipeline_run_scans`
  rows.** True today by construction (see Decisions), but if a future change ever lets one source
  span multiple scans, this silently returns an arbitrary one. Not re-litigated here; flag it if
  that invariant ever changes.

- **Pre-existing, not introduced here: `(argo_workflow_name, scan_id)` has no DB-level
  uniqueness.** Both the fallback's targeted update and step 9's non-no-op update key on this
  pair, but the only real constraint on `cyl_pipeline_run_scans` is `UNIQUE (run_id, scan_id)`
  (`20260730120000_create_cyl_pipeline_runs.sql:106`) — `argo_workflow_name` is a bare nullable
  `text` column with nothing tying it 1:1 to a `run_id`. If a workflow name were ever reused
  across two `run_id`s for the same scan (not DB-enforced), either update could silently touch 2
  rows instead of 1. `/review-pr`'s behavioral-correctness pass found this while tracing the
  fallback and confirmed it is inherited, not introduced — step 9's identical `WHERE` shape has
  carried it since `fix-cyl-pipeline-run-scan-status`. Recorded here since this change is the one
  that added a second instance of the same pattern; not fixed, as it is out of scope for a change
  whose only job is closing bloom#875.

  **PR #880 review (blm3886) confirms the same gap independently and proposes a concrete fix**:
  a partial `UNIQUE (argo_workflow_name, scan_id)` index would close it, and — since
  `cyl_pipeline_run_scans` currently has no index beyond the PK and `UNIQUE (run_id, scan_id)`
  — would also turn the fallback's `WHERE source_id = ...` lookup from a sequential scan into an
  index scan if paired with a plain index on `source_id`. Not implemented here: it is a schema
  change (new migration, index-build behavior, a real constraint rather than a lookup
  optimization) beyond this change's minimal-footprint scope, and a partial-unique constraint on
  `(argo_workflow_name, scan_id)` deserves its own review of whether any legitimate path could
  ever want two rows sharing that pair (none is known today, but that's exactly the kind of claim
  this program's review process exists to verify independently, not assume). Filed as bloom#881
  rather than folded in.

- **Verification cannot be closed with a fresh live Argo run today.** See the Verification
  section below.

## Migration Plan

One forward-only migration, `CREATE OR REPLACE FUNCTION public.insert_cyl_result_envelope(jsonb,
text)` — the signature is unchanged (still `(envelope jsonb, p_argo_workflow_name text DEFAULT
NULL)`), so no `DROP FUNCTION` is needed and there is no `PGRST202` window. Only step 5's body
gains the fallback block; steps 1-4 and 6-9 are copied unchanged from the current function body so
the `CREATE OR REPLACE` is a full-body replacement (Postgres has no partial-function-body syntax).
Owner and `EXECUTE` grants are unaffected by a same-signature `CREATE OR REPLACE` (unlike a `DROP`,
which discards them) but are re-asserted anyway, matching this program's established convention of
never relying on a grant surviving a migration implicitly.

Timestamp must exceed `20260916120000` (the latest migration on `origin/staging` at the time this
change was authored, confirmed live against `origin/staging` during `/review-openspec`). Because
`scripts/lint_migrations.sh` (run in CI as `lint_migrations.sh origin/${{ github.base_ref }}`,
i.e. `origin/staging` for a PR targeting `staging`) only ever compares against `staging`'s state
*at the time each CI run executes* and has no visibility into other open, unmerged migration
PRs, a sibling migration PR merging first with a timestamp between `20260916120000` and this
change's chosen timestamp would only be caught by a *re-run* of that check, not by the original
one — `tasks.md` §5 re-runs `lint_migrations.sh` immediately before merge, not only earlier in
the flow, for exactly this reason.

Rollback (`supabase/rollbacks/`) is `CREATE OR REPLACE` back to the prior body (i.e., step 5
without the fallback block) — not a `DROP FUNCTION`, for the same signature-unchanged reason.
Rollback is safe unconditionally: it only removes the fallback's extra chance to match a row, it
never removes a guarantee anything else depends on. Following this file's own established
precedent for a function-body-only migration (`test_a3_migration_body_is_idempotent` /
`test_a3_rollback_restores_strict_a2`, and `test_scan_status_migration_body_is_idempotent`, all
in `tests/integration/test_cyl_writeback_rpc.py`), this change adds the analogous pair —
`test_redelivery_status_fallback_migration_is_idempotent` and
`test_redelivery_status_fallback_rollback_restores_prior_body` — rather than asserting rollback
safety without a test to back it, which no closer precedent in this file does either.

No table or column changes, and no schema-level `GRANT`/`REVOKE` statement (the kind
`tests/unit/test_schema_usage_grants.py`'s raw-schema-grant regex, scoped to `ON SCHEMA
auth|storage`, is checking for). The function's own `EXECUTE` grants to `bloom_writer`,
`service_role`, `bloom_admin`, and `bloom_workflows` — unaffected by a same-signature `CREATE OR
REPLACE`, which never drops them the way a `DROP FUNCTION` would — are nonetheless re-asserted
explicitly in the migration body, matching this file's own convention of never relying on a grant
surviving implicitly.

## Verification

**Scope limit, stated plainly (caught during `/review-pr`, PR #880):** this fix closes a
re-delivery under a new workflow name **only when the source's original delivery was itself
dispatched through `pipeline.py`** (an ordinary retry, or a re-run of a previously
Bloom-dispatched batch under a fresh `pipeline_run_id`). It does **not** close the case where
the original delivery was a hand-submitted `argo submit` — no `cyl_pipeline_run_scans` row was
ever inserted for that delivery, so `source_id` was never stamped anywhere, and the fallback has
no row to resolve `scan_id` from regardless of how many times it's retried. This is
`test_noop_redelivery_under_never_dispatched_workflow_reports_no_match`'s and
`test_fallback_finds_nothing_for_a_never_dispatched_workflow`'s shape, not
`test_noop_redelivery_under_new_workflow_name_falls_back_to_scan_id`'s.

**Operational consequence:** `sleap-roots-pipeline#56`'s task 7.4b will still fail on the
existing `A4-PIPELINE-E2E-TEST` scans after this change merges — sources 83-90 there all
originated from hand-submitted runs. That task needs fresh synthetic scans regardless (see
below), which independently makes this a non-issue for 7.4b specifically, but anyone expecting
7.4b to go green *because of this merge* will be surprised for the wrong reason otherwise.

A full live re-test (dispatch a real Argo batch, observe `done_count`/`failed_count` after a
re-delivery under a fresh workflow name) cannot pass today, independent of the scope limit above:

- It needs `scan_id`s with no prior envelope. Every scan in `A4-PIPELINE-E2E-TEST`
  (`experiment_id=12880747`) is now either already ingested or is the poison scan
  (`TEST-E2E-007`, `image_ids` resolving to `12894751`).
- Minting new synthetic scans means re-deriving an uploader that writes through `bloom-fs`'s own
  `insert_image_v2_0` + storage-upload path — the prior one-off version of this was never
  committed (roadmap note, 2026-09-01).
- Even with fresh scans, the re-test must dispatch the *original* delivery through `pipeline.py`
  (not a hand-submitted `argo submit`) to exercise the case this fix actually covers — see the
  scope limit above.

So this change's proof lives in `tests/integration/test_cyl_writeback_rpc.py`, which already
seeds `cyl_scans`/`cyl_images`/`cyl_pipeline_runs`/`cyl_pipeline_run_scans` directly against a
local Postgres (no live Argo, no live pipeline needed) and already has the exact fixture helpers
(`_seed_scan`, `_seed_run_scan_for_writeback`, `_call`, `_run_scan_status`) this fix's tests reuse
unchanged. See `tasks.md` for the specific red/green tests, including a negative control that
fails if the fallback block is removed (asserted by re-running the new tests against the
unmodified migration body during review, per `superpowers:test-driven-development`).

Per `/review-pr` history on both sibling changes (and the standing instruction to question
whether a test proves what it claims — see sleap-roots-pipeline#60's substring-check gate that
passed on every exit code): the new tests assert the actual `cyl_pipeline_run_scans.status` and
`.source_id` values after the call, not just the RPC's returned `status_update_matched` boolean,
so a fallback that flips the boolean without actually performing the `UPDATE` would still fail
the test.

## Open Questions

None outstanding for this change's own scope. The cross-repo `pipeline_run_id`-as-parameter
follow-up (Non-Goals) is a candidate for a separate issue, not filed here to avoid scope creep
into a change whose only job is closing bloom#875.
