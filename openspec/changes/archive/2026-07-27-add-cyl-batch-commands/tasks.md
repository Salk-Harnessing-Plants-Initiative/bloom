TDD throughout: write the failing test first, confirm RED, then implement to GREEN. This is a
staging-first, protected repo — every pushed commit must keep CI green, so new tests and the code
they exercise land **in the same commit**. Do not touch `download_for_predict`'s or `ingest_result`'s
existing command functions or their tests — this change is purely additive.

Section 2 (`_batch.py`) is a hard prerequisite for sections 3-6: both `ScanResult`/`BatchResult`
are imported by `stage_one_scan` and `ingest_one_envelope`. Land section 2 in its own commit
before starting section 3 or 5 — working out of order would surface as an `ImportError`, not a
clean assertion failure. Sections 3-4 and 5-6 are otherwise independent of each other and may be
worked in parallel.

Each numbered section below (2, 3, 4, 5, 6) is one commit boundary: its RED tasks and its GREEN
task land together, per the TDD-same-commit rule stated above — do not split a section's RED and
GREEN tasks across separate pushes. Section 8 ("Verify") is not a commit boundary — it's pre-merge
checks against the branch as a whole, plus PR-description content.

## 1. Proposal & specs

- [x] 1.1 `openspec validate add-cyl-batch-commands --strict` passes (proposal.md, design.md,
      tasks.md, both spec deltas already written).
