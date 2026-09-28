## Why

[bloom#772](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/772) — found via a
live poison-scan batch-oracle test on staging (2026-09-01,
`sleap-roots-pipeline` workflow `sleap-roots-pipeline-jqsf9`): a batch of 3 scans, 1 deliberately
pointing at image data that was never uploaded. `stage_one_scan` correctly isolated the failure
per-scan — the 2 good scans got fully staged (real images + `scan_metadata.json` present on the
shared NFS mount), the poison scan's directory was never created. But the batch still ended
`Failed` at 0/3 progress, because `batch_download_for_predict`
(`bloomcli/src/bloomctl/cyl/download_for_predict.py:641-642`) does:

```python
if not result.ok:
    ctx.exit(1)
```

— exit 1 whenever **any** scan failed, with no distinction from "everything failed." Argo's
`images-downloader` template's `retryStrategy` (`limit: 2`, `retryPolicy: Always`) sees any
non-zero exit as a failed step and retries the **entire batch** (the poison scan fails identically
every retry), then marks the whole step — and the whole DAG — `Failed`. `predictor`/
`trait-extractor`/write-back never run at all, for any scan in the batch, including the 2 that
were already correctly staged and ready. This directly contradicts the A4 design doc's
"retry-then-isolate" requirement (`sleap-roots-pipeline/docs/superpowers/specs/2026-07-06-a4-request-driven-pipeline-design.md`
§8/§14: a genuinely-failing scan should be marked failed and the batch should continue, ending
`partial`).

`sleap_roots_predict/__main__.py` and `sleap-roots`' `trait_extractor/__main__.py` already do
`return 0 if result.ok else 3` — a real partial-success exit code, distinct from a crash.
bloomctl has no equivalent for this command. `sleap-roots-pipeline`'s own roadmap
(`docs/bloom-integration/roadmap.md`, 2026-09-01 status-log entry) already commits to giving
`batch_download_for_predict` this exact `0`/`3` convention as the fix, so that the companion issue
[sleap-roots-pipeline#56](https://github.com/talmolab/sleap-roots-pipeline/issues/56) (wiring
Argo's `retryStrategy` to actually consume the distinction — a separate repo, not in scope here)
has something to act on.

## What Changes

- `batch_download_for_predict` exits `0` when every scan in the batch succeeded, was skipped, or
  the input was empty (unchanged), and exits `3` (not `1`) when at least one scan failed — mirrors
  `sleap_roots_predict`/`trait_extractor`'s existing `0 if result.ok else 3` convention exactly.
  Exit code `3` is confirmed unclaimed in bloomctl today (the only two `ctx.exit()` call sites in
  the whole CLI are this one and `batch_ingest_result`'s, both currently `ctx.exit(1)`) and doesn't
  collide with Click's own reserved `1` (`ClickException`) or `2` (`UsageError`).
- No other exit path in this command changes. Usage errors (`--scan-ids-file`/`--scan-ids` both
  given or both omitted, a non-finite `--lock-staleness-seconds`, malformed scan_ids input),
  manifest-lock contention, and manifest-write failures all already raise `click.ClickException`/
  `click.UsageError` directly, independent of `result.ok` — these stay at their existing exit codes
  (`1`/`2`).
- Update the three places that currently describe this command's exit behavior with the vague
  "exits non-zero" phrasing to state the concrete `0`/`3` contract: the command's own docstring
  (`download_for_predict.py`), `bloomcli/README.md`'s "Exit code:" bullet for
  `batch-download-for-predict`, and a new `bloomcli/CHANGELOG.md` entry under `Unreleased` (the
  original shipped-feature entry describing the old "non-zero" behavior is left as an accurate
  historical record, not edited).
- Update the `cyl-batch-download-for-predict` spec's "One scan's failure is isolated, not fatal to
  the batch" requirement (and its dependent scenarios) to state exit code `3` specifically, in
  place of "non-zero."
- Tighten the 8 existing tests in `bloomcli/tests/test_cyl_download_for_predict.py` that currently
  assert `result.exit_code != 0` for the `result.ok is False` path to assert `== 3` instead,
  proving the new code, not just "some" non-zero code. Also tighten 1-2 of the existing
  usage-error/manifest-lock tests (which today only assert `!= 0`) to assert `== 1` specifically —
  a negative test proving the new `3` path doesn't leak into the pre-existing `ClickException`
  paths.

**Not marked BREAKING.** This does change the literal exit-code value returned for a partial-batch
failure (`1` → `3`), which is technically an external-contract change for a shipped CLI command
(`bloomctl` `0.1.0a6` is already published to PyPI with the old contract). It isn't marked
**BREAKING** because no consumer today — in this repo or in `sleap-roots-pipeline` — branches on
the specific value `1` versus any other non-zero code; Argo's `retryStrategy` (the only real
external consumer) currently treats *any* non-zero exit identically (retry-then-fail-whole-step)
until `sleap-roots-pipeline#56`'s wiring lands. Until then, this change is behaviorally inert in
production: a poison-scan batch still ends up fully retried and `Failed`, exactly as before —
`bloom#772` should stay open after this PR merges, not get auto-closed, since the issue's actual
symptom isn't fixed until the companion Argo-wiring change also ships. Should still be called out
in `bloomctl`'s next version bump/release notes as an external-contract change, even though nothing
in this repository depends on the old value.

## Non-Goals

- **`bloomctl cyl ingest-result`/`batch-ingest-result` (`bloomcli/src/bloomctl/cyl/ingest.py`) are
  untouched.** PR #774 (open, in review) is actively reworking that command's exit-code logic with
  a `retriable`/`needs_retry` distinction (a different axis — "is this failure worth an automatic
  retry," not "did some scans succeed while others failed"). Reworking it here too would conflict
  with in-flight review. See `design.md` for why the two commands' contracts are allowed to diverge
  for now.
- **No new per-scan status-marking mechanism** (e.g. writing to `cyl_pipeline_run_scans` from this
  command). PR #774 already adds a `fail_cyl_pipeline_run_scans_without_result` RPC, called from
  `batch_ingest_result` at the end of a write-back batch and keyed by `ARGO_WORKFLOW_NAME` (not by
  envelope) — once a partial batch's DAG can actually reach write-back (this fix, plus the separate
  Argo-wiring change in `sleap-roots-pipeline#56`, is what makes that possible), that existing
  mechanism will already correctly mark an isolated-failed scan (staged-but-never-resulted, for any
  reason, including a staging-stage failure) as `failed` at the run level. See `design.md`.
- **No Argo template changes.** Wiring `images-downloader-template.yaml`'s `retryStrategy` to
  actually consume exit code `3` differently from a crash-class exit is
  `sleap-roots-pipeline#56`, a separate repo not touched by this change.
- **No change to which failures are retriable vs. not within this command.** Unlike `ingest.py`'s
  `status_update_matched` case, no failure mode in `stage_one_scan` today is inherently
  non-retriable — every existing failure (a transient RPC/network error, a missing scan, lock
  contention, a corrupt sidecar) could plausibly succeed on a re-run. A single two-value exit
  contract (`0`/`3`) is sufficient; no `retriable` field is added to `ScanResult`/`BatchResult` for
  this command.

## Impact

- **Affected specs:** `cyl-batch-download-for-predict` (MODIFIED — "One scan's failure is isolated,
  not fatal to the batch").
- **Affected code:** `bloomcli/src/bloomctl/cyl/download_for_predict.py` (one exit-code branch +
  docstring wording), `bloomcli/README.md`, `bloomcli/CHANGELOG.md`.
- **Affected tests:** `bloomcli/tests/test_cyl_download_for_predict.py` (8 existing assertions
  tightened from `!= 0` to `== 3`; no new test files).
- **Dependencies:** none — no schema/migration change, no new package dependency, no change to
  `_batch.py`'s shared `ScanResult`/`BatchResult` classes (which `ingest.py` also uses).
- **Branch/PR:** branches off `origin/staging`; PR targets `staging`. Recommend referencing
  `Related to #772` (not `Fixes`) in the PR body — this closes the driver-side half; the issue's
  full poison-scan scenario isn't verified end-to-end until `sleap-roots-pipeline#56`'s Argo wiring
  also lands, so leave #772 open for the follow-up session to close once that companion fix ships
  and the live re-run confirms a `partial` run end-to-end.
