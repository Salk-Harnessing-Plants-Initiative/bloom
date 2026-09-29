## Context

`bloomctl` has one writer and one reader of the run manifest, and both ship in the same image.

- **The writer:** `cyl batch-download-for-predict` writes into `images-downloader`'s `/workspace/images_input`.
- **The reader:** `cyl batch-ingest-result` reads `write-back`'s `/workspace/input`, which is traits' output directory.
- **Downstream readers:** predict and traits read the manifest and forward it under the name they read. Both have adopted 0.1.0a9 with `allow_legacy=True`.
- **The shared tree:** every Argo workflow uses the same three hostPath directories (`a4_poc/input`, `predictions`, `traits`). No workflow gets a subdirectory of its own.
- **Batching:** one Bloom request becomes ⌈N/25⌉ Workflows (`services/workflows/pipeline.py:311`, `dispatch_worker.py:89-90`), and each has a unique `{{workflow.name}}`.
- **Pipeline shape:** the DAG is linear, so within a workflow each stage runs once, plus `retryStrategy` retries (downloader `limit: 2, retryPolicy: Always`; write-back `limit: 2, retryPolicy: Always`, no `continueOn`).

The design of record is sleap-roots-pipeline `docs/superpowers/specs/2026-09-21-per-run-run-manifest-identity-design.md`, abbreviated **D** below. This change implements D §3.3's two `salk-bloom` rows.

## Decision 1: The file name is keyed to the run id from the environment, not to the stamped id

`resolve_pipeline_run_id()` returns `ARGO_WORKFLOW_NAME` or a fresh `local-<uuid8>`. A reader cannot reproduce another process's placeholder. Naming the file after it would mean `run_manifest.local-ab12cd34.json`, which no reader without a run id looks for, so local scoping would break end to end (D §2.3).

The writer therefore does this:

1. It calls `pipeline_run_id_from_env()` once. That function strips whitespace and returns `None` when the variable is unset or blank.
2. It names the file `run_manifest_name_for_writing(<that value>)`.
3. It stamps `<that value> or f"local-{uuid4().hex[:8]}"` into `pipeline_run_id`.

bloomctl no longer reads `ARGO_WORKFLOW_NAME` itself for this purpose (D §3.2).

The old writer read `ARGO_WORKFLOW_NAME` itself and did not strip it (`download_for_predict.py:455`). Combined with contract naming, given `" wf1\n"`, the file would be named from the stripped form and the unstripped form would be stored inside it, so every downstream reader would raise `RunManifestIdentityError`. Given `"   "`, bloomctl would treat the run as having an id while the contract says it has none.

The name is resolved **after the empty-input return and before authenticating or staging**. `run_manifest_filename` raises `ValueError` for an id that fails `[A-Za-z0-9][A-Za-z0-9._-]*` or is longer than 237 characters. That surfaces as a `ClickException` (exit `1`) before any download work is done, which keeps exit `3` reserved for per-scan failures. An empty `--scan-ids` writes no manifest, so it never resolves a name and keeps the live spec's exit `0` for empty input.

Argo workflow names are DNS-1123 names, so this is defensive: a hand-set bad value fails early instead of after a full batch.

## Decision 2: Overwrite and never union, with or without a run id

The union came from bloom#653. That change assumed the 25-scan chunks of one request share one manifest in one `out_dir`. Per-run names break that assumption, because each chunk is its own Workflow with its own file (D §2.1).

Within one run, the only other writer is a `retryStrategy` re-run of `images-downloader`. There, a union would keep a key from a failed earlier attempt that has no `result.json`. That is bloom#859's latch surviving inside a single run.

So the writer records exactly what this invocation found usable (`ok` ∪ `skipped`), and does not read the existing file at all (D §2.4). An already-staged scan comes back as `skipped` on a retry, so it is still recorded.

The same rule applies with no run id, where the legacy name is written. We chose this deliberately on 2026-09-28, over keeping the union for the legacy name.

- **What it removes:** a second code path, and the local copy of the latch.
- **What it costs:** manually running the command twice into one `out_dir` with disjoint scans now keeps only the second invocation's keys.
- **Who it affects:** no automated caller writes the legacy name. The `local-WSL2-*` templates do not invoke bloomctl.

Because nothing is read any more, a corrupt existing file is simply replaced. The old fail-loud-on-corrupt rule existed to protect an append-only record that no longer exists.

If an invocation finds nothing usable, it still skips the write, because `RunManifest` rejects an empty `scan_keys`. It does not delete an existing same-name file. That file can exist only from an earlier attempt of the same run, and every key in it was staged by that attempt.

**The manifest lock stays** at `out_dir/.locks/manifest.lock`, one lock per directory (D §2.4). Under per-run names it only serializes writers of different files, which costs one fail-fast retry in the rare collision case. Removing it is bloom#894.

## Decision 3: The reader uses `load_run_manifest(..., allow_legacy=True)` and fails loudly once the run id is known

