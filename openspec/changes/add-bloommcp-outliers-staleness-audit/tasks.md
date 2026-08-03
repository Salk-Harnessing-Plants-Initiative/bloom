## 1. Fix and promote the shared local-manifest-backend test fixture

- [ ] 1.1 In `bloommcp/tests/test_storage_backend.py`, confirm (by reading, not assuming) that its
      `_local_backend` fixture (`def _local_backend(monkeypatch, tmp_path)`) calls
      `reset_backend_for_tests()` only on entry, with no `yield`/teardown reset — a real,
      pre-existing gap. Note for context (do not rely on this file to demonstrate the gap): this
      file's own module-level `autouse=True` fixture, `_reset_backend` (added by #420's own review
      response), resets the backend on both setup *and* teardown around *every* test in this file,
      and — because pytest tears down fixtures in reverse setup order — its post-yield reset runs
      *after* `_local_backend`'s missing one, masking the leak for any two tests that both live in
      `test_storage_backend.py`. The gap only becomes externally observable once this seam is used
      from a file with no equivalent autouse net — exactly what tasks 3 and 4 are about to do. Do
      not attempt to prove the leak via a same-file, two-sequential-test regression test — it will
      not go red here, for the reason above.
- [ ] 1.2 Fix by promoting `_local_backend`, `_write_cleaned_manifest`, and
      `_write_invalid_schema_manifest` (currently private to `test_storage_backend.py`) into
      `bloommcp/tests/conftest.py` as shared fixtures/helpers, function-scoped (consistent with
      every other fixture already in that file). The promoted `local_manifest_backend` fixture
      SHALL reset the backend (`reset_backend_for_tests()`) both on setup *and* on teardown (`yield`
      then reset) — self-evidently correct given `_reset_backend`'s already-established
      yield-then-reset shape in the same codebase, applied here at fixture scope instead of
      module scope, so a test using `local_manifest_backend` from *any* file leaves no residue
      regardless of whether that file has its own autouse net. Update `test_storage_backend.py` to
      consume the promoted fixture/helpers instead of its own private copies — its full existing
      test suite (including `test_latest_logs_when_resolved_trim_is_stale` /
      `test_latest_does_not_log_when_resolved_trim_is_current`) must keep passing unmodified.
- [ ] 1.3 Confirm the full `test_storage_backend.py` suite is green after the promotion, and run the
      whole `bloommcp` suite once with `pytest --random-order` (or an equivalent forced-shuffle
      option, if the project has one available; otherwise, run each new test file created by tasks
      3-4 both on its own and as part of the full suite) to confirm no new test's outcome depends on
      execution order relative to any `local_manifest_backend`-based test in a different file — this
      is the practical, end-to-end proof that the promoted fixture's teardown reset actually does
      its job, replacing the same-file regression test that task 1.1 established cannot work.

## 2. Extract the shared `trim_staleness` primitive

- [ ] 2.1 Using the now-shared `local_manifest_backend` fixture, write failing unit tests for a new
      `experiment_utils.trim_staleness(stem)`, returning `None` or a `TrimStaleness(is_stale,
      outliers_based_on_version, current_qc_label)` `NamedTuple`: (a) no `outliers`-class version at
      all → `None`; (b) an `outliers`-class latest entry whose `based_on_version` matches the
      current `qc`-class latest label → `is_stale=False`, both string fields populated; (c) a `qc`
      re-run since the trim (the same fixture shape `test_latest_logs_when_resolved_trim_is_stale`
      already builds) → `is_stale=True`, both string fields populated; (d) an `outliers`-class
      latest entry exists but the `qc`-class manifest has no `latest` entry at all (write only the
      `outliers` manifest, no `qc` manifest for that stem) → `is_stale=True`,
      `current_qc_label=None` — a new, previously-untested/unreached corner (design.md Decision 1).
