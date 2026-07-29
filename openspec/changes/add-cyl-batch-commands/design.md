## Context

A4's Argo pipeline (`sleap-roots-pipeline`) runs `download-all → predict-all(warm) → traits →
write-back → notify` per batch (≤ `BATCH_SIZE` scans, chunked by Bloom's `workflows` trigger
route — not built yet, tracked separately as bloom #11). `predict-all` and the traits step are
already batch-shaped (`sleap_roots_predict.batch.run_batch` and
`trait_extractor.extractor.extract_batch` each loop a whole directory in one warm
process). `bloomctl`'s two existing cyl commands are not: `download-for-predict` takes one
`scan_id`, `ingest-result` takes one envelope. This is the last gap before the Argo template's
`download-all` and `write-back` tasks can be wired up (tracked in the roadmap's `images-downloader`
and `write-back` rows, both flagged "Scope pivot 2026-07-24").

Read in full before this design: `bloomcli/src/bloomctl/cyl/download_for_predict.py`,
`bloomcli/src/bloomctl/cyl/ingest.py`, `bloomcli/src/bloomctl/cyl/download.py`,
`sleap-roots-predict/sleap_roots_predict/batch.py`,
`sleap-roots/trait_extractor/extractor.py`, and `sleap-roots-pipeline`'s
`docs/superpowers/specs/2026-07-06-a4-request-driven-pipeline-design.md` §8 + its
`docs/bloom-integration/roadmap.md` 2026-07-24 status-log entry (the actual pivot decision).

## Goals / Non-Goals

**Goals:**
- Stage a batch of scans and write back a batch of envelopes with per-item failure isolation, so
  one bad scan/envelope doesn't abort the other 39/40.
- Match the input/output directory shapes the real upstream batch runners already use, so no
  glue/reshaping step is needed in the Argo template between `bloomctl` and
  `sleap_roots_predict`/`trait_extractor`.
- Match the exit-code/empty-input contract the pipeline's other producers are supposed to follow,
  consistently.
- Zero behavior change to the existing single-scan commands (still the correct per-scan building
  block for manual/debug use per the roadmap).

**Non-Goals:**
- The Argo `WorkflowTemplate`/DAG wiring, `BATCH_SIZE` chunking, and the semaphore — all
  `sleap-roots-pipeline`, not this repo.
- The Bloom `workflows` trigger route and `cyl_pipeline_runs`/`cyl_pipeline_run_scans` tables
  (bloom #11, #404) — separate, already-tracked work; these commands don't read or write those
  tables.
- `MAX_SCAN_ATTEMPTS` retry-then-isolate bookkeeping *across* separate invocations — that's an
  orchestration-layer concern (the pipeline-runs dispatch worker, once built) consuming this
  command's per-item report; this command only isolates failures *within* one invocation.
- Non-interactive/scoped-credential auth (bloom #398) — out of scope, same as the existing
  commands.

## Decisions

- **New per-item core functions, existing commands untouched.** `stage_one_scan` /
  `ingest_one_envelope` are new functions that sequence the same pure helpers
  `download_for_predict`/`ingest_result` already call, but return a `ScanResult` instead of
  raising `click.ClickException`. The existing single-scan commands are not refactored to call
  them. **Alternative considered:** make the existing commands thin wrappers around the new
  non-raising core (better long-term DRY on the ~10-line sequencing wrapper). Rejected for this
  proposal: the existing commands have ~30 tests each asserting exact error-message text and
  ordering (e.g. `test_cli_stale_sidecar_does_not_survive_a_partial_failure_retry`); a refactor
  risks subtle drift for a marginal DRY win on a short wrapper, and the roadmap's own 2026-07-24
  decision explicitly says "no rewrite implied" for the merged single-scan work — Phase 2 loops/
  extends it, doesn't touch it.

- **Batch input formats.** `batch-download-for-predict` takes a JSON-array via `--scan-ids-file`
  (path or `-` for stdin, mirroring `ingest-result`'s existing `<envelope_path_or_stdin>`
  precedent) as the primary mechanism, plus a `--scan-ids 1,2,3` comma-separated convenience flag
  for ad hoc use — mutually exclusive with `--scan-ids-file`. `batch-ingest-result` takes a
  directory (`envelopes_dir`) of `{scan_key}.result.json` files, discovered non-recursively
  (`Path.glob("*.result.json")`, not `rglob`) since `extract_batch`'s own `output_dir` is flat.
  **Alternative considered:** query `cyl_pipeline_run_scans` directly for scan_ids. Rejected: that
  table doesn't exist yet (checked — only appears in draft proposals/docs, no migration).

  **Revised during implementation:** the first draft had `scan_ids_source` as a positional
  argument (`<scan_ids_source> <out_dir>`), with `--scan-ids` as an alternative to it. Building it
  surfaced a genuine Click limitation, confirmed with a minimal repro
  (`@click.argument("a", required=False)` before `@click.argument("b")`, invoked with one
  positional token): Click fills positional slots in declaration order regardless of which one is
  actually required, so an optional positional preceding a required one can be filled instead of
  the required one whenever the optional one is meant to be omitted (e.g. when `--scan-ids` is
  used instead). There is no reliable way to declare "this positional is present only if that flag
  is absent" in Click's argument model. Fixed by making the scan_ids input **entirely
  option-based** (`--scan-ids-file` / `--scan-ids`, mutually exclusive) and `out_dir` the sole
  positional argument — this removes the ambiguity entirely, since there is now only one
  positional slot to fill.

- **`ingest_one_envelope` takes a file path, not a pre-loaded dict — reusing `load_envelope` for
  the read+parse step.** (Revised after review — the first draft had the batch command discover
  paths and hand already-parsed dicts to `ingest_one_envelope`, which left no owner for a
  malformed-JSON file: `discover_envelopes` only lists paths, and nothing between that and
  `ingest_one_envelope` would isolate a per-file read/parse failure.) `ingest_one_envelope(client,
  envelope_path, *, predictions_dir=None)` calls the existing `load_envelope(str(envelope_path))`
  internally (the same helper the single-scan command already uses for its path case) and catches
  `EnvelopeError` as a per-item `failed` `ScanResult` — no new read/parse function needed, and the
  isolation boundary now covers a file that isn't readable or isn't valid JSON, not just a
  file that parses but fails contract validation.

- **Skip-if-done for stage-in, existence-based.** A scan is skipped (`status="skipped"`) if
  `out_dir/{scan_key}/{scan_key}.scan_metadata.json` already exists, parses as JSON, and its
  `scan_key` field matches the directory name — the same validity check
  `sleap_roots_predict.batch._load_scan` itself applies when deciding whether a staged scan is
  usable. This mirrors predict's own skip-if-done today (per the roadmap: "sleap-roots-predict...
  per scan skip-if-done (existence-based resume)") rather than the fuller checksum-verified
  version the A4 design doc envisions but neither sibling producer has built yet (`sleap-roots-
  predict #26`, `sleap-roots #259` are both still open). If the sidecar is missing, unparseable,
  or its `scan_key` doesn't match, the scan is treated as not-done: `clear_scan_dir` + full
  redownload, exactly like the existing single-scan command's unconditional behavior.
  **Not** re-verified against `cyl_images`/checksums — that would require re-downloading to
  verify, defeating the point of skipping. This is intentionally an optimization (avoids wasted
  re-downloads on an Argo `retryStrategy` retry of the whole batch), not a correctness mechanism —
  `predict-all`'s own skip (checked separately, against its own output *and* against an existing
  Bloom source row) is what actually guarantees a re-predicted scan isn't wasted GPU work even if
  stage-in redid something unnecessarily.