`discover_envelopes` calls `load_run_manifest(envelopes_dir, pipeline_run_id_from_env(), allow_legacy=True)`.

| run id | per-run file | legacy file | result |
|---|---|---|---|
| set | present | any | scope to it. If its `pipeline_run_id` differs, fail loudly (`RunManifestIdentityError`) |
| set | absent | present | scope to legacy. **Warn** if its `pipeline_run_id` differs from the run id |
| set | absent | absent | **fail loudly** (`RunManifestMissingError`), no longer unscoped |
| unset | n/a | present | scope to legacy (unchanged) |
| unset | n/a | absent | unscoped (unchanged; local/dev) |

`allow_legacy=True` matches the other readers during the rollout. The fleet-wide flip to False is sleap-roots-pipeline#82.

The legacy-fallback warning is the only signal a reader gets for the stale-fallback window before row 6 deletes the files. predict and traits emit the same warning.

Every failure keeps the existing `EnvelopeError` → `ClickException` path:

- a dangling symlink or a non-file at a candidate path (`FileNotFoundError` or `OSError`)
- a permission error
- a parse or validation failure

`load_run_manifest` opens files rather than probing them, so an `EACCES` never reads as "absent".

A run id the contract rejects (for example `"../wf"`) raises a bare `ValueError` from `load_run_manifest`. It is mapped to `EnvelopeError` too. Pydantic's `ValidationError` subclasses `ValueError`, so the handler catches the contract's own exceptions first, then `ValidationError`, then `ValueError`.

**Whitespace stripping is scoped to finding the manifest.** Only the manifest lookup goes through `pipeline_run_id_from_env()`. `resolve_argo_workflow_name()`, which feeds `p_argo_workflow_name` to the insert and reconciliation RPCs of both `ingest-result` and `batch-ingest-result`, is left unchanged.

- **Why:** changing it would modify a `cyl-ingest-cli` requirement that the unarchived `fix-cyl-pipeline-run-scan-status` owns.
- **Why it's safe:** Argo workflow names are DNS-1123 and never contain whitespace, so the two agree for every real value.
- **The edge case:** `ARGO_WORKFLOW_NAME="   "` resolves no run identity, so discovery is unscoped, yet it still triggers one reconciliation call under `"   "`. That call matches no rows. The case only arises when someone sets the variable by hand.

With a run identity set, the "no envelopes and no missing keys" branch of `batch_ingest_result` can no longer be reached: a resolved manifest always declares at least one key, and a missing one now fails. The branch keeps its reconciliation call anyway, because the blank-value edge above still reaches it with a truthy `ARGO_WORKFLOW_NAME`, and the spec requires the call there.

**Why fail loudly:** with the id known, a missing manifest means something upstream went wrong. Examples are skewed pins, a downloader that staged nothing, or a failed forward by traits. Unscoped discovery over the shared `traits/` directory would silently widen scope to every run's envelopes, which is worse than the defect being fixed (D §2.2).

## Decision 4: A missing manifest still reconciles before exiting

`fail_cyl_pipeline_run_scans_without_result` currently runs only after discovery succeeds. A discovery error raises before it is reached. With the new missing-manifest failure, a batch whose downloader staged nothing would therefore leave its scans `'queued'` until the status poller's terminal backstop (`status_poller.py:383-386`) closes them.

So on `RunManifestMissingError` (which requires `ARGO_WORKFLOW_NAME` to be set), the command does four things:

1. authenticates;
2. makes the one reconciliation call, isolated exactly as on the normal path;
3. reports a failed batch entry that names the missing manifest;
4. exits `1`. The entry is marked retriable only so that write-back, and with it the Workflow, ends `Failed`.

Argo's `retryStrategy` (`limit: 2`) then repeats the step twice to the same result. That costs two short pod runs. The reconciliation call is idempotent (`WHERE status = 'queued'`).

Reconciliation is **not** added for the other manifest failures: a corrupt file, an identity mismatch, or an `OSError`.

- An `OSError` may be transient. Reconciling first would mark scans `'failed'` that a successful retry would then be unable to update, because the status guard is `status != 'failed'`.
- A corrupt file already failed without reconciling. An identity mismatch and an invalid run id are new failures, and they follow the same rule.
- The poller's backstop still covers all of them.

**Risk: a missing manifest can come from a failed forward by traits, not only from an upstream fault.**

- **The mechanism:** sleap-roots `trait_extractor/extractor.py:428-430` treats forwarding the manifest into `traits/` as best-effort. An `OSError` there is only logged, and traits exits `0`/`3` with real envelopes on disk. predict's forward, by contrast, fails loudly.
- **What write-back does then:** it fails loudly and ingests nothing, and the batch's scans end `'failed'`. That happens whether this command reconciles or the poller's backstop does. The `status != 'failed'` guard then keeps a later manual re-ingest under the same workflow name from updating their status, so recovery is a re-dispatch.
- **Why not handle it here:** it follows from D §2.2's fail-loud rule, not from this change's reconciliation choice. The fix belongs upstream: a sleap-roots follow-up to make traits' forward fail loudly (drafted with this change, posted only with approval).