- [x] 1.2 Open the PR and tag @blm3886 (Benfica) for CLI-shape feedback before merge (per the
      roadmap's canonical-scope split — Bloom impl detail is her call) — opened as
      [bloom #532](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/532), Benfica
      added as reviewer. This is a courtesy heads-up, distinct from 8.5 below.
- [x] 1.3 File the Bloom EPIC #9 sub-issue for "batch-capable bloomctl commands" — filed as
      [bloom #529](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/529).

## 2. Shared `_batch.py` module (`ScanResult`/`BatchResult`)

- [x] 2.1 RED: `bloomcli/tests/test_cyl_batch.py` — `ScanResult(scan_key, status, error="")`;
      `status` accepts `ok`/`skipped`/`failed`.
- [x] 2.2 RED: `BatchResult.ok` is `True` iff no scan has `status == "failed"` (skipped/ok don't
      affect it); `True` for an empty `scans` list.
- [x] 2.3 RED: a human-readable summary renderer (`format_summary(result, noun="scan")` or
      similar) — e.g. `"Staged 39/40 scans -> out_dir (1 failed)"` — and a JSON renderer that
      round-trips every field (`scan_key`, `status`, `error`).
- [x] 2.4 GREEN: implement `bloomcli/src/bloomctl/cyl/_batch.py` to pass 2.1-2.3.

## 3. `batch-download-for-predict` — pure helpers

- [x] 3.1 RED: `read_scan_ids(source, *, stdin=None)` — parses a JSON array of integers from a
      path or `-`. Cover explicitly: valid array (success); empty array (success — the no-op
      case, not an error); path does not exist; path is a directory; content is not valid JSON;
      content parses to something other than an array (object/string/number); array contains a
      non-integer element. All error cases raise a readable error, never a bare exception.
- [x] 3.2 RED: `--scan-ids 1,2,3` comma-separated parsing (int-coerced, rejects non-numeric with a
      readable message) — mutually exclusive with `--scan-ids-file` (cover both-given and
      neither-given as two distinct cases).
- [x] 3.3 RED: `scan_is_already_staged(scan_dir, scan_key)` — `True` iff the sidecar exists,
      parses as JSON, and its `scan_key` field matches; `False` for missing/unparseable/mismatched
      sidecar (all three cases explicitly, not just "sidecar exists").
- [x] 3.4 RED: `stage_one_scan(client, scan_id, out_dir)` returns a `ScanResult` (never raises) for
      each of: scan not found; zero frames; invalid frame_numbers (null/duplicate); metadata
      resolution failure; partial frame-download failure; already-staged (skip); full success.
      Reuse the existing pure helpers (`fetch_scan`, `fetch_images`, `validate_frame_numbers`,
      `resolve_sidecar_params`, `clear_scan_dir`, `download_frames_for_predict`, `build_sidecar`,
      `write_sidecar`) — do not duplicate their logic.
- [x] 3.5 GREEN: implement 3.1-3.4 in `download_for_predict.py`.

## 4. `batch-download-for-predict` — command wiring

- [x] 4.1 RED: CLI happy path — N scan_ids from a JSON file, all stage successfully, exit 0,
      `--json` output round-trips a `BatchResult` with N `ok` entries. Registering the command in
      `cyl/__init__.py` is required for this (and every other CLI-level test below) to run at
      all — do that as part of 4.8's GREEN step, not as a separate later task.
- [x] 4.2 RED: **isolation test (always runs, mocked client — no `importorskip`)** — a batch of 3
      scan_ids where one has zero frames → 2 staged, 1 `failed` and named in the report, exit code
      non-zero, the other 2 scans' output directories are intact. This is the test of the batch's
      core contract and MUST run unconditionally in CI.
- [x] 4.3 RED: **oracle test (dev-machine only, `pytest.importorskip("sleap_roots_predict")`)** —
      same 3-scan-with-one-bad-scan setup as 4.2, but additionally asserts
      `sleap_roots_predict.discover_scans` accepts the 2 successfully staged scans — mirrors the
      existing single-command's `test_oracle_sidecar_is_accepted_by_discover_scans` convention.
      This is a real-package acceptance check layered on top of 4.2, not a replacement for it.
- [x] 4.4 RED: empty scan_ids array → exit 0, no output directory created, `BatchResult.scans == []`.
- [x] 4.5 RED: malformed/unreadable `--scan-ids-file` value at the CLI level (nonexistent path,
      directory path, non-JSON content, non-array content) → exits non-zero with a readable
      message (not a traceback), and stages nothing — mirrors `test_cli_bad_json_makes_no_call`'s
      convention of asserting no auth/download call happened.
- [x] 4.6 RED: `--scan-ids-file -` (stdin) works identically to a file path.
- [x] 4.7 RED: `--scan-ids` convenience flag works and is mutually exclusive with `--scan-ids-file`
      (both-given and neither-given each produce a `UsageError`, as two separate tests). NB:
      `out_dir` is the command's only positional argument — both scan_ids inputs are options (see
      design.md's "Revised during implementation" note on why an optional-before-required
      positional pair doesn't work in Click).
- [x] 4.8 RED: a scan already staged from a prior run is reported `skipped`, not re-downloaded
      (assert the fake storage client's `download` is never called for that scan's frames).
- [x] 4.9 RED: a batch with a mix of ok/skipped/failed scans — assert `--json` output lists all
      three statuses correctly, and the default human-readable output names the failed scan by
      `scan_key` and error (this is the CLI-level test the `--json`/default-output spec scenarios
      actually need — the `_batch.py` unit tests in section 2 only cover the renderer in
      isolation, not the wired command's real output).
- [x] 4.10 RED: `--profile` passes through, same as the existing command.
- [x] 4.11 GREEN: implement the `batch-download-for-predict` command in `download_for_predict.py`
      AND register it in `bloomcli/src/bloomctl/cyl/__init__.py` (needed for 4.1-4.10's
      `CliRunner` calls to resolve the command at all — do this in the same commit).

## 5. `batch-ingest-result` — pure helpers

- [x] 5.1 RED: `discover_envelopes(envelopes_dir)` — non-recursive glob for `*.result.json`,
      sorted. Cover explicitly: files present (returns them); empty-but-present directory (returns
      `[]` — the no-op case, not an error); directory does not exist; path exists but is a file,
      not a directory. Both error cases raise a readable error.
- [x] 5.2 RED: `ingest_one_envelope(client, envelope_path, *, predictions_dir=None)` returns a
      `ScanResult` (never raises) for each of: the file is unreadable or not valid JSON (via the
      existing `load_envelope(str(envelope_path))` — reused as-is, not re-implemented, so this
      isolates a bad file the same way the single-scan command already isolates a bad path/stdin
      input, just without raising); envelope fails contract validation; envelope is missing/has an
      empty `provenance.idempotency_key` when `predictions_dir` is given (mirrors the single
      command's regression-tested `test_cli_predictions_dir_missing_idempotency_key_fails_actionably`
      case); RPC raises a mapped error; RPC returns `was_noop=true` (→ `status="skipped"`);
      success; when `predictions_dir` given — missing manifest, blob upload failure, successful
      blob construction+upload+merge before the RPC call. Reuse the existing pure helpers
      (`load_envelope`, `validate_envelope`, `load_predictions_manifest`, `build_pending_blobs`,
      `upload_pending_blobs`, `call_insert_envelope`, `map_rpc_error`, `summarize_result`) — do
      not duplicate their logic.
- [x] 5.3 GREEN: implement 5.1-5.2 in `ingest.py`.

## 6. `batch-ingest-result` — command wiring

- [x] 6.1 RED: CLI happy path — N envelope files in a directory, all ingest successfully, exit 0,
      `--json` output round-trips a `BatchResult` with N `ok` entries. Registering the command in
      `cyl/__init__.py` is required for this (and every other CLI-level test below) — do that as
      part of 6.9's GREEN step, not as a separate later task.
- [x] 6.2 RED: **isolation test (always runs — no `importorskip`; `sleap-roots-contracts` is
      already a hard `bloomctl` dependency, exercised unconditionally elsewhere in the suite)** —
      a batch of 3 envelope files where one fails `sleap-roots-contracts` validation → 2 ingested,
      1 `failed` and named in the report, exit code non-zero, the other 2 envelopes' RPC calls
      still happened (isolation, not abort). Do NOT gate this with `pytest.importorskip` — it does
      not need the real `trait_extractor` package, only `sleap-roots-contracts`.
- [x] 6.3 RED: **oracle test (dev-machine only, `pytest.importorskip("trait_extractor")`)**
      — verifies `discover_envelopes`'s flat, non-recursive glob assumption against the real
      `trait_extractor.extractor.extract_batch`'s actual `output_dir` layout (mirrors
      the symmetric acceptance check on the stage-in side, task 4.3).
- [x] 6.4 RED: a malformed envelope file (not valid JSON) among 3 in the directory → 2 ingested,
      the malformed one `failed` (named by filename, since no `scan_key` could be read from it),
      exit code non-zero.
- [x] 6.5 RED: empty directory → exit 0, `BatchResult.scans == []`, no RPC calls made.
- [x] 6.6 RED: `envelopes_dir` does not exist, or is a file rather than a directory → exits
      non-zero with a readable message, no RPC calls made.
- [x] 6.7 RED: a re-ingested (already-ingested) envelope reports `status="skipped"` via
      `was_noop`, does not count as a failure.
- [x] 6.8 RED: `--predictions-dir` — happy path uploads blobs for every envelope whose
      `predictions_dir/{scan_key}/{scan_key}.predictions.json` exists; a missing manifest for one
      scan_key isolates that scan as `failed` without aborting the others; an envelope with an
      empty/absent `idempotency_key` when `--predictions-dir` is given isolates as `failed` with
      an actionable message (per 5.2).
- [x] 6.9 RED: a batch with a mix of ok/skipped/failed envelopes — assert `--json` output lists
      all three statuses correctly, and the default human-readable output names the failed
      envelope by `scan_key` and error.
- [x] 6.10 RED: `--profile` passes through, same as the existing command.
- [x] 6.11 GREEN: implement the `batch-ingest-result` command in `ingest.py` AND register it in
      `bloomcli/src/bloomctl/cyl/__init__.py` (needed for 6.1-6.10's `CliRunner` calls to resolve
      the command at all — do this in the same commit).

## 7. Docs & changelog

- [x] 7.1 Add a `### Added` heading under `[Unreleased]` in `bloomcli/CHANGELOG.md` (none exists
      yet — check before assuming one does) with one bullet per new command
      (`batch-download-for-predict`, `batch-ingest-result`), matching the existing
      `download-for-predict`/`ingest-result` entries' level of detail (command syntax, input/output
      shape, exit-code behavior); reference bloom #529.
- [x] 7.2 Update `bloomcli/README.md`: add both new commands to the "## Commands" bullet list, and
      add dedicated `## bloomctl cyl batch-download-for-predict` / `## bloomctl cyl
      batch-ingest-result` sections matching the shape of the existing `download-for-predict`/
      `ingest-result` sections (usage, behavior, examples) — same convention
      `add-cyl-download-for-predict`'s proposal required.
- [x] 7.3 Update the module docstring in `bloomcli/src/bloomctl/cyl/__init__.py` (the "one file
      per entity" command catalog) to list `_batch.py` and both new commands. While here, also
      add the currently-missing `download_for_predict.py` entry — a pre-existing drift bug from an
      earlier merged proposal, found during this proposal's review; fixing it now stops the same
      drift from recurring for a third time.

## 8. Verify

- [x] 8.1 `uv run --extra test pytest` green in `bloomcli/` (full suite, not just the new files) —
      271 passed, 2 failed, 6 skipped (corrected wording — a PR-review finding caught the original
      note here conflating failures with skips). The 2 failures are pre-existing and
      environment-specific, unrelated to this change and in files it doesn't touch:
      `test_saved_file_is_owner_only` (POSIX file-mode check, doesn't apply on this Windows dev
      machine) and `test_list_renders_rows` (rich-table terminal-width truncation on this
      machine) — confirmed failing identically before this branch's work started. The 6 skips are
      the 4 oracle tests plus 2 pre-existing `psycopg`-import skips
      (`test_cyl_ingest_integration.py`, `test_dev_stack_smoke.py`), also unrelated.
- [x] 8.2 Run `uvx ruff@0.9.9 check bloomcli/` locally before every push. **This is not redundant
      with CI** — `pr-checks.yml` runs no lint step at all for `bloomcli`, and
      `.pre-commit-config.yaml`'s `ruff-format`/`black` hooks exclude `bloomcli/`; the only ruff
      gate is in `release-bloomcli.yml`, which runs after merge at release-cut time. This is the
      exact gap that caused the `#521` incident — do not skip this step assuming CI will catch it.
      Result: all checks passed.
- [x] 8.3 Manually run all oracle tests and confirm no regression; paste the passing output into
      the PR description (same convention as `add-cyl-download-for-predict`'s manual oracle note).
      **Actually run, not just attempted** — the first pass wrongly concluded "environment-limited"
      by trying to inject deps into bloomcli's own minimal venv (which deliberately doesn't carry
      them). The real fix: `sleap-roots-predict`/`sleap-roots` already have their own `.venv`s
      checked out alongside this repo (`c:\repos\sleap-roots-predict`, `c:\repos\sleap-roots`),
      each missing only 1-2 packages (`python-dotenv`; `sleap-roots-contracts` pinned older than
      bloomcli's floor). Installed those into each sibling venv, ran the real test files with that
      interpreter (`PYTHONPATH` pointed at `bloomcli/src`), then restored both venvs to their own
      lockfile-pinned state via `uv sync` afterward (confirmed clean — no residue left in either
      sibling repo). Results: `test_cyl_download_for_predict.py` 88/88 passed against the real
      `sleap_roots_predict` (all 3 oracle-gated tests included, not just the 2 new batch ones);
      `test_cyl_ingest.py` 118/118 passed against the real `trait_extractor` (needed bumping that
      venv's `sleap-roots-contracts` copy to `0.1.0a5` first — reverted after). Genuine end-to-end
      confirmation that both new commands produce output the real upstream packages accept.
- [x] 8.4 `/review-openspec` before requesting approval (done pre-implementation, all findings
      fixed — see the earlier "Revised during implementation"/"Added after review" annotations
      throughout this file and design.md); `/review-pr` (5-subagent) run on
      [bloom #532](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/532) — found 2
      confirmed BLOCKING bugs (each independently flagged by 2 of the 5 lenses, each verified by
      direct code reading before being accepted), both fixed in follow-up commits with regression
      tests; see section 9.
- [x] 8.5 Non-author approving review obtained: @blm3886 approved
      [bloom #532](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/532) (branch
      protection on `staging` has `enforce_admins=true` — this satisfies that hard merge gate).
      Note: her approval landed in parallel with the `/review-pr` pass above and predates the 2
      blocking bugs it found — those were fixed afterward regardless of the existing approval,
      since an approval doesn't retroactively clear bugs found after it was given.

## 9. Post-PR-review hardening (found via `/review-pr` on #532, fixed same PR)

- [x] 9.1 RED: `ScanResult("scan_1", "faild")` (typo'd status) must raise `ValueError`, not
      silently construct — a typo'd status wouldn't just miscount, `BatchResult.ok`'s old
      negative-match check (`!= "failed"`) would report a genuinely-failed batch as success.
      GREEN: `ScanResult.__post_init__` validates against the three known statuses;
      `BatchResult.ok` changed to an explicit membership check; `status` typed as
      `Literal["ok", "skipped", "failed"]`.
- [x] 9.2 RED: `stage_one_scan`/`ingest_one_envelope` must isolate an unexpected exception
      (network/auth error beyond the specific types already handled) into a `failed` `ScanResult`,
      not raise — confirmed this was previously uncaught (would crash the whole batch) both at the
      function level and through the batch CLI with other items present. GREEN: wrapped the whole
      per-item body in a catch-all in both functions, mirroring `download_images`'s existing
      per-frame "record and continue" discipline.
- [x] 9.3 RED: an envelope with `provenance.scan_key` containing `/`, `\`, or `..` (path
      traversal) must be rejected before any local filesystem access when `--predictions-dir` is
      given — confirmed the batch path's `predictions_dir / scan_key` construction had no such
      guard, unlike `blob_object_path`'s existing one for the object-storage key. GREEN: added the
      same character check before constructing `scan_predictions_dir`.
- [x] 9.4 Also fixed (no test needed — pure passthrough): `ingest_one_envelope` silently dropped
      the `profile` kwarg when calling `map_rpc_error`, losing the actionable `(profile 'x')` hint
      the single-scan command includes on permission errors.
- [x] 9.5 Closed 2 test-coverage gaps the review found (both passed immediately once written —
      coverage gaps, not bugs): a mixed ok/skipped/failed batch test for `batch-ingest-result`
      (`--json` and default output), previously asymmetric with `batch-download-for-predict`'s
      equivalent; and an end-to-end "malformed sidecar triggers full redownload" test (via
      `stage_one_scan` and the batch CLI), previously only unit-tested at the
      `scan_is_already_staged` boolean-helper level.
- [x] 9.6 Filed [bloom #533](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/533)
      for the one deferred IMPORTANT finding: concurrent invocations against the same `out_dir`
      can race (`scan_is_already_staged` → `clear_scan_dir` has no lock/lease). Deliberately not
      fixed here — the real fix belongs with the not-yet-built `cyl_pipeline_run_scans` dispatch
      worker and Argo's `retryStrategy`, not a bolted-on file lock. See design.md's Risks section.
- [x] 9.7 Remaining SUGGESTIONS from the review (NDJSON for very large `--json` batches,
      scan-key/idempotency indistinguishability in batch reports) left as-is — polish, not
      correctness, and not worth further delay on this PR.
