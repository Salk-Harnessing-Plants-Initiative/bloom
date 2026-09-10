## 1. RED — tighten existing tests to pin exit codes, confirm they fail against today's code

All 10 tests below currently pass today (8 assert the looser `result.exit_code != 0`, already true
under the existing `ctx.exit(1)`; 2 already assert `!= 0` for a usage-error/lock-contention path
that stays at `1`). Tightening each to the specific value it should be makes the first 8 fail
against today's code — that failure is the RED step; no new test files or fixtures are needed,
since `bloomcli/tests/test_cyl_download_for_predict.py` already exercises every one of these
scenarios. Section 1 and Section 2 land in the same commit (see Section 6) — do not push after
Section 1 alone; a red-only push would trigger a real (if harmless) CI failure on this PR for no
benefit.

- [x] 1.1 `test_batch_cli_isolates_unexpected_network_error_among_several` (line 826): change
      `assert result.exit_code != 0` to `assert result.exit_code == 3`.
- [x] 1.2 `test_batch_cli_isolates_one_bad_scan` (line 954): same change.
- [x] 1.3 `test_batch_cli_isolates_one_bad_scan_json` (line 971): same change.
- [x] 1.4 `test_batch_oracle_discover_scans_accepts_the_survivors` (line 991, dev-only/skipped in
      CI via `pytest.importorskip("sleap_roots_predict")`): same change — kept in sync even
      though CI skips it, so a local run of the oracle test doesn't silently regress to the old
      assumption.
- [x] 1.5 `test_batch_cli_mixed_statuses_json_output` (line 1149): same change.
- [x] 1.6 `test_batch_cli_mixed_statuses_default_output` (line 1161): same change.
- [x] 1.7 `test_batch_cli_lock_contention_isolates_one_scan_others_succeed` (line 1259): same
      change — confirms lock-contention failures (a `result.ok is False` case, not a raised
      exception) also map to `3`, not just content/network-style failures.
- [x] 1.8 `test_batch_cli_all_scans_failed_no_prior_manifest_skips_write_no_crash` (line 1514):
      same change — confirms the *all*-failed case (not just partial) also exits `3`; its existing
      `isinstance(result.exception, SystemExit)` assertion is unaffected by the exit-code value.
- [x] 1.9 Negative boundary tests — tighten two existing tests from `assert result.exit_code !=
      0` to pin their specific existing value, proving the new `3` path doesn't leak into the
      pre-existing `ClickException`/`UsageError` paths: the both-flags-given usage error (line
      1082) to `== 2` (a `click.UsageError`, Click's own reserved usage-error code — confirmed by
      reading the raise site directly, not assumed), and the manifest-lock-contention failure
      (line 1449) to `== 1` (a `click.ClickException`/plain `ctx.exit()`, both surfacing as
      `SystemExit(1)`). These two already pass today under the loose assertion, so tightening them
      doesn't add a RED step — they're GREEN-from-the-start regression guards, not part of this
      section's failing set.
- [x] 1.10 Run the 8 tests from 1.1-1.8 (by name, or the whole file) and confirm exactly those 8
      fail with `assert 1 == 3`, and the 2 tests from 1.9 still pass (`== 2` and `== 1`
      respectively), before touching any implementation code.

## 2. GREEN — the exit-code change itself

- [x] 2.1 In `bloomcli/src/bloomctl/cyl/download_for_predict.py`, change the tail of
      `batch_download_for_predict` (currently `if not result.ok: ctx.exit(1)`) to exit `0` on full
      success and `3` otherwise (e.g. `ctx.exit(0 if result.ok else 3)`), preserving the existing
      early `return`s (empty `scan_ids`, `write_run_manifest` still runs first) exactly as today.
      Do not touch any other exit path in this file (`click.UsageError`/`click.ClickException`
      call sites stay as they are).
- [x] 2.2 Run the 8 tests from 1.1-1.8 again; confirm all now pass (GREEN). Re-run the 2 tests
      from 1.9; confirm they still pass (unchanged).
- [x] 2.3 Run the full `test_cyl_download_for_predict.py` and
      `test_download_for_predict_concurrency.py` files; confirm no other test (including the
      remaining usage-error/manifest-lock/manifest-write-failure tests at lines 1024, 1041, 1091,
      1479, 1499, 1667, 1693, 1716, 1743, 1772, none of which this change should affect) regresses.

## 3. Docs

- [x] 3.1 Update the `batch_download_for_predict` docstring
      (`download_for_predict.py:573-574`) from "exits non-zero if any scan failed" to state the
      concrete contract: exits `0` on full success, `3` if any scan failed.