- **No skip-if-done for write-back.** Re-ingesting the same envelope is already a benign no-op via
  the RPC's first-writer-wins `idempotency_key` gate (`was_noop=true`) — no additional CLI-level
  skip logic adds anything here.

- **`--predictions-dir` blob upload reuses the single-command's helpers, batch-computes the
  per-scan subdir.** The single `ingest-result --predictions-dir` command expects a flat dir
  containing `{scan_key}.predictions.json` directly (the caller passes the per-scan subdir of
  predict's nested output). The batch command instead accepts predict's own top-level nested
  output root and computes `predictions_dir / scan_key` per envelope before calling the existing
  `load_predictions_manifest`/`build_pending_blobs`/`upload_pending_blobs` unchanged — no changes
  to those helpers' signatures or behavior.

- **Exit-code / empty-input contract matches the documented cross-producer policy.** Empty input
  (no scan_ids / no envelope files found) exits 0 (silent-green no-op). Any item failing makes
  the command exit non-zero. This is the A4 design doc's §8 "Producer Argo-readiness" policy,
  applied here for the first time (see Context) rather than copied from a working implementation
  elsewhere — flagged so a future reader doesn't assume it was validated against a live Argo
  `retryStrategy` retry yet.

- **Two distinct kinds of test, not one — a real-package "oracle" test is not a substitute for an
  always-running isolation test.** (Added after review.) Both new commands need: (a) a test of the
  headline "one bad item doesn't abort the batch" contract that runs unconditionally in CI against
  a mocked client, and (b) a genuine oracle test, gated by `pytest.importorskip` on the real
  upstream package (`sleap_roots_predict` for stage-in, `trait_extractor` for
  write-back), that confirms the produced directory shape is actually accepted by that package.
  These are not interchangeable: `sleap_roots_predict`/`sleap_roots` are dev-machine-only, not
  `bloomctl` runtime dependencies, so (b) self-skips in CI — mislabeling (a) as an "oracle test"
  and gating it the same way would silently drop CI coverage of the batch's core isolation
  guarantee. (`sleap-roots-contracts`, by contrast, *is* a hard `bloomctl` dependency already
  exercised unconditionally elsewhere in the test suite — a batch-ingest test that only needs
  contract validation, not the real `trait_extractor` package, must not be
  `importorskip`-guarded at all.)

- **New shared `_batch.py` module for `ScanResult`/`BatchResult`.** One small new file rather than
  duplicating the dataclass + JSON/human report rendering in both `download_for_predict.py` and
  `ingest.py`. Shape matches `sleap_roots_predict.batch.ScanResult`/`BatchResult` exactly
  (`status`: `ok`/`skipped`/`failed`) rather than `trait_extractor.extractor.BatchResult`'s
  `succeeded`/`failed`-list shape, since the three-state enum is what both new commands need
  (write-back's RPC `was_noop` maps naturally to `"skipped"`; stage-in's resume maps to
  `"skipped"` too) and the traits-side shape can't represent a skip at all.

- **`ScanResult.status` validates at construction, not just a `Literal` type hint.** (Added after
  PR review.) `status: Status` (`Literal["ok", "skipped", "failed"]`) documents the contract
  statically, but nothing runs mypy in this package's CI, so the hint alone enforces nothing at
  runtime. `__post_init__` raises `ValueError` for any other value. Without this, a typo'd status
  string wouldn't just miscount — `BatchResult.ok`'s membership check (`status in ("ok",
  "skipped")`, changed from a negative `!= "failed"` match for the same reason) would silently
  report a genuinely-failed batch as a success.

## Risks / Trade-offs

- **PR review (bloom #532) found and fixed two real bugs post-implementation**, both confirmed by
  direct code reading before fixing, each with a RED-then-GREEN regression test: (1)
  `stage_one_scan`/`ingest_one_envelope` didn't catch unexpected exceptions (network/auth errors
  beyond the specific types already handled), which could crash the whole batch instead of
  isolating one item — the exact guarantee this proposal exists to provide; (2) the
  `--predictions-dir` batch path built a local directory from the envelope's own
  `provenance.scan_key` (producer-supplied, no path-safety constraint), which became the
  containment root passed into `build_pending_blobs`'s own traversal guard, neutralizing it. Both
  now wrap the per-item body in a catch-all and validate `scan_key` before using it as a path
  segment, mirroring `blob_object_path`'s existing guard.
- **Concurrent invocations against the same `out_dir` can race** (PR review finding, deferred as a
  follow-up, not fixed in this proposal): `scan_is_already_staged` → `clear_scan_dir` has no
  lock/lease, so a stale Argo retry pod and a fresh one can both pass the skip check and clobber
  each other's in-progress writes. The right fix belongs with the not-yet-built
  `cyl_pipeline_run_scans` dispatch worker and Argo's `retryStrategy` (the infrastructure meant to
  own retry/attempt semantics) rather than a bolted-on file lock here. Tracked as
  [bloom #533](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/533).
- **Exit-code policy is unvalidated against a real Argo retry yet** (see Decisions) — if the
  eventual Argo wiring needs a different contract, this may need a follow-up change. Mitigated by
  keeping the contract identical to what's already documented as intended for predict/traits, so
  divergence would be a pipeline-wide problem, not specific to this proposal.
- **CLI shape may need to change after Benfica/EPIC #9 review** — per the roadmap, the exact shape
  is explicitly "not decided" at the program level. Mitigated by keeping the new commands additive
  and isolated (new file + two new functions in existing files); a shape change here doesn't
  touch the existing single-scan commands or their tests.
- **PR-time CI does not lint `bloomcli` at all** (confirmed by reading `pr-checks.yml`,
  `.pre-commit-config.yaml`, and `release-bloomcli.yml`) — the only ruff gate on this package runs
  in `release-bloomcli.yml`, after merge, at release-cut time. This is exactly what caused the
  `#521` incident (ruff import-order errors surfacing only at release, right after this same
  package's prior PR merged). Mitigated for this proposal by running `uvx ruff@0.9.9 check
  bloomcli/` locally before every push (tasks.md §8.2) — not by any CI change, since adding a real
  PR-time lint gate for `bloomcli` is a repo-wide CI change out of scope here. Filed as
  [bloom #531](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/531).

## Migration Plan

No data migration. Purely additive CLI surface; ships in the next `bloomctl` release following
the existing `prepare-release-bloomctl` process. No rollback concerns beyond reverting the PR.

## Open Questions

- Final CLI shape sign-off from @blm3886/Bloom EPIC #9 (tracked as a coordination item in
  `proposal.md`, not blocking this proposal's own review).
- Whether `MAX_SCAN_ATTEMPTS` retry-then-isolate bookkeeping ends up needing anything from these
  commands beyond the per-item `BatchResult` (e.g. an `--attempt-count` input) — deferred until
  the `cyl_pipeline_run_scans` dispatch worker (bloom #404) is actually built.
