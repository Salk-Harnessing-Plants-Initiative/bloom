## 1. Bug fix (test-first)

- [x] 1.1 Write a direct unit test in `bloommcp/tests/test_storage_backend.py`, alongside its existing coverage of `_resolve_one_class`'s other two failure points: monkeypatch the local backend's `list_prefix` to raise during `get_version`'s manifest read, call `_resolve_one_class`/`_resolve_versioned_cleaned` directly, assert the hard-error tuple is returned. Confirm it fails red against current code (an uncaught exception, not a clean hard-error tuple) before touching `experiment_utils.py`. (Implemented as `test_latest_outliers_manifest_read_fails_is_a_hard_error`, which also seeds a valid `qc` entry — see 1.2, folded into the same test to match this file's existing convention for the sibling schema-error/download-failure tests.)
- [x] 1.2 Write the load-bearing safety test in the same file, mirroring `test_latest_schema_error_on_outliers_propagates_first_iteration`: seed a valid, resolvable `qc`-class `latest` entry alongside the induced manifest-read failure on the higher-priority `outliers` class; assert the hard error propagates and the call never silently falls through to return the seeded `qc` entry's data. Confirm this also fails red. (Satisfied by the same test as 1.1 — see note above.)
- [x] 1.3 Write a `LocalReader`-level test proving `LocalReader.load_experiment` (via `experiment_utils.load_experiment_data`) hits the identical uncaught-exception bug. Confirm red. (Implemented as `test_manifest_read_failure_is_caller_safe_not_raw` in `bloommcp/tests/data_access/test_local_reader.py`, reusing that file's own `local_env`/`_seed_cleaned` fixtures rather than `test_storage_backend.py`'s — a better fit since it exercises `LocalReader` directly.)
- [x] 1.4 Fix `_resolve_one_class` (`bloommcp/src/bloom_mcp/experiment_utils.py:404-407`): add a second, additive `except Exception` clause below the existing `except ManifestSchemaError` — do NOT merge them into one message (the existing clause's exact string is asserted by `test_storage_backend.py:958-996`). Add `logger.warning(..., exc_info=True)` in the new branch. Confirm 1.1-1.3 now pass green, and the two pre-existing `ManifestSchemaError`-message tests are unaffected.

## 2. FakeReader failure-injection hook

- [x] 2.1 Add `FakeReader.fail_next_load(name, *, version="latest")` — one-shot, mirroring `FakeResultStore.fail_next_commit`'s pattern — consumed at the top of `load_experiment` before any resolution logic.
- [x] 2.2 Add a `FakeReader`-only unit test in `bloommcp/tests/data_access/test_fake_reader.py` covering: one-shot behavior, `(name, version)` scoping, and that a retry for the same `(name, version)` after the hook clears resolves normally.

## 3. Cross-adapter parity coverage

- [x] 3.1 Add `bloommcp/tests/data_access/test_reader_parity.py` with one shared scenario parametrized `fake`/`supabase`, proving both adapters convert a mid-read storage failure into `ExperimentReadError` (not a raw exception) — `fail_next_load` for the fake branch, a `bloom_mcp.manifest.manifest.list_prefix` monkeypatch for the supabase branch, plus a retry-succeeds check for both.

## 4. Spec + validation

- [x] 4.1 Confirm `openspec/specs/bloommcp-experiment-read/spec.md` deltas (this change's `specs/` dir) are current — new scenario on `ExperimentReader Port` for the hard-error-during-resolution case, new scenario + extended coverage note on `FakeReader Adapter`.
- [x] 4.2 Run `openspec validate update-bloommcp-reader-fake-parity --strict` and fix any issues.

## 5. Full verification

- [x] 5.1 Run the full `bloommcp` test suite (891 passed, 29 skipped — pre-existing, unrelated), with explicit attention to `bloommcp/tests/test_storage_backend.py` (the two pre-existing `ManifestSchemaError`-message tests pass unchanged), `test_supabase_reader.py`, `test_local_reader.py`, `test_fake_reader.py`, `test_reader_parity.py`.
- [x] 5.2 Run `ruff check` / `ruff format` (pinned `v0.9.9`, matching `.pre-commit-config.yaml`) on changed files — clean.

## 6. PR #588 review response

- [x] 6.1 **Blocking**: rebased onto `staging` after PR #587 (#585's outliers-staleness audit) merged and renamed `test_storage_backend.py`'s `_local_backend`/`_write_cleaned_manifest`/`_write_invalid_schema_manifest` to `local_manifest_backend` (promoted to `conftest.py`)/`write_cleaned_manifest`/`write_invalid_schema_manifest` (promoted to `manifest_fixtures.py`). Updated `test_latest_outliers_manifest_read_fails_is_a_hard_error` to the renamed fixture/helpers; resolved via `git rebase`, not a merge commit.
- [x] 6.2 Corrected design.md's Risks section: the new `except Exception` branch does **not** itself redact `{e}` (it embeds it verbatim, same as its two siblings) — the caller-facing scrubbing is a separate, pre-existing layer at the `load_experiment` call sites. Noted `experiment_utils.safe_error_text()` (added by the concurrent #585 work, not present when this proposal was drafted) as a candidate for a future, uniform redaction pass across all three failure points — deliberately not wired into only this new branch, to avoid a fresh asymmetry with its untouched siblings.
- [x] 6.3 Documented (previously implicit) that the new branch always hard-errors on `version="latest"`, even though a healthy check for a tool class with no manifest yet returns `None` cleanly rather than raising — a deliberate, safety-favoring trade-off consistent with the function's own existing "uncertain state is a hard error, never a silent soft-miss" rationale, not an oversight.
- [x] 6.4 Added a one-line callout that this fix's benefit is "no crash," not "distinguish a transient network blip from a genuinely absent experiment" — the not-found/read-error taxonomy redesign remains a deliberate non-goal, not silently implied to be solved.
- [x] 6.5 Corrected design.md's "Concurrent branch awareness" note, which had named the wrong PR (#587, not #585) and the wrong hazard (predicted a clean `experiment_utils.py` merge and missed the actual `test_storage_backend.py` fixture-rename conflict).
- [x] 6.6 Suggestions applied: added `test_fail_next_load_fires_even_for_an_unseeded_experiment` (pins that the injected failure fires before any not-found check); rewrote `test_reader_parity.py`'s supabase-side injection to use `monkeypatch.context()` (auto-reverting on `with`-block exit) instead of a manual capture-and-restore.
- [x] 6.7 Re-ran the full suite for real against the rebased branch (929 passed, 29 skipped — the higher count reflects tests merged in from #585/#419) and `ruff check`/`format` (pinned `v0.9.9`) — both clean.
