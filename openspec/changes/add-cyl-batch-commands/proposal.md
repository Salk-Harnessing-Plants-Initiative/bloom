## Why

A4's per-batch pipeline pivot (`sleap-roots-pipeline` roadmap, 2026-07-24 status log: "A4/A2
batch-vs-single-scan architecture reconciled: Option 2 (full batch pivot) chosen") needs Argo
`download-all` and `write-back` tasks that stage in / write back a whole batch of scans in one
warm process — mirroring `predict-all`'s single warm pod per batch. Today's `bloomctl cyl
download-for-predict`/`ingest-result` are single-scan shaped (one `scan_id` in, one envelope in),
so those two Argo tasks have no CLI to call without either an N-process shell loop (heavier:
N cold Python/import/auth cycles) or new commands. This proposal adds the two missing
batch-capable commands.

## What Changes

- Add `bloomctl cyl batch-download-for-predict <out_dir> --scan-ids-file <path_or_dash>` — stages
  every `scan_id` from a JSON array (a file path, or `-` for stdin, via `--scan-ids-file`) into
  the nested `out_dir/{scan_key}/` layout `sleap_roots_predict.discover_scans` expects. A
  `--scan-ids 1,2,3` comma-separated flag is also accepted for ad hoc manual use, mutually
  exclusive with `--scan-ids-file`. (Revised from an initial `<scan_ids_source> <out_dir>`
  positional-argument shape after implementation surfaced a genuine Click limitation: an optional
  positional preceding a required one can't be reliably filled or omitted — verified with a
  minimal repro — so both scan_ids inputs are options and `out_dir` is the sole positional.) Skips
  a scan whose stage directory already has a valid sidecar (existence-based resume, mirroring
  `sleap_roots_predict.batch`'s own skip-if-done convention). Isolates per-scan failures (one bad
  scan doesn't abort the batch) and reports an aggregate result.
- Add `bloomctl cyl batch-ingest-result <envelopes_dir>` (+ optional `--predictions-dir`) —
  ingests every `{scan_key}.result.json` file in a flat directory (the exact shape
  `trait_extractor.extract_batch`'s `output_dir` produces). When `--predictions-dir`
  is given, constructs + uploads blobs per envelope from predict's nested batch output
  (`predictions_dir/{scan_key}/{scan_key}.predictions.json`), reusing the existing
  `load_predictions_manifest`/`build_pending_blobs`/`upload_pending_blobs` helpers unchanged.
  Isolates per-envelope failures and reports an aggregate result.
- Both commands: a `--json` flag emits the full aggregate result (`BatchResult`, one `ScanResult`
  per item — `status`: `ok`/`skipped`/`failed`) for machine consumption; the default is a
  human-readable summary. Exit code: **non-zero if any item failed, zero on empty input or all
  ok/skipped** — the exit-code/empty-input policy documented in `sleap-roots-pipeline`'s A4
  design doc (§8 "Producer Argo-readiness"), applied to bloomctl as the third/fourth producer in
  the chain (predict/traits' own implementations of this policy — `sleap-roots-predict #26`,
  `sleap-roots #259` — are still open, so this is the first place it actually lands, not
  precedent this proposal is copying from working code).
- **Existing `download-for-predict` and `ingest-result` commands are untouched** — no behavior
  change, no shared runtime coupling. The new batch commands get their own non-raising per-item
  core functions (`stage_one_scan`, `ingest_one_envelope`) that sequence the same pure helpers the
  existing commands already call; they do not refactor the existing raising commands to use them.

## Impact

- **New capabilities**: `cyl-batch-download-for-predict`, `cyl-batch-ingest-result`.
- **Affected code**:
  - `bloomcli/src/bloomctl/cyl/download_for_predict.py` — new `stage_one_scan`, `read_scan_ids`,
    `scan_is_already_staged`, a `--scan-ids` comma-separated parser, and the
    `batch_download_for_predict` command.
  - `bloomcli/src/bloomctl/cyl/ingest.py` — new `ingest_one_envelope`, `discover_envelopes`, and
    the `batch_ingest_result` command.
  - `bloomcli/src/bloomctl/cyl/_batch.py` (new file) — shared `ScanResult`/`BatchResult`
    dataclasses and JSON/human-readable report rendering, used by both new commands.
  - `bloomcli/src/bloomctl/cyl/__init__.py` — register the two new commands; also fixes a
    pre-existing gap in the module's "one file per entity" docstring catalog (it currently omits
    `download_for_predict.py` from an earlier merged proposal), found during this proposal's
    review.
- **No server/RPC/schema changes.** No changes to the `cyl-download-for-predict`, `cyl-ingest-cli`,
  or `cyl-trait-writeback` capabilities — this proposal only adds new capabilities alongside them.
- **Coordination**: the CLI surface (naming, argument shape, input format) is Bloom-side
  implementation detail per the roadmap's canonical-scope split ("Bloom impl detail is Benfica's
  call") — get @blm3886/Bloom EPIC #9 a heads-up on this proposal before merge, even though this
  session has commit access.
- **Tracking**: [bloom #529](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/529)
  (bloom #11 covers the trigger route, bloom #404 covers the tables/dispatch worker, neither
  covered these CLI commands until now).
- **Out of scope**: the Argo `WorkflowTemplate` wiring (BATCH_SIZE chunking, semaphore, the actual
  `download-all`/`write-back` DAG tasks calling these commands) lives in `sleap-roots-pipeline`,
  not this repo. The Bloom `workflows` trigger route and `cyl_pipeline_runs`/`_scans` tables
  (bloom #11, #404) are separate, already-tracked pieces of work.
