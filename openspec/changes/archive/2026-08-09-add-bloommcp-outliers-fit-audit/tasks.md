## 1. Extract the shared fit-trustworthiness primitives (own commit, verified green in isolation)

`remove_outliers.py` is the file `#419` (PR #592) just shipped a behavior-sensitive gate in — this
section ships as its own commit, verified green on its own, *before* the audit script's commit
lands on top (elevated care beyond `#585`'s precedent, which fused its analogous refactor with its
new script in one commit).

- [x] 1.1 Add direct unit tests for the primitives *in their new home*, mirroring how `#585`
      tested `trim_staleness` directly in `test_storage_backend.py` rather than only indirectly
      through a consumer's tests: in `bloommcp/tests/test_storage_backend.py`, test
      `experiment_utils.fit_is_trustworthy` and `experiment_utils.UNTRUSTWORTHY_FIT_QUALITIES`
      directly — `None` for a non-dict/absent `goodness_of_fit`, `False` for each of
      `"poor"`/`"very_poor"`/`"unknown"`, `True` for an acceptable-or-better value. Write these
      failing first (the functions don't exist in `experiment_utils` yet).
- [x] 1.2 Move `_UNTRUSTWORTHY_FIT` (rename `UNTRUSTWORTHY_FIT_QUALITIES`) and
      `_fit_is_trustworthy` (rename `fit_is_trustworthy`) from
      `bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/remove_outliers.py` to
      `bloommcp/src/bloom_mcp/experiment_utils.py`, unchanged in behavior. Move `_REPORT_NAME`
      (rename `OUTLIER_REPORT_NAME`) alongside them. Confirm 1.1's new tests pass.
- [x] 1.3 Update `remove_outliers.py` to import all three from `experiment_utils` instead of
      defining them; remove the now-dead private definitions.
- [x] 1.4 Add a symbol-relocation regression test (same pattern as `#403`'s
      `test_role_pattern_lists_live_here_not_in_experiment_utils`, inverted): assert
      `remove_outliers` no longer defines its own `_UNTRUSTWORTHY_FIT`/`_fit_is_trustworthy`/
      `_REPORT_NAME` (e.g. `not hasattr(remove_outliers, "_UNTRUSTWORTHY_FIT")`), so a future
      accidental reintroduction of a duplicate local copy is caught, not silently reintroducing
      the exact drift risk this extraction exists to remove.
- [x] 1.5 Re-run `bloommcp/tests/tools/test_remove_outliers_tool.py` unmodified — a pure
      import-path refactor should not require touching any existing test. Commit here, verified
      green, before starting section 2.

## 2. Add a `manifest_fixtures.py` helper for a fit-report-bearing version

- [x] 2.1 Add `write_outlier_trim_manifest(tmp_path, stem, tool_class, version_id, created_at,
      *, based_on_version, goodness_of_fit, n_outliers, n_input_samples, n_output_samples,
      method="mahalanobis", tool="remove_outliers")` — mirrors `write_cleaned_manifest`'s shape
      (a fresh, one-version manifest; every test scenario in section 4 needs only a single
      version, not an appended history, so this does not need an `append_cleaned_version`-style
      variant). Writes **both** `_cleaned.csv` and `outlier_report.json` under the version dir,
      and populates `VersionEntry.output_keys`/`outputs` for *both* (the existing
      `write_cleaned_manifest`/`append_cleaned_version` only ever write `_cleaned.csv` and never
      populate `output_keys` at all — additive only, existing helpers/callers untouched). The
      `tool=` parameter (default `"remove_outliers"`, overridable) exists specifically so one test
      can build the defensive "`latest` not `remove_outliers`-authored" case without a second,
      bespoke helper.

## 3. Audit script (test-first, per function)

- [x] 3.1 Write failing tests for `scan_for_untrustworthy_outlier_fits()` in a new
      `bloommcp/tests/scripts/test_audit_untrustworthy_outlier_fits.py` (loaded by path, mirrors
      `test_audit_stale_outlier_trims.py`), using `local_manifest_backend` +
      `write_outlier_trim_manifest`:
      - a `remove_outliers`-authored `latest` with `fit_quality="very_poor"` → a hit with the
        expected fields (stem, run ref, `based_on_version`, `created_at`, `fit_quality`,
        `n_outliers`, `n_input_samples`, `n_output_samples`, `method`).
      - `fit_quality="poor"`/`"unknown"` → also hits (not just `"very_poor"`).
      - `fit_quality` acceptable-or-better (e.g. `"excellent"`) → not a hit.
      - `goodness_of_fit is None` (an `isolation_forest` trim) → not a hit.
      - a manifest whose `latest` is not `remove_outliers`-authored (via `tool=` override) → not
        a hit (defensive; not expected to occur in a real `outliers_<stem>` manifest, but must
        not crash if it did).
      - **Decision 2 scope-boundary negative test:** a `qc_<stem>` manifest (not `outliers_`)
        whose `latest` is `remove_outliers`-authored with an untrustworthy fit, and **no**
        `outliers_<stem>` manifest at all for that stem → confirms this is **not** reported,
        pinning the deliberate scope exclusion as a tested fact, not just prose.
      - a manifest with no `latest` pointer → skipped, not an error.
      - a missing/malformed `outlier_report.json` (key absent from `output_keys`, or `read_json`
        raises) → recorded in `errors`, scan continues to the next stem.
      - a malformed manifest.json → recorded in `errors`, scan continues (reuse
        `write_invalid_schema_manifest` from `manifest_fixtures.py`).
      - an empty bucket → empty, successful report.
      - enumeration failure (`list_prefix` raises) → propagates.
      - the scan never calls `write_manifest`/`upload_file` (read-only assertion, mirrors `#585`'s
        `test_scan_never_writes_or_uploads_even_with_hits_and_errors`).
- [x] 3.2 Implement `bloommcp/scripts/audit_untrustworthy_outlier_fits.py`:
      `scan_for_untrustworthy_outlier_fits() -> dict` — enumerate `outliers_<stem>` prefixes via
      `list_prefix`, read each manifest via `AnalysisDir`, skip a manifest with no `latest`, assert
      (don't crash on) `latest_entry.tool == REMOVE_OUTLIERS_TOOL_NAME`, resolve
      `output_keys[OUTLIER_REPORT_NAME]` and `read_json` it, compute `fit_is_trustworthy` on its
      `goodness_of_fit`, and record a hit when it is `False`. Per-stem manifest-read or
      report-read failure → recorded in `errors`, scan continues; enumeration failure →
      propagates. Confirm 3.1's tests pass.
- [x] 3.3 Write failing tests for `write_report()`'s collision-avoidance (mirrors `#585`'s
      `test_write_report_keys_never_collide_even_within_the_same_second`): two `write_report()`
      calls completing within the same wall-clock second produce distinct object keys.
- [x] 3.4 Implement `write_report(report) -> str` — same shape as `#585`'s (`scanned_at`,
      `storage_backend`, a `scope_note` describing the Decision 2 scope gap embedded in the
      payload itself, plus the report), written under
      `bloommcp_output/_audit_reports/untrustworthy_outlier_fits_<ts>_<suffix>.json`. Confirm
      3.3's tests pass.
- [x] 3.5 Write failing tests for `run()`/`main()`: a successful scan returns `0`, prints the
      report, and writes exactly one report object under the `_audit_reports/` prefix; an
      enumeration failure returns `1` and writes no report.
- [x] 3.6 Implement `run()`/`main()` — same exit-code contract as `#585`'s script. Confirm 3.5's
      tests pass.
- [x] 3.7 Write the script's module docstring — purpose, read-only disclosure, the `scope_note`'s
      content, and "run this against a real environment's bucket, not an empty local/dev one"
      guidance, mirroring `audit_stale_outlier_trims.py`'s own docstring (lines 1-54) and its
      `live_persistence_smoke.py` env-override cross-reference.

## 4. Validate

- [x] 4.1 `openspec validate add-bloommcp-outliers-fit-audit --strict` passes.
- [x] 4.2 Full `bloommcp` suite green (`uv run --extra test pytest -q -m 'not live_smoke'`), not
      just the new/changed files.
- [x] 4.3 `ruff format --check` / `black --check` clean on all changed/new Python files. Ran the
      pipeline in this repo's actual pre-commit order (black → `ruff check --fix` → `ruff format`,
      per `.pre-commit-config.yaml`); the two formatters disagree on a few stylistic edge cases
      (e.g. multi-line `assert` wrapping), so `ruff format` — the pipeline's last step — is the
      state that's actually committed, matching what a real `pre-commit run` would leave behind.
