## 1. Extract the shared fit-trustworthiness primitives

- [ ] 1.1 Move `_UNTRUSTWORTHY_FIT` (rename `UNTRUSTWORTHY_FIT_QUALITIES`) and
      `_fit_is_trustworthy` (rename `fit_is_trustworthy`) from
      `bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/remove_outliers.py` to
      `bloommcp/src/bloom_mcp/experiment_utils.py`, unchanged in behavior. Move
      `_REPORT_NAME` (rename `OUTLIER_REPORT_NAME`) alongside them.
- [ ] 1.2 Update `remove_outliers.py` to import all three from `experiment_utils` instead of
      defining them; remove the now-dead private definitions.
- [ ] 1.3 Re-run `bloommcp/tests/tools/test_remove_outliers_tool.py` unmodified — a pure
      import-path refactor should not require touching any existing test.

## 2. Add a `manifest_fixtures.py` helper for a fit-report-bearing version

- [ ] 2.1 Add `append_outlier_trim_version(tmp_path, stem, tool_class, version_id, created_at,
      *, based_on_version, goodness_of_fit, n_outliers, n_input_samples, n_output_samples,
      method="mahalanobis")` — writes both `_cleaned.csv` and `outlier_report.json` under the
      version dir, and populates `VersionEntry.output_keys`/`outputs` for *both* (the existing
      `write_cleaned_manifest`/`append_cleaned_version` only ever write `_cleaned.csv` and never
      populate `output_keys` at all — additive only, existing helpers/callers untouched).

## 3. Audit script

- [ ] 3.1 `bloommcp/scripts/audit_untrustworthy_outlier_fits.py`:
      `scan_for_untrustworthy_outlier_fits() -> dict` — enumerate `outliers_<stem>` prefixes via
      `list_prefix`, read each manifest via `AnalysisDir`, skip a manifest with no `latest`, assert
      (don't crash on) `latest_entry.tool == REMOVE_OUTLIERS_TOOL_NAME`, resolve
      `output_keys[OUTLIER_REPORT_NAME]` and `read_json` it, compute `fit_is_trustworthy` on its
      `goodness_of_fit`, and record a hit (fields per design.md Decision 5) when it is `False`.
      Per-stem manifest-read or report-read failure → recorded in `errors`, scan continues;
      enumeration failure → propagates (nothing to report at all).
- [ ] 3.2 `write_report(report) -> str` — same shape as `#585`'s (`scanned_at`, `storage_backend`,
      a `scope_note` describing the Decision 2 scope gap embedded in the payload itself, plus the
      report), written under `bloommcp_output/_audit_reports/untrustworthy_outlier_fits_<ts>_<suffix>.json`.
- [ ] 3.3 `run()`/`main()` — same exit-code contract as `#585`'s script (`1` only when
      enumeration itself fails; `0` whenever the scan completes, hits/errors included).

## 4. Tests

- [ ] 4.1 `bloommcp/tests/scripts/test_audit_untrustworthy_outlier_fits.py`, loaded by path
      (mirrors `test_audit_stale_outlier_trims.py`), using `local_manifest_backend` +
      `append_outlier_trim_version`:
      - a `remove_outliers`-authored `latest` with `fit_quality="very_poor"` → a hit with the
        expected fields.
      - `fit_quality="poor"`/`"unknown"` → also hits (not just `"very_poor"`).
      - `fit_quality` acceptable-or-better (e.g. `"excellent"`) → not a hit.
      - `goodness_of_fit is None` (an `isolation_forest` trim) → not a hit.
      - a manifest whose `latest` is not `remove_outliers`-authored → not a hit (defensive; not
        expected to occur in a real `outliers_<stem>` manifest, but must not crash if it did).
      - a manifest with no `latest` pointer → skipped, not an error.
      - a missing/malformed `outlier_report.json` (key absent from `output_keys`, or `read_json`
        raises) → recorded in `errors`, scan continues to the next stem.
      - a malformed manifest.json → recorded in `errors`, scan continues (reuse
        `write_invalid_schema_manifest` from `manifest_fixtures.py`).
      - an empty bucket → empty, successful report.
      - enumeration failure (`list_prefix` raises) → propagates, `run()` returns `1`, no report
        written.
      - `run()` on a successful scan → returns `0`, prints the report, and writes exactly one
        report object under the `_audit_reports/` prefix.
      - two hits' report keys never collide even committed within the same wall-clock second
        (mirrors `#585`'s own collision-avoidance test).
      - the scan never calls `write_manifest`/`upload_file` (read-only assertion, mirrors `#585`'s
        `test_scan_never_writes_or_uploads_even_with_hits_and_errors`).

## 5. Validate

- [ ] 5.1 `openspec validate add-bloommcp-outliers-fit-audit --strict` passes.
- [ ] 5.2 Full `bloommcp` suite green (`uv run --extra test pytest -q -m 'not live_smoke'`), not
      just the new/changed files.
- [ ] 5.3 `ruff format --check` / `black --check` clean on all changed/new Python files.
