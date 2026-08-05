> **TDD note:** write the RED tests (§1) against the not-yet-guarded `build_output_links`, confirm
> each fails (or rather, confirm the call signature itself doesn't yet accept `expected_prefix` —
> a `TypeError`, which is the correct RED state here), then implement (§2) to GREEN. Commit
> RED+GREEN together — do not push a RED-only commit.
>
> **Commit plan:**
> 0. `docs(#598): draft add-bloommcp-signed-url-key-scoping proposal` — this
>    `openspec/changes/add-bloommcp-signed-url-key-scoping/` directory.
> 1. `feat(#598): add structural key-scoping guard to ResultStore.commit()` — `_artifacts.py`,
>    `supabase_store.py`, `fake_store.py`, and all new/updated tests, atomic (green).
> 2. `docs(#598): document the create_signed_url trust boundary` — `storage_backend.py` docstring.

## 1. RED — the scoping guard does not exist yet

- [ ] 1.1 In `bloommcp/tests/result_store/test_store_parity.py` (or a new
      `test_artifacts.py` alongside `_artifacts.py`'s existing unit coverage — check which file
      currently unit-tests `hash_outputs`/`build_output_links` directly and add there for
      locality), add: `build_output_links(output_keys, output_sha256, output_size_bytes, url_for,
      expected_prefix="some/prefix/")` — call with the new keyword argument. Confirm RED
      (`TypeError: build_output_links() got an unexpected keyword argument 'expected_prefix'`).
- [ ] 1.2 Same test file: a key in `output_keys` that does NOT start with `expected_prefix` →
      the call raises `RuntimeError` naming the offending key and the expected prefix, and
      `url_for` is never invoked for that key (spy/mock `url_for`, assert it's called zero times
      when the *first* key is already out of scope — the guard must fire before any signing call,
      not partway through). Confirm RED (no such parameter exists to test against yet).
- [ ] 1.3 A key that DOES start with `expected_prefix` → `url_for` is called normally and the
      resulting `OutputLink` is built exactly as before (no behavior change for in-scope keys).
      Confirm RED (same reason).
- [ ] 1.4 In `bloommcp/tests/result_store/test_supabase_result_store.py`, add an end-to-end test:
      inject a mismatched key via the smallest possible seam — e.g. monkeypatch `AnalysisDir.key`
      (or the `key_for` closure's dependency) so exactly one output's computed key is corrupted
      relative to what was actually uploaded — and assert `commit()` raises `CommitFailedError`
      (via the existing broad `except Exception` path), best-effort cleans up the uploaded
      objects (mirror the assertions in the existing
      `test_signing_failure_fails_commit_and_cleans_up_orphans`), and records no new version.
      Confirm RED (no guard exists yet to trigger this failure — the corrupted key would currently
      just get signed).
- [ ] 1.5 Same pattern in `bloommcp/tests/result_store/test_fake_result_store.py` for
      `FakeResultStore.commit()` — fake/real parity for this guarantee. Confirm RED.
- [ ] 1.6 Confirm the full existing suite for all 8 consumer tools
      (`test_qc_clean_tool.py`, `test_qc_inspect_tool.py`, `test_pca_analysis_tool.py`,
      `test_remove_outliers_tool.py`, `test_descriptive_stats_tool.py`,
      `test_cross_experiment_correlations_tool.py`, `test_umap_analysis_tool.py`,
      `test_clustering_tool.py`) and `test_supabase_result_store.py` /
      `test_fake_result_store.py` / `test_store_parity.py` are green *before* touching
      `_artifacts.py` — the baseline the "no behavior change" acceptance criterion is measured
      against.

## 2. GREEN — implement the guard

- [ ] 2.1 `result_store/_artifacts.py`: add `expected_prefix: str` as a required keyword-only
      parameter to `build_output_links`. Before building each `OutputLink` (i.e. before calling
      `url_for(key)`), check `key.startswith(expected_prefix)`; on the first violation raise
      `RuntimeError(f"output key {key!r} is outside the expected run prefix {expected_prefix!r}")`
      — matching this file's existing "structural invariant violated" idiom (the same class of
      check `supabase_store.py` already uses bare `RuntimeError` for). Do not add a new
      `ResultStoreError` subclass — this is caught immediately by the callers' existing broad
      `except Exception` and remapped to `CommitFailedError`.
- [ ] 2.2 `result_store/supabase_store.py`: at the `build_output_links(...)` call site
      (`commit()`, currently line ~255), add `expected_prefix=adir.key(f"{version_dir}/")` —
      reusing `adir.key(...)`, the same method `key_for` itself calls, so the expected prefix and
      the actual keys can never independently drift.
- [ ] 2.3 `result_store/fake_store.py`: at the equivalent `build_output_links(...)` call site in
      `commit()`, add `expected_prefix=f"{state.prefix}{version_dir}/"` — mirroring its own
      `key_for`'s construction exactly.
- [ ] 2.4 Run §1's suite; debug to GREEN. Confirm the "every real call site's keys satisfy the
      scoping check" scenario holds by re-running the full 8-tool + result_store suite from 1.6
      unmodified and green.

## 3. Docs

- [ ] 3.1 `storage_backend.py`: add a short docstring note on `StorageBackend.create_signed_url`
      (the Protocol method) and both implementations stating plainly that this primitive performs
      no ownership/scope check and that `ResultStore.commit()` is responsible for restricting
      `key` to its own authorized scope before calling it — matching the spec delta's
      "Trust boundary" paragraph so the code and the spec say the same thing.

## 4. Refactor & verify

- [ ] 4.1 Refactor for clarity; confirm `create_signed_url`'s signature, both backend
      implementations' behavior, and every existing `output_links`/signing test are unchanged
      except for the new scoping-violation ones added in §1.
- [ ] 4.2 `/pre-merge`: lint (`black --check` + `ruff check`, pinned versions) + the exact CI
      suite command `cd bloommcp && uv run --frozen --extra test pytest tests/ -m "not
      integration and not live_smoke"` + `uv run --frozen` import (server boots) +
      `python scripts/check-uv-locks.py` (no drift — no new dependency) +
      `openspec validate add-bloommcp-signed-url-key-scoping --strict`.
- [ ] 4.3 Confirm no behavior change end-to-end: run the full suite once before any code change
      (baseline, §1.6) and once after (§2.4) and diff the pass/fail set — it SHALL be identical
      except for the new tests added by this change.