- [x] 3.2 Update `bloomcli/README.md:460-461`'s "Exit code:" bullet for
      `batch-download-for-predict` to the same concrete `0`/`3` wording.
- [x] 3.3 Add one short parenthetical to `bloomcli/README.md:564-567`'s "Exit code:" bullet for
      the sibling `batch-ingest-result` command (left otherwise unchanged — its own exit-code
      rework is PR #774's scope, not this change's), flagging the now-visible inconsistency
      between the two structurally-parallel commands, e.g.: "(this command's non-zero code is
      still a single undifferentiated value pending bloom PR #774; see `batch-download-for-predict`
      above for the `0`/`3` convention that command now uses)."
- [x] 3.4 Add a new entry under `bloomcli/CHANGELOG.md`'s existing (currently empty)
      `## [Unreleased]` header — add a `### Fixed` subheading (none exists yet) with a bullet
      along the lines of: "`bloomctl cyl batch-download-for-predict` now exits `3` (not `1`) when
      at least one scan in the batch failed, distinguishing partial success from a full crash —
      mirrors `sleap_roots_predict`/`trait_extractor`'s existing `0`/`3` convention. Full success,
      an all-skipped batch, or empty input still exit `0` (#772)." Do not edit the historical
      entry (lines ~301-310) describing the original shipped behavior.

## 4. Spec

- [x] 4.1 The delta spec at
      `openspec/changes/fix-cyl-batch-download-partial-exit-code/specs/cyl-batch-download-for-predict/spec.md`
      is already scaffolded and validated (`openspec validate ... --strict` passes) — no further
      edits needed here; this task exists only to confirm it still validates after 2.1-3.4 land
      (spec text describes the target behavior, doesn't need to change alongside the
      implementation).

## 5. Validate

- [x] 5.1 `ruff check --fix` on `download_for_predict.py` and the changed test file
      (bloomcli's pre-commit config does not include a `black` hook or dependency for this
      package — `ruff` is the only formatter/linter that applies here).
- [x] 5.2 `openspec validate fix-cyl-batch-download-partial-exit-code --strict` passes.
- [x] 5.3 `cd bloomcli && uv run --extra test pytest tests/ -m "not integration"` — the full suite
      this repo's CI (`pr-checks.yml`) actually runs for `bloomcli`; confirm green. (Lint/format
      is not CI-enforced for `bloomcli` today — 5.1 is a local-only gate, still worth running.)
- [ ] 5.4 (Post-merge, not part of this PR) Re-run the poison-scan batch-oracle scenario on
      staging (`A4-PIPELINE-E2E-TEST`, `experiment_id 12880747`) and confirm
      `batch_download_for_predict` itself now exits `3` (not `1`) for a batch with 1 poison + 2
      good scans, with the 2 good scans still correctly staged and a `run_manifest.json` listing
      only them. Full poison-scan-scenario-ends-`partial` verification (Argo not retrying the
      whole step, the DAG proceeding to `predictor`) additionally requires
      `sleap-roots-pipeline#56`'s Argo wiring, out of scope for this change/PR — **do not close
      bloom#772** when this PR merges; it stays open until that companion fix ships and the live
      re-run confirms a `partial` run end-to-end.

## 6. Commit plan

- [x] 6.1 One commit: `fix(#772): exit 3, not 1, when a download batch has a partial failure` —
      `bloomcli/src/bloomctl/cyl/download_for_predict.py` +
      `bloomcli/tests/test_cyl_download_for_predict.py` (Sections 1+2 together; matches this
      repo's convention of bundling a behavior change with its tests in one commit rather than a
      separate red-only commit).
- [x] 6.2 One commit: `docs(#772): describe batch-download-for-predict's 0/3 exit contract` —
      `bloomcli/README.md`, `bloomcli/CHANGELOG.md` (Section 3).
- [x] 6.3 One commit: `docs(openspec): scope fix-cyl-batch-download-partial-exit-code` — the
      `openspec/changes/fix-cyl-batch-download-partial-exit-code/` scaffold itself (proposal.md,
      design.md, tasks.md, specs/), landing in the same PR per this repo's bundled-PR convention.
- [ ] 6.4 Single PR targeting `staging`, branch `eberrigan/fix-bloomctl-batch-download-partial-exit-772`.
      PR body: reference `Related to #772` (not `Fixes #772` — see 5.4's note on why the issue
      stays open post-merge).