## Decision 5: Consequences for run status

An all-failed-at-`images-downloader` batch changes outcome.

- **Before (in a directory with no manifest):** no manifest was written, the readers went unscoped, write-back found nothing, and the gate passed the downloader's `3`, so the run read `'complete'` with `done_count = 0`. In the real shared trees, the union writer instead rewrote the stale legacy keys even on an all-failed batch.
- **After (once row 6 has removed the stale legacy files):** predict raises `RunManifestMissingError`, as traits does and as write-back now does too. Write-back has no `continueOn`, so the Workflow ends `Failed`. The run reads `'failed'` with `failed_count = scan_count` after the reconciliation.

This is a truer outcome. A batch where some scans are staged and every staged scan then fails at predict or traits already read `'failed'`: those keys are in the manifest, so write-back reports them missing and exits non-zero. So, for a run with at least one scan, `'complete'` with `done_count = 0` can only come from a stale legacy fallback, and it goes away once row 6 deletes those files. The same statement lives in two specs, `cyl-pipeline-runs` ("`cyl_pipeline_runs` table") and `cyl-pipeline-status-polling` ("A `'complete'` rollup does not imply every scan produced a result"). Both are modified here.

The live `cyl_pipeline_runs` requirement has four bounds, and they become three. The second ("union, never prune") is deleted, and a deterministically failing scan is now excluded from every run's manifest, not only the first one. The fourth (`'complete'` does not imply any scan succeeded) is replaced by "A batch that stages nothing reads `'failed'`".

## Decision 6: One active change per requirement

The unarchived `fix-cyl-pipeline-run-scan-status` modifies two requirements this change also has to modify:

- "Batch ingest-result command ingests every envelope in a directory"
- "`cyl_pipeline_runs` table"

Its `cyl_pipeline_runs` delta was already stale. It predates the exit-gate block and still carries the clause the live spec says not to restore.

Because archiving replaces a requirement wholesale, two active changes on one requirement silently revert each other, and `--strict` cannot see it.

This change therefore takes over both requirements. Its MODIFIED blocks are the live text, plus the other change's intended additions (the end-of-batch reconciliation and `retriable` exit semantics, already implemented by #774), plus this change's own edits. That change's blocks for both requirements are removed.

Its remaining deltas were each compared with the live spec. Only `cyl-trait-writeback` "Write-back RPC ingests a ResultEnvelope" was stale: a later archived change had grown it. It is rebuilt as the live text plus that change's one unarchived scenario, plus one explanatory sentence on `status_update_matched` that the later change had dropped without comment. The `cyl-ingest-cli` and `cyl-pipeline-status-polling` deltas were written against text that is still live, and they are left as they are.

Both changes archive together after row 6's Bloom-dispatched E2E, which also supplies the recorded `done_count`/`failed_count` evidence for that change's tasks 8.2–8.4.

## Rollout and rollback

Merging to `staging` builds `bloomctl:sha-<new>` and moves the mutable `:staging` tag. That tag only affects people running `docker run …:staging` by hand, since no template or automation references `:staging` or `:latest`.

No pipeline runs the new image until row 6 re-pins `images-downloader`, `write-back` and `exit-gate` together in one step. The exit gate runs no bloomctl command; it reuses the image only for its shell. That single pin bump flips the writer and write-back's reader at the same moment.

The pin bump from `sha-28034f6` also ships every bloomcli change merged to staging since then: #880 (write-back redelivery fallback), #882 (an anyio CVE bump in `bloomcli/uv.lock` only), #884 (`cyl create-test-scan`) and #861 (`scrna hdf5`). #880 changes write-back behavior and gets its first live run in row 6's end-to-end test.

No PyPI release is needed, because the templates pin image tags. The next `prepare-release-bloomctl` must still call out the two breaking manifest changes for manual users. Until then, the image reports the unreleased version `0.1.0a6` (PyPI's latest is `0.1.0a5`), so manual `:staging` users can't tell the builds apart from `bloomctl --version`.

Row 6 relies on this guarantee: **with `ARGO_WORKFLOW_NAME` set, this bloomctl never writes the legacy `run_manifest.json`, and never reads it in order to merge.** Only that makes deleting the three stale files permanent.

A rollback means reverting the three template pins in sleap-roots-pipeline and running `argo template update`. Reverting this PR only moves `:staging`. Rolling back to `sha-28034f6` restores the union writer on the legacy name, and also drops #880, #882, #884 and #861.

- The a9 readers accept that file through `allow_legacy=True`.
- Per-run files that are already written are inert, because workflow names never repeat.
- If the stale files have already been deleted, the old writer recreates a fresh legacy file, which then accumulates again. That is what #82 hardens against.
