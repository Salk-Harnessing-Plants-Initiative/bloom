## Context

This change is one driver-side half of a three-part fix; the other two are out of scope here but
shape what "done" means for this piece:

1. **bloom#772** (this change) — give `batch_download_for_predict` a partial-success exit code.
2. **sleap-roots-pipeline#56** (separate repo, not touched here) — wire the `images-downloader`,
   `predictor`, and `trait-extractor` Argo templates' `retryStrategy` to actually consume a
   distinct exit code (retry on a crash-class exit, treat a partial-success exit as terminal so
   the DAG continues). `predictor`/`trait-extractor`'s own drivers (`sleap_roots_predict`,
   `trait_extractor`) already emit `0`/`3`; only the Argo-side wiring for all three is missing,
   confirmed by reading the templates directly (`sleap-roots-trait-extractor-template.yaml` even
   has its own code comment already flagging this, citing
   [sleap-roots#259](https://github.com/talmolab/sleap-roots/issues/259)).
3. **bloom PR #774** (open, in review, branch `eberrigan/fix-cyl-pipeline-run-counts-716`) — an
   unrelated-but-adjacent fix making `cyl_pipeline_run_scans.status`/`cyl_pipeline_runs.done_count`/
   `failed_count` actually get populated from real write-back outcomes.

None of these three lands a fully working poison-scan scenario alone. This change only makes (1)
true; the roadmap's full "partial batch actually completes end-to-end" verification depends on
(2) also shipping.

## Decision 1: Exit code value — `3`, matching predict/traits exactly

Considered:
- **A new bloomctl-specific code** (e.g. `4`, to avoid any appearance of coupling to another
  package's convention).
- **Reuse `3`, mirroring `sleap_roots_predict`/`trait_extractor`'s existing `return 0 if result.ok
  else 3`.**

Chose B. The three producer stages (`images-downloader`/`predictor`/`trait-extractor`) are read by
the same downstream tooling and the same person's mental model when debugging a failed Argo step —
a different convention per stage would be a needless inconsistency with no offsetting benefit.
`sleap-roots-pipeline`'s own roadmap already commits to this exact value in its 2026-09-01
status-log entry, written before this change was scoped. Confirmed via direct grep of the whole
`bloomcli` package: exactly two `ctx.exit()` call sites exist total (this one, and
`batch_ingest_result`'s), both currently `1`; `3` is unclaimed. Click itself reserves `1`
(`ClickException`) and `2` (`UsageError`) — `3` doesn't collide with either.

## Decision 2: Keep this command's contract binary (`0`/`3`), not `retriable`-aware like #774

PR #774 adds a `retriable: bool` field to `_batch.py`'s shared `ScanResult`, and a
`BatchResult.needs_retry` property distinct from `.ok`, so `batch_ingest_result` can exit `0` even
when `.ok` is `False`, provided every failure is a specific non-retriable bookkeeping artifact (a
write that succeeded but whose status-linkage UPDATE was skipped by an idempotency guard — nothing
a retry could ever fix).

That distinction doesn't apply here. Every failure mode `stage_one_scan` can currently produce (a
scan not found, zero frames, invalid frame_numbers, a metadata-resolution failure, a partial
frame-download failure, or lock contention) is, at least in principle, retriable — a transient
network blip, a lock held by another live invocation, or even the "poison scan" case itself (which
a human might legitimately fix by re-uploading the missing image data and re-running the batch).
Introducing a `retriable` field to this command with no failure mode that's ever actually `False`
would add a parameter with no observable behavior difference — the same "no reachable benefit" that
`fix-bloommcp-resultstore-error-swallowing`'s proposal reasoned about for adding unreachable
exception types.

If a genuinely non-retriable failure mode is ever added to `stage_one_scan` in the future (unlikely
given today's list, but not impossible), extending this command's exit-code contract to a third
value using `_batch.py`'s existing `retriable`/`needs_retry` fields (once #774 merges them) would
be the natural next step — deliberately deferred, not designed away.

## Decision 3: Why `ingest.py` isn't touched here, and how the two commands' contracts differ without conflicting

`batch_ingest_result` (`ingest.py:797`) has the identical `if not batch_result.ok: ctx.exit(1)`
shape today, and per bloom#772's own text and the `sleap-roots#259` code comment, the
`trait-extractor` Argo template already needs the same "distinguish partial from crash" fix. It
would be tempting to fix both commands together. Not done here, for one concrete reason: PR #774 is
already mid-review, actively changing that exact line (to `if batch_result.needs_retry:
ctx.exit(1)`) as part of a larger, already-scoped change with its own accepted risk/design
tradeoffs (see `openspec/changes/fix-cyl-pipeline-run-scan-status/design.md` on that branch, or
[bloom PR #774](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/774) if that local
path is unavailable — e.g. after the branch merges/renames or if this reader is on a different
checkout). Touching the same
line in a second, concurrent change would create a real merge conflict and duplicate review
surface over the same few lines, for no benefit — `batch_ingest_result`'s partial-success signaling
is #774's problem to solve, already in flight.

This does leave a temporary, explicit inconsistency: after this change merges,
`batch_download_for_predict` signals partial success via `0`/`3`, while `batch_ingest_result`
(post-#774) signals it via `0`/`1` gated on `needs_retry` rather than a distinct non-1 code. This
is a real observation for `sleap-roots-pipeline#56`'s Argo-wiring session to reconcile against the
`trait-extractor` template's own already-filed gap — not something this change can resolve unless
it also modified `ingest.py`, which is explicitly out of scope (Non-Goals). If, once #774 merges,
someone wants both commands to converge on one exit-code shape, that's a natural small follow-up —
tracked here as a note, not a task.

## Decision 4: Per-scan run-level status marking — defer entirely to PR #774, no new mechanism

The pipeline#56 issue text raises the question of how the poison scan actually gets marked
`failed` at the run level (not just observed as "never staged" via the absence of its directory).
Read PR #774's diff directly (not just its description) to confirm the mechanism it introduces
would already cover this case once the DAG can reach write-back:

- `batch_ingest_result` calls a new `fail_cyl_pipeline_run_scans_without_result` RPC once per
  batch, keyed by `ARGO_WORKFLOW_NAME` (not by which envelopes happen to be present in
  `envelopes_dir`) — it closes out, as `failed`, **any** scan dispatched under that workflow name
  that write-back never resolved either way, for any reason: a prediction failure before write-back
  was attempted, an envelope that was never produced, or — the case this change enables — a scan
  that never made it past `images-downloader` at all and so never even reached `predictor`.
- This is workflow-scoped, not envelope-scoped, so it does not need to know *why* a given scan's
  result never showed up; a staging-stage failure is indistinguishable, to that RPC, from a
  prediction-stage failure. No change to `download_for_predict.py`, `_batch.py`, or any RPC is
  needed on this side for run-level status to eventually reach `failed` for the poison scan — it
  falls out of #774's existing mechanism for free, once #772 + pipeline#56 let the DAG reach
  write-back at all for a partial batch.

Consequence: this change's own scope stays exactly "exit code + docs + tests" for
`download_for_predict.py`. The run-level status-marking question pipeline#56 raises is already
answered by code in flight elsewhere, not a gap this change needs to fill.

**Caveat (found during `/review-pr`):** the paragraph above is read directly off PR #774's diff as
of this writing, but that PR is open and unmerged — this change has no test or other verification
surface of its own that would catch it if #774's RPC-selection logic changes shape before merging
(e.g. if it stops being purely `ARGO_WORKFLOW_NAME`-scoped). Treat this as a reasoned assumption
about in-flight code, not a verified guarantee; worth re-confirming against #774's actual merged
state before relying on it operationally.