- [ ] 2.2 In `bloommcp/src/bloom_mcp/experiment_utils.py`, extract `trim_staleness(stem)` out of
      `_log_if_trim_is_stale`'s body, returning the `TrimStaleness` `NamedTuple` (or `None`) from
      2.1 — this preserves the exact values `_log_if_trim_is_stale`'s current inline implementation
      logs today (do not drop them to a bare boolean). Rewrite `_log_if_trim_is_stale` to call
      `trim_staleness(stem)` and log only when the result is not `None` **and** `result.is_stale` is
      `True` (not merely "the result is truthy" — a `TrimStaleness` with `is_stale=False` is itself
      a non-empty, truthy tuple). Give it two message branches: the existing text ("...a `qc_clean`
      has run since this trim was made...", naming `current_qc_label`) when `current_qc_label` is
      not `None`, and a distinct message ("...no `qc`-class version could be found for this
      experiment at all...") for the `current_qc_label is None` case from 2.1(d) — do not
      interpolate `None` into the old sentence. It must keep swallowing all exceptions itself (never
      raise) exactly as it does today — `trim_staleness` itself does not swallow exceptions, so
      callers choose their own failure policy (task 3 chooses differently).
- [ ] 2.3 Confirm the two pre-existing staleness-log tests
      (`test_latest_logs_when_resolved_trim_is_stale`,
      `test_latest_does_not_log_when_resolved_trim_is_current`) pass unmodified — including their
      assertions on the logged message's interpolated values — plus the four new `trim_staleness`
      tests from 2.1, plus one new log test for 2.1(d)'s no-`qc`-baseline-at-all case asserting the
      distinct message branch fires.

## 3. Surface `trim_is_stale` in `list_existing_analyses`

- [ ] 3.1 Grep `bloommcp/tests/` for any existing test that asserts exact/full-dict equality on
      `list_existing_analyses`'s JSON response for an experiment that has an `outliers`-class
      manifest (as opposed to membership checks, e.g. `"outliers" in response["analyses"]`, which
      an added key doesn't affect) — confirm whether any needs updating.
- [ ] 3.2 Write failing tests (new file `bloommcp/tests/tools/test_list_existing_analyses_staleness.py`)
      covering: no `outliers` version → no `trim_is_stale` key, no new `errors` entry; current trim
      → `"trim_is_stale": false`; stale trim → `"trim_is_stale": true`; a `trim_staleness` failure
      (monkeypatch `experiment_utils.trim_staleness` to raise) → `trim_is_stale` absent **and**
      `errors` contains an entry starting with `"trim_staleness: "`, with the tool's other output
      (`analyses`) still populated. Also add one test that runs with **no** `BLOOM_STORAGE_BACKEND`/
      `SUPABASE_URL`/`BLOOM_AGENT_KEY` configured (this repo's actual test-suite default per
      `conftest.py`'s env-scrubbing) and asserts explicitly that `list_existing_analyses` does not
      raise, `trim_is_stale` is absent, and `errors` contains a `trim_staleness` entry — this
      converts what would otherwise be an untested, accidental-pass corner into a verified
      contract. Use the `local_manifest_backend` fixture (task 1.2) for the manifest-backed cases;
      `injected_ports`' `FakeReader` can stay seeded with no experiments, since the "known
      experiment" guard only rejects when its `known` set is non-empty.
- [ ] 3.3 In `bloommcp/src/bloom_mcp/sections/core/list_existing_analyses.py`, import
      `trim_staleness` from `experiment_utils` (with a one-line comment recording why this is a
      disclosed exception to the ports-only dependency — design.md Decision 2), compute
      `stem = Path(experiment_filename).stem`, and wrap the call in `try/except`. Build the
      response's `errors` list unconditionally as a local variable through the whole function body
      (not only materialized into `response["errors"]` once at the end) so a `trim_staleness`
      failure appended to it is never dropped regardless of whether any earlier tool-class lookup
      also failed — this is a deliberate fix to a latent ordering trap (appending to a list that
      might not have been attached to `response` yet). Set `response["trim_is_stale"]` only when
      the call succeeds and returns non-`None` (`result.is_stale`).
- [ ] 3.4 Add two sentences to `list_existing_analyses`'s docstring: what `trim_is_stale` means and
      when it appears, and the advisory-only disclosure ("if absent, check `errors` for a
      `trim_staleness` entry before concluding the experiment was never trimmed").
- [ ] 3.5 Apply whatever test updates task 3.1 found were needed; confirm the full discovery test
      suite (`test_qc_tools_discovery.py`, `test_remove_outliers_tool.py`'s
      `test_discoverable_via_list_existing_analyses`, `test_cross_experiment_correlations_tool.py`)
      still passes unmodified otherwise.

## 4. Historical silent-revert audit script

- [ ] 4.1 Create `bloommcp/tests/scripts/test_audit_stale_outlier_trims.py`, loading
      `bloommcp/scripts/audit_stale_outlier_trims.py` by path via `importlib.util` (mirroring
      `test_live_persistence_smoke_logic.py`'s pattern for loading the file — `bloommcp/scripts/`
      is not a package either; note this precedent covers only the by-path *loading* mechanism, not
      the `run()`/exit-code split in 4.3, which has no existing local precedent to follow). Using
      the `local_manifest_backend` fixture, write failing tests against a
      `scan_for_stale_outlier_trims()` function: (a) `qc_clean`(v1) → `remove_outliers`(v2) →
      `qc_clean`(v3, latest) → one hit naming v2 (id + created_at) as the superseded trim and v3
      (id + tool + created_at) as current latest; (b) `qc_clean`(v1) → `remove_outliers`(v2) →
      `remove_outliers`(v3, latest) → **no hit** (the #419 legitimate-re-trim case — this is the
      corrected case that replaces the earlier, incorrect "still latest → no hit" framing);
      (b2) `qc_clean`(v1) → `remove_outliers`(v2) → `remove_outliers`(v3, latest) → assert **still
      no hit** even though v2 is a non-latest `remove_outliers` entry, and separately
      `qc_clean`(v1) → `remove_outliers`(v2) → `qc_clean`(v3, latest) → assert the hit names **v2**
      specifically (exercises the "most recently-committed `remove_outliers` entry" tie-break rule
      from design.md Decision 4 against a manifest with more than one `remove_outliers` entry in
      history — e.g. `qc_clean`(v1) → `remove_outliers`(v2) → `remove_outliers`(v3) →
      `qc_clean`(v4, latest) → hit must name **v3**, not v2); (c) a manifest with only
      `qc_clean`-authored entries → no hit; (d) one manifest containing genuinely malformed JSON
      (not merely an unsupported schema version — write raw invalid bytes directly to the manifest
      object via the storage backend's own file-write primitive, the same layer
      `_write_invalid_schema_manifest` already writes through, bypassing `write_manifest`/`write_json`
      entirely) alongside one valid, hit-producing manifest → the valid hit is still reported, the
      malformed one appears in the report's error list keyed by stem, and the exception type is not
      assumed to be only `ManifestSchemaError`; (d2) a `qc_<stem>` prefix that exists with **no**
      `manifest.json` underneath it at all (write a bare file directly under that prefix, no
      manifest) → the stem is skipped silently — it appears in **neither** `hits` nor `errors`
      (design.md Decision 5's "soft skip, not an error" case); (d3) a schema-valid manifest whose
      `versions` list contains a `remove_outliers` entry but whose `latest` field is `None` (a
      valid-but-unusual state per the `Manifest` schema) → does not crash; document/assert whatever
      defined behavior 4.2 gives it (no hit, since there is no "current latest" authored by a
      different tool to compare against); (e) no `qc_*` prefixes at all → empty hits, empty errors;
      (f) `list_prefix` itself raising (monkeypatch it to simulate an unreachable backend) →
      `scan_for_stale_outlier_trims()` propagates the exception rather than reporting an empty
      "successful" scan (this is the one failure mode the function does *not* swallow — see 4.2).
- [ ] 4.2 In `bloommcp/scripts/audit_stale_outlier_trims.py`, implement
      `scan_for_stale_outlier_trims() -> dict` (`{"hits": [...], "errors": [...],
      "experiments_scanned": N}`), purely read-only — it must never import or call
      `write_manifest`, `upload_file`, or `write_json`. Call
      `supabase_client.list_prefix("bloommcp_output/")` **unguarded** (a failure here propagates —
      task 4.1f), keep names starting with `f"{QC_TOOL_CLASS}_"` (imported from
      `experiment_utils`, not a bare `"qc_"` literal), and derive each stem by stripping that
      prefix. For each stem, build `AnalysisDir("bloommcp_output", f"{stem}.csv", QC_TOOL_CLASS)`
      and call `.read_manifest()` inside a **per-stem** `try/except Exception` (deliberately
      broader than `_resolve_one_class`'s catch for this "no entry" case — design.md Decision 5
      explains why a best-effort forensic sweep should route around any per-stem failure, not just
      a schema mismatch). If `read_manifest()` returns `None` (no `manifest.json` under that
      prefix — task 4.1d2), skip the stem with no entry in either list. Otherwise, apply the
      corrected hit rule (design.md Decision 4): a hit requires at least one
      `remove_outliers`-authored `VersionEntry` in the manifest's history **and** the entry
      `manifest.latest` points at was authored by a *different* tool (if `manifest.latest` is
      `None` — task 4.1d3 — there is no "current latest" to compare, so no hit). Report the most
      recently-committed `remove_outliers` entry (by `created_at`, not by list position) as the
      superseded trim.
- [ ] 4.3 Add a `write_report(report: dict) -> str` helper that adds `scanned_at` (ISO-8601 UTC) and
      `storage_backend` (the active backend's name) fields to the report dict, JSON-serializes it,
      and writes it via `supabase_client.write_json` to a timestamped key under
      `bloommcp_output/_audit_reports/` (returning the key written) — the payload itself, not only
      the object's key/filename, must carry these two fields so the report stays self-describing if
      moved or renamed later. Add a thin `main()` that calls `scan_for_stale_outlier_trims()`, calls
      `write_report`, prints the report plus a one-line summary (`"N experiments scanned, M hits, E
      errors, report written to <key>"`) to stdout, and returns exit code `0`. If
      `scan_for_stale_outlier_trims()` itself raises (task 4.1f's case), `main()` prints the error to
      stderr and returns exit code `1` — no report is written, since there is nothing complete to
      persist. Extract this logic into a testable `run() -> int` separate from the
      `if __name__ == "__main__": raise SystemExit(run())` guard (this specific split has no
      existing precedent elsewhere in this repo's scripts/smoke drivers — it is being introduced
      here, not reused). Write unit tests for: both branches of `run()` (success → 0; enumeration
      failure → 1); and, using the `local_manifest_backend` fixture, one test that actually calls
      `write_report` (or `run()` end-to-end) against a seeded hit-producing fixture and then reads
      the written object back via the backend's own read primitive, asserting the key lands under
      `bloommcp_output/_audit_reports/`, and that its parsed content includes `scanned_at`,
      `storage_backend`, and the same `hits`/`errors`/`experiments_scanned` the scan produced — a
      test that only monkeypatches `write_report` away would leave the real write path unverified,
      which this task explicitly rules out.
- [ ] 4.4 Write a unit test asserting `scan_for_stale_outlier_trims()` makes zero calls to any
      write/upload path, even when it encounters hit-producing and error-producing manifests in the
      same run. Name the monkeypatch targets precisely rather than patching one arbitrary binding:
      this codebase's own `fake_supabase_storage` fixture (`bloommcp/tests/conftest.py`) already has
      to patch multiple, separately-bound references to the same underlying functions (e.g.
      `bloom_mcp.supabase_client.write_json` **and** `bloom_mcp.manifest.manifest.write_json`, and
      similarly for `write_manifest`/`upload_file` via `bloom_mcp.manifest`'s re-export) — follow
      that same fixture's patch-target list exactly, rather than guessing one path, so an
      incompletely-patched test doesn't give false assurance by trivially passing regardless of
      what `scan_for_stale_outlier_trims()` actually calls. This is the mechanical guard for the
      spec's "never mutates an experiment's own manifests" requirement, not merely a prose claim.
- [ ] 4.5 Write the script's module docstring: purpose, that its core scan is read-only (the one
      write is its own dedicated, self-describing report object, never a `qc_`/`outliers_`
      manifest — and note explicitly that this is a write to the shared bucket, a deliberately
      narrow reading of #585's "no data mutation" as "no experiment-manifest mutation"), and how to
      point it at a real environment (it uses the same `SUPABASE_URL`/`BLOOM_AGENT_KEY`/
      `BLOOM_STORAGE_BACKEND` configuration the running `bloommcp` service for that environment
      uses — mirroring `tests/smoke/live_persistence_smoke.py`'s documented env-override
      convention — since running it against an empty local/dev bucket finds nothing meaningful).

## 5. Disclose the narrow import-guard exception

- [ ] 5.1 In `bloommcp/tests/test_persistence_import_guard.py`'s module docstring, add one sentence
      noting that `list_existing_analyses.py` has one disclosed, transitive exception to the
      ports-only dependency this guard's AST scan enforces (via `experiment_utils.trim_staleness`,
      which itself reads through `AnalysisDir` — the same pattern `_log_if_trim_is_stale` already
      uses from inside `experiment_utils.py`) — so a future reader of the guard understands its
      per-file `import` scan does not claim to catch transitive dependencies, by design.

## 6. Validate

- [ ] 6.1 `npx -y -p @fission-ai/openspec openspec validate add-bloommcp-outliers-staleness-audit --strict`
      passes.
- [ ] 6.2 Full `bloommcp` unit test suite passes (`uv run --extra test pytest`), including all new
      tests above.
